"""
stapel-runtime-image — the shared runtime a generated project's images are
built FROM, and the gates that keep the project layer thin.

A scaffolded backend used to start from ``python:3.12-slim`` and resolve the
whole stapel closure (~1.2 GB) into its own image, so every preview stored
its own copy. Under ``--image-base fleet`` it starts from
``stapel-images/stapel-runtime``, which already carries the catalog's pinned
closure, and the frontend builds on ``stapel-images/node-build`` and ships an
alpine stage holding only ``dist/``. The closure then exists once per host.

Subcommands
-----------
emit         Write the runtime closure: ``requirements-runtime.txt`` (the
             union of every composite's generated requirements, stapel
             libraries pinned ``==`` to the catalog) and
             ``composites/<name>.txt`` (each composite's requirements exactly
             as the scaffold writes them). stapel-images builds the runtime
             from this output.
check        Offline subset check: are the given requirements satisfied by a
             runtime manifest (``/etc/stapel-runtime/manifest.json``)?
dry-run      ``pip install --dry-run`` of a requirements file in the current
             interpreter; exit 1 when anything would be installed.
thin-check   Build a composite's preview images with docker and grade them:
             backend unique size, dry-run inside the runtime, no
             ``node_modules`` in the frontend export image. Runs on CI or a
             stand's runner, never on a laptop.

Exit codes: 0 pass, 1 gate failed, 2 usage error.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, Optional

#: Where the fleet images live.
FLEET_REGISTRY = "registry.gitlab.com/stapel-studio/stapel-images"

#: One stapel-images pipeline tag. stapel-runtime and node-build of that
#: pipeline carry it together: one base date per host.
FLEET_IMAGE_TAG: Optional[str] = None

#: Overrides FLEET_IMAGE_TAG (CI builds against its own pipeline's tag).
FLEET_TAG_ENV = "STAPEL_FLEET_IMAGE_TAG"

#: The frontend export stage: a pinned static base, no toolchain.
STATIC_EXPORT_BASE = "alpine:3.24.3"

#: ``upstream`` keeps the public python/node images (OSS examples that cannot
#: pull the private registry); ``fleet`` builds on stapel-images.
IMAGE_BASES = ("upstream", "fleet")
DEFAULT_IMAGE_BASE = "upstream"

#: Unique size budget of a composite's backend image over the runtime.
BACKEND_UNIQUE_BUDGET_MB = 300

_DATED_TAG_RE = re.compile(r"^\d{8}-[0-9a-f]{7,40}$")


class RuntimeImageError(ValueError):
    pass


def resolve_image_base(value: Optional[str]) -> str:
    base = (value or DEFAULT_IMAGE_BASE).strip()
    if base not in IMAGE_BASES:
        raise RuntimeImageError(
            f"unknown image base {base!r}; expected one of {', '.join(IMAGE_BASES)}")
    return base


def resolve_fleet_tag(tag: Optional[str] = None) -> str:
    """The dated stapel-images tag: argument, else ``$STAPEL_FLEET_IMAGE_TAG``,
    else :data:`FLEET_IMAGE_TAG`. A moving tag is refused."""
    value = (tag or os.environ.get(FLEET_TAG_ENV) or FLEET_IMAGE_TAG or "").strip()
    if not value:
        raise RuntimeImageError(
            "no stapel-images tag known for --image-base fleet; pass --fleet-tag "
            f"or set ${FLEET_TAG_ENV}")
    if not _DATED_TAG_RE.match(value):
        raise RuntimeImageError(
            f"fleet tag {value!r} is not a dated <YYYYMMDD>-<sha> tag")
    return value


def fleet_ref(name: str, tag: str) -> str:
    return f"{FLEET_REGISTRY}/{name}:{tag}"


# ---------------------------------------------------------------------------
# Dockerfile templates (fleet)
# ---------------------------------------------------------------------------

BACKEND_DOCKERFILE = """\
# stapel-runtime carries the catalog's pinned stapel closure
# (/etc/stapel-runtime/manifest.json): the pip step is a no-op when the pins
# below match it and a thin delta otherwise.
FROM {{RUNTIME}}
WORKDIR /app
COPY {{DIR}}/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# The bootstrap step runner (require/optional/bootstrap_done) — bootstrap.sh
# sources it from here.
COPY scripts/bootstrap_lib.sh /usr/local/lib/stapel-bootstrap.sh
{{CORE}}COPY --chown=10001:10001 {{DIR}} .
ENV DJANGO_SETTINGS_MODULE=config.settings.prod
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
"""

FRONTEND_DOCKERFILE = """\
FROM {{NODE_BUILD}} AS build
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
COPY --chown=node:node package.json pnpm-workspace.yaml pnpm-lock.yaml* ./
RUN if [ -f pnpm-lock.yaml ]; then pnpm install --frozen-lockfile; else pnpm install; fi
COPY --chown=node:node . .
RUN pnpm build

# Prod canon (§57): the one-shot frontend-build service publishes dist/ into
# the frontend-dist volume nginx serves. Only dist/ and the publish step ship:
# no node, no node_modules.
FROM {{STATIC}} AS export
WORKDIR /app
COPY --from=build /app/dist ./dist
COPY frontend-publish.sh /usr/local/bin/frontend-publish
RUN chmod +x /usr/local/bin/frontend-publish
CMD ["/usr/local/bin/frontend-publish"]
"""

_VENDORED_CORE_COPY = (
    "# stapel_core is vendored as a git submodule at the project root\n"
    "COPY stapel_core ./stapel_core\n"
)
_PIP_CORE_NOTE = (
    "# Stapel libraries, stapel_core included, install from PyPI\n"
    "# (requirements.txt); no vendored checkout.\n"
)
_REQ_COPY_RE = re.compile(r"^COPY\s+(?P<dir>[^\s/]+)/requirements\.txt\s+\.\s*$", re.M)


def render_backend(service_dir: str, tag: str, vendored_core: bool) -> str:
    core = _VENDORED_CORE_COPY if vendored_core else _PIP_CORE_NOTE
    return (BACKEND_DOCKERFILE
            .replace("{{RUNTIME}}", fleet_ref("stapel-runtime", tag))
            .replace("{{DIR}}", service_dir)
            .replace("{{CORE}}", core))


def render_frontend(tag: str) -> str:
    return (FRONTEND_DOCKERFILE
            .replace("{{NODE_BUILD}}", fleet_ref("node-build", tag))
            .replace("{{STATIC}}", STATIC_EXPORT_BASE))


def _is_generated_backend(text: str) -> bool:
    return (text.startswith("FROM python:")
            and "ENV DJANGO_SETTINGS_MODULE=config.settings.prod" in text
            and _REQ_COPY_RE.search(text) is not None)


def _is_generated_frontend(text: str) -> bool:
    return text.startswith("FROM node:") and "AS export" in text and "frontend-publish" in text


def apply_image_base(project_dir: Path, image_base: str, tag: Optional[str] = None) -> list[Path]:
    """Rewrite the generated Dockerfiles of ``project_dir`` for ``image_base``.

    ``upstream`` changes nothing. ``fleet`` replaces every generated backend
    Dockerfile with the stapel-runtime one and the frontend Dockerfile with
    the node-build + static export one, all on ONE dated tag. A Dockerfile the
    scaffold did not generate is left alone.
    """
    if resolve_image_base(image_base) == "upstream":
        return []
    tag = resolve_fleet_tag(tag)
    changed: list[Path] = []
    for path in sorted(project_dir.rglob("Dockerfile")):
        if "node_modules" in path.parts or ".git" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if _is_generated_backend(text):
            service_dir = _REQ_COPY_RE.search(text).group("dir")
            out = render_backend(service_dir, tag, _VENDORED_CORE_COPY in text)
        elif _is_generated_frontend(text):
            out = render_frontend(tag)
        else:
            continue
        path.write_text(out, encoding="utf-8")
        changed.append(path)
    return changed


# ---------------------------------------------------------------------------
# The runtime closure
# ---------------------------------------------------------------------------

_REQ_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\[(?P<extras>[^\]]*)\])?\s*(?P<spec>[^;#]*)")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_requirement(line: str) -> Optional[tuple[str, frozenset, str]]:
    """``stapel-cdn[images]>=0.30.0,<0.31`` -> ("stapel-cdn", {"images"},
    ">=0.30.0,<0.31"). Blank and comment lines -> None. Option lines raise:
    an editable or index line has no place in a runtime closure."""
    line = line.split("#", 1)[0].strip()
    if not line:
        return None
    if line.startswith("-"):
        raise RuntimeImageError(f"requirement option line is not installable from the index: {line!r}")
    match = _REQ_RE.match(line)
    if not match:
        raise RuntimeImageError(f"unparseable requirement: {line!r}")
    extras = frozenset(e.strip() for e in (match.group("extras") or "").split(",") if e.strip())
    return _norm(match.group("name")), extras, match.group("spec").strip().replace(" ", "")


def composite_names() -> list[str]:
    from .create_project import FRONTEND_COMPOSITES
    return sorted(FRONTEND_COMPOSITES)


def composite_modules(name: str) -> list[str]:
    from .create_project import FRONTEND_COMPOSITES
    preset = FRONTEND_COMPOSITES.get(name)
    if preset is None:
        raise RuntimeImageError(f"unknown composite {name!r}; known: {', '.join(composite_names())}")
    return sorted({preset["backend"], *preset["pairs"]})


def generate_composite(name: str, output_dir: Path, image_base: str = "upstream",
                       fleet_tag: Optional[str] = None) -> Path:
    """Scaffold the composite as Studio does (monolith, PyPI deps, no git)."""
    from .create_project import create_project
    project = f"thin-{name}"
    with contextlib.redirect_stdout(io.StringIO()):
        create_project(
            name=project, project_type="monolith", title=f"Thin {name}",
            url="https://shop.example", company_name="Acme",
            company_email="ops@shop.example", modules=composite_modules(name),
            output_dir=output_dir, use_submodules=False, init_git=False,
            image_base=image_base, fleet_tag=fleet_tag,
        )
    return output_dir / project


def service_requirements(project: Path) -> Path:
    found = sorted(project.glob("svc-*/requirements.txt"))
    if len(found) != 1:
        raise RuntimeImageError(f"expected one svc-*/requirements.txt under {project}, found {len(found)}")
    return found[0]


def composite_requirements(name: str) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="stapel-closure-") as tmp:
        project = generate_composite(name, Path(tmp))
        lines = service_requirements(project).read_text(encoding="utf-8").splitlines()
    out = []
    for raw in lines:
        if parse_requirement(raw) is not None:
            out.append(raw.split("#", 1)[0].strip())
    return out


def _lower_bound(spec: str) -> Optional[str]:
    match = re.search(r">=\s*([0-9][0-9A-Za-z.]*)", spec)
    return match.group(1) if match else None


def runtime_closure(requirements: dict[str, list[str]]) -> list[str]:
    """The union of every composite's requirements. A stapel library is
    pinned ``==`` to its catalog pin (the ``>=`` bound the scaffold writes);
    its extras are merged. Everything else keeps its specifier."""
    stapel: dict[str, tuple[set, str]] = {}
    other: dict[str, tuple[set, set]] = {}
    for lines in requirements.values():
        for raw in lines:
            parsed = parse_requirement(raw)
            if parsed is None:
                continue
            name, extras, spec = parsed
            if name.startswith("stapel-"):
                pin = _lower_bound(spec)
                if pin is None:
                    raise RuntimeImageError(f"stapel requirement without a catalog pin: {raw!r}")
                prev = stapel.get(name)
                if prev and prev[1] != pin:
                    raise RuntimeImageError(f"{name}: composites disagree on the pin ({prev[1]} vs {pin})")
                stapel[name] = ((prev[0] if prev else set()) | set(extras), pin)
            else:
                prev_x, prev_s = other.get(name, (set(), set()))
                other[name] = (prev_x | set(extras), prev_s | ({spec} if spec else set()))
    out = []
    for name in sorted(stapel):
        extras, pin = stapel[name]
        suffix = f"[{','.join(sorted(extras))}]" if extras else ""
        out.append(f"{name}{suffix}=={pin}")
    for name in sorted(other):
        extras, specs = other[name]
        suffix = f"[{','.join(sorted(extras))}]" if extras else ""
        out.append(f"{name}{suffix}{','.join(sorted(specs))}")
    return out


def _tools_version() -> str:
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version("stapel-tools")
    except PackageNotFoundError:  # pragma: no cover - source checkout
        return "unknown"


def emit(out_dir: Path) -> dict:
    tools_version = _tools_version()
    names = composite_names()
    reqs = {name: composite_requirements(name) for name in names}
    closure = runtime_closure(reqs)
    (out_dir / "composites").mkdir(parents=True, exist_ok=True)
    for name, lines in reqs.items():
        (out_dir / "composites" / f"{name}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    header = (f"# stapel-runtime closure, emitted by stapel-tools {tools_version} "
              f"(stapel-runtime-image emit) from composites: {', '.join(names)}\n")
    (out_dir / "requirements-runtime.txt").write_text(header + "\n".join(closure) + "\n", encoding="utf-8")
    info = {"stapel_tools": tools_version, "composites": names, "closure": closure}
    (out_dir / "closure.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return info


# ---------------------------------------------------------------------------
# Subset gates
# ---------------------------------------------------------------------------


def _version_key(version: str) -> tuple:
    parts = []
    for piece in re.split(r"[.+-]", version):
        if piece.isdigit():
            parts.append((0, int(piece)))
        else:
            match = re.match(r"(\d+)(.*)", piece)
            if match:
                parts.append((0, int(match.group(1))))
                parts.append((-1, match.group(2)))
            else:
                parts.append((-1, piece))
    while parts and parts[-1] == (0, 0):
        parts.pop()
    return tuple(parts)


def satisfies(version: str, spec: str) -> bool:
    """A small PEP 440 subset: ``==``, ``!=``, ``>=``, ``<=``, ``>``, ``<``,
    ``~=`` on release numbers. ``==X.*`` compares the given prefix."""
    have = _version_key(version)
    for clause in filter(None, (c.strip() for c in spec.split(","))):
        match = re.match(r"^(===|==|!=|~=|>=|<=|>|<)\s*(.+)$", clause)
        if not match:
            return False
        op, raw = match.groups()
        if raw.endswith(".*"):
            prefix = _version_key(raw[:-2])
            hit = have[:len(prefix)] == prefix
            if (op == "==" and not hit) or (op == "!=" and hit):
                return False
            continue
        want = _version_key(raw)
        if op in ("==", "===") and have != want:
            return False
        if op == "!=" and have == want:
            return False
        if op == ">=" and have < want:
            return False
        if op == "<=" and have > want:
            return False
        if op == ">" and have <= want:
            return False
        if op == "<" and have >= want:
            return False
        if op == "~=":
            release = raw.split(".")
            upper = _version_key(".".join(release[:-1])) if len(release) > 1 else want
            if have < want or have[:len(upper)] != upper:
                return False
    return True


def check_against_manifest(lines: Iterable[str], manifest: dict) -> list[str]:
    """Requirement lines the manifest does NOT satisfy, each with the reason.
    Extras are not resolved here (the dry-run inside the image does that)."""
    installed = {_norm(k): v for k, v in (manifest.get("distributions") or {}).items()}
    problems = []
    for raw in lines:
        parsed = parse_requirement(raw)
        if parsed is None:
            continue
        name, _extras, spec = parsed
        have = installed.get(name)
        if have is None:
            problems.append(f"{raw.strip()}: not in the runtime")
        elif spec and not satisfies(have, spec):
            problems.append(f"{raw.strip()}: runtime has {name} {have}")
    return problems


def pip_dry_run(requirements: Path, python: str = sys.executable) -> list[str]:
    """``name==version`` of everything ``pip install --dry-run`` would install."""
    proc = subprocess.run(
        [python, "-m", "pip", "install", "--dry-run", "--quiet", "--disable-pip-version-check",
         "--report", "-", "-r", str(requirements)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeImageError(f"pip dry-run failed: {proc.stderr.strip()[-2000:]}")
    return dry_run_installs(proc.stdout)


def dry_run_installs(report_json: str) -> list[str]:
    report = json.loads(report_json or "{}")
    out = []
    for item in report.get("install") or []:
        meta = item.get("metadata") or {}
        out.append(f"{meta.get('name')}=={meta.get('version')}")
    return sorted(out)


# ---------------------------------------------------------------------------
# thin-check (docker; CI / stand runner only)
# ---------------------------------------------------------------------------


def _docker(*args: str, input_text: Optional[str] = None, check: bool = True) -> str:
    proc = subprocess.run(["docker", *args], input=input_text, capture_output=True, text=True)
    if check and proc.returncode != 0:
        tail = (proc.stdout + proc.stderr)[-4000:]
        raise RuntimeImageError(f"docker {' '.join(args[:2])} failed:\n{tail}")
    return proc.stdout


def _image_size(ref: str) -> int:
    return int(_docker("image", "inspect", "--format", "{{.Size}}", ref).strip())


def thin_check(composite: str, image_base: str = "fleet", fleet_tag: Optional[str] = None,
               workdir: Optional[Path] = None) -> dict:
    """Scaffold ``composite``, build its backend and frontend export images,
    and grade them. ``upstream`` only proves the images still build."""
    if shutil.which("docker") is None:
        raise RuntimeImageError("thin-check needs docker (CI or a stand's runner)")
    image_base = resolve_image_base(image_base)
    tag = resolve_fleet_tag(fleet_tag) if image_base == "fleet" else None
    report: dict = {"composite": composite, "image_base": image_base, "fleet_tag": tag, "failures": []}
    with tempfile.TemporaryDirectory(prefix="stapel-thin-", dir=workdir) as tmp:
        project = generate_composite(composite, Path(tmp), image_base=image_base, fleet_tag=tag)
        reqs = service_requirements(project)
        svc = reqs.parent.name
        backend = f"stapel-thin/{composite}-{image_base}-backend:check"
        frontend = f"stapel-thin/{composite}-{image_base}-frontend:check"
        _docker("build", "-f", f"{project}/{svc}/Dockerfile", "-t", backend, str(project))
        _docker("build", "--target", "export", "-t", frontend, str(project / "frontend"))
        report["backend_size_mb"] = round(_image_size(backend) / 1e6, 1)
        report["frontend_export_size_mb"] = round(_image_size(frontend) / 1e6, 1)
        if image_base == "fleet":
            runtime = fleet_ref("stapel-runtime", tag)
            # BuildKit keeps the base it pulled in its own cache, not in the
            # image store, so the size comparison needs an explicit pull.
            _docker("pull", runtime)
            runtime_size = _image_size(runtime)
            unique = (_image_size(backend) - runtime_size) / 1e6
            report["runtime_size_mb"] = round(runtime_size / 1e6, 1)
            report["backend_unique_mb"] = round(unique, 1)
            if unique > BACKEND_UNIQUE_BUDGET_MB:
                report["failures"].append(
                    f"backend unique layers {unique:.1f} MB > {BACKEND_UNIQUE_BUDGET_MB} MB")
            out = _docker(
                "run", "--rm", "-i", "--entrypoint", "sh", runtime, "-c",
                "cat > /tmp/r.txt && pip install --dry-run --quiet --disable-pip-version-check "
                "--report - -r /tmp/r.txt",
                input_text=reqs.read_text(encoding="utf-8"),
            )
            installs = dry_run_installs(out)
            report["dry_run_installs"] = installs
            if installs:
                report["failures"].append(
                    f"pip --dry-run in the runtime would install {len(installs)}: {', '.join(installs)}")
            found = _docker("run", "--rm", "--entrypoint", "sh", frontend, "-c",
                            "find / -xdev -type d -name node_modules 2>/dev/null | head -5")
            report["frontend_node_modules"] = [p for p in found.splitlines() if p]
            if report["frontend_node_modules"]:
                report["failures"].append(
                    f"frontend export image ships node_modules: {report['frontend_node_modules']}")
    report["ok"] = not report["failures"]
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="stapel-runtime-image", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_emit = sub.add_parser("emit", help="write the runtime closure")
    p_emit.add_argument("--out", type=Path, required=True)
    p_check = sub.add_parser("check", help="requirements vs a runtime manifest, offline")
    p_check.add_argument("--manifest", type=Path, required=True)
    p_check.add_argument("requirements", type=Path, nargs="+")
    p_dry = sub.add_parser("dry-run", help="pip install --dry-run; exit 1 if anything installs")
    p_dry.add_argument("requirements", type=Path, nargs="+")
    p_thin = sub.add_parser("thin-check", help="build and grade a composite's preview images")
    p_thin.add_argument("--composite", action="append", required=True)
    p_thin.add_argument("--image-base", default="fleet", choices=IMAGE_BASES)
    p_thin.add_argument("--fleet-tag", default=None)
    p_thin.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.cmd == "emit":
            info = emit(args.out)
            print(f"runtime closure: {len(info['closure'])} requirements from "
                  f"{', '.join(info['composites'])} -> {args.out}")
            return 0
        if args.cmd == "check":
            manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
            failed = False
            for req in args.requirements:
                problems = check_against_manifest(_read_lines(req), manifest)
                for problem in problems:
                    print(f"{req}: {problem}")
                failed = failed or bool(problems)
                if not problems:
                    print(f"{req}: satisfied by the runtime")
            return 1 if failed else 0
        if args.cmd == "dry-run":
            failed = False
            for req in args.requirements:
                installs = pip_dry_run(req)
                print(f"{req}: {len(installs)} would be installed"
                      + (f": {', '.join(installs)}" if installs else ""))
                failed = failed or bool(installs)
            return 1 if failed else 0
        if args.cmd == "thin-check":
            reports = [thin_check(c, args.image_base, args.fleet_tag) for c in args.composite]
            if args.json:
                print(json.dumps(reports, indent=2))
            else:
                for r in reports:
                    print(json.dumps(r))
            return 0 if all(r["ok"] for r in reports) else 1
    except RuntimeImageError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
