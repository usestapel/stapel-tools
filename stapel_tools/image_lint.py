"""
stapel-image-lint — the "a service builds its own base layer, on the host that
serves it" gate, in the ``stapel-env-address-lint`` / ``stapel-config-lint``
idiom (rule codes, ``--json``, ``--strict``, exit 1 on any error).

Why this exists
----------------
Measured across the two client fleets on 2026-09-13: 17 service Dockerfiles,
and every one of them started from a bare upstream image (``python:3.12-slim``,
or a base image belonging to another organisation) and then installed the same
apt packages and resolved the same Python dependency closure — Django, DRF,
drf-spectacular, psycopg's libpq, confluent-kafka's librdkafka, cryptography —
seventeen separate times, on machines that were simultaneously answering
requests. One fleet's own build script records the unit cost in passing:
"three minutes into a pip layer".

``stapel-images`` (https://gitlab.com/stapel-studio/stapel-images) builds those
layers once, on CI, and publishes them to
``registry.gitlab.com/stapel-studio/stapel-images/<name>``. This linter is what
keeps a service from drifting back off them — and, more usefully, what catches
a service that IS on a stapel base but the WRONG one, which is a failure that
otherwise shows up as a missing ``ffmpeg`` in a worker at 3am rather than at
build time.

Rules
-----
IMG001  (warning, during the migration) A service Dockerfile's final-stage
        ``FROM`` is not a stapel base image. The fix is one line — see
        ``docs/reference/base-images.md``.

        WARNING AND NOT ERROR, deliberately and temporarily. On the day this
        rule shipped, all 17 fleet services tripped it, because the migration
        is planned and has not run (stapel-images/MIGRATION.md: a demo was
        running on one fleet and the other had just come back from an
        outage). A rule that turns both fleets' pre-commit red before the
        work it asks for is possible is a rule people learn to skim past,
        which is strictly worse than no rule — the same reasoning that keeps
        DOC001 at warning level while its sweep runs. It becomes an error
        when the fleets are migrated; that is one word in this file.

IMG002  (error) The service IS on a stapel base image, and that base cannot
        satisfy what the service declares it needs. This one is an error from
        the first day and always was safe to be: it cannot fire on a project
        that has not migrated, because it only looks at Dockerfiles already
        pointing at ``stapel-images``. Choosing ``python-base`` for a service
        whose requirements name ``stapel-recordings`` is a build that
        succeeds and a convert worker that fails on the first recording.

IMG003  (warning) A stapel base image referenced by its MOVING tag (the bare
        major, ``python-base:1``) rather than by an immutable
        ``<YYYYMMDD>-<short sha>`` tag or a ``@sha256:`` digest. The moving
        tag exists for ``--cache-from`` and for reading what "current" means;
        a service pinned to it is a service whose base can change between two
        builds of the same commit.

How a service's needs are worked out
-------------------------------------
Two ways, and the first wins:

1. **Declared.** A ``stapel-service.toml`` beside the Dockerfile (or at the
   service directory root) saying::

       [image]
       base = "media-base"

   This is the escape hatch and the documentation in one: a service whose need
   this linter's inference cannot see (a runtime that shells out to a binary
   named only in Python code, say) states it, and the statement is then what
   the rule checks against.

2. **Inferred** from the requirements file the Dockerfile installs. The
   inference is deliberately narrow — it maps only the markers that ARE the
   reason a heavier base exists, so that a wrong inference is a missing
   finding rather than a false one:

       media  stapel-recordings, stapel-cdn[...images...], pyvips,
              ffmpeg-python, pillow-heif
       ml     torch, torchvision, torchaudio, onnxruntime, pyannote.audio,
              transformers, sentence-transformers

   A requirement naming none of those needs ``python-base``.

The bases form a chain — ``ml-base`` is built FROM ``media-base``, which is
built FROM ``python-base`` — so a service needing ``media`` is satisfied by
``media-base`` OR ``ml-base``. That ordering is the whole of IMG002's
comparison; it is stated once, in :data:`BASE_RANK`.

Scope and exclusions
---------------------
* Only the **final** stage's ``FROM`` is graded. A multi-stage builder that
  starts from ``python:3.12-slim`` to compile a wheel is the supported
  pattern, not a violation — what ships is the last stage.
* A ``FROM`` naming an earlier stage in the same file (``FROM builder``) is
  resolved back to that stage's own base.
* ``ARG`` defaults are substituted, because that is how every base reference
  in the estate is actually written (``ARG PYTHON_BASE=...`` / ``FROM
  ${PYTHON_BASE}``). An ``ARG`` with no default resolves to nothing and the
  ``FROM`` is reported as unresolvable rather than guessed at.
* Node/frontend Dockerfiles are not graded against the Python bases. A
  Dockerfile whose final stage is a node image is out of scope for IMG001/2;
  ``node-build`` exists but a storefront repo is not a service.
* ``scratch`` is never flagged: an image built from nothing is a deliberate
  statement, not a service that forgot.
* Suppress a deliberate exception with ``# noqa: IMG001`` on the ``FROM``
  line (the shared ``stapel_tools.escape`` grammar).

Exit codes: 0 clean (warnings allowed), 1 errors present, 2 usage errors.
``--strict`` promotes warnings to errors — which is how a fleet that HAS
migrated keeps IMG001 from regressing before the default flips.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.10 and below
    tomllib = None  # type: ignore[assignment]

from . import escape

#: The registry path prefix that makes an image one of ours. Bare
#: ``stapel-images/<name>`` is accepted too, for a fleet that mirrors the
#: images into its own registry — what the rule is about is which LAYER SET a
#: service inherits, not which host serves it.
REGISTRY_PATH = "stapel-images"

SKIP_DIRS = {
    "__pycache__",
    ".git",
    ".hg",
    ".tox",
    ".venv",
    "venv",
    "node_modules",
    "htmlcov",
    "build",
    "dist",
    ".claude",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "worktrees",
    "site-packages",
}

#: base name -> what it satisfies. The chain is the point: ml-base is built
#: FROM media-base, which is built FROM python-base, so a heavier base always
#: satisfies a lighter need. Rank, not a set, because that is what makes
#: "is this base enough" one comparison instead of a table.
BASE_RANK: dict[str, int] = {
    "python-base": 0,
    "media-base": 1,
    "ml-base": 2,
}

#: need -> the lightest base that satisfies it
NEED_RANK: dict[str, int] = {
    "python": 0,
    "media": 1,
    "ml": 2,
}

RANK_BASE: dict[int, str] = {v: k for k, v in BASE_RANK.items()}

#: Requirement markers that RAISE a service's need. Narrow on purpose: each
#: entry is a package that is the reason one of the heavier images exists.
#: Matched against the distribution name only (extras and version specifiers
#: stripped), except stapel-cdn, whose *extra* is what decides.
MEDIA_MARKERS = {
    "stapel-recordings",
    "pyvips",
    "ffmpeg-python",
    "pillow-heif",
}
ML_MARKERS = {
    "torch",
    "torchvision",
    "torchaudio",
    "onnxruntime",
    "onnxruntime-gpu",
    "pyannote.audio",
    "transformers",
    "sentence-transformers",
}

#: What makes a Dockerfile a PYTHON service's, and therefore in scope. Read
#: off what the file does, not off what it starts from: a blocklist of base
#: images ("not node, not nginx, not golang…") is a list that is wrong the
#: first time a fleet adds a runtime nobody thought of — this repo's own
#: `photon` geocoder is `eclipse-temurin:21-jre`, and a linter that offers it
#: python-base has discredited itself on the one line a reader will check.
_PYTHON_SERVICE_RE = re.compile(
    r"^\s*(?:RUN\s+.*\bpip\s+install\b"
    r"|COPY\s+.*requirements[\w.-]*\.txt"
    r"|CMD\s+.*\b(?:gunicorn|uvicorn|python|manage\.py)\b"
    r"|ENTRYPOINT\s+.*\b(?:gunicorn|uvicorn|python)\b"
    r"|ENV\s+DJANGO_SETTINGS_MODULE)",
    re.IGNORECASE | re.MULTILINE,
)


def is_python_service(text: str) -> bool:
    return bool(_PYTHON_SERVICE_RE.search(text))

_FROM_RE = re.compile(r"^\s*FROM\s+(?P<rest>.+?)\s*$", re.IGNORECASE)
_ARG_RE = re.compile(r"^\s*ARG\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:=(?P<value>.*))?\s*$",
                     re.IGNORECASE)
_VAR_RE = re.compile(r"\$\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)(?::?-[^}]*)?\}|\$(?P<bare>[A-Za-z_][A-Za-z0-9_]*)")
_COPY_REQ_RE = re.compile(r"^\s*COPY\s+.*?(?P<path>[\w./-]*requirements[\w.-]*\.txt)", re.IGNORECASE)


@dataclass
class Violation:
    path: str
    line: int
    rule: str
    message: str
    level: str = "error"

    def __str__(self) -> str:
        tag = self.rule if self.level == "error" else f"{self.rule} warning"
        return f"{self.path}:{self.line}: [{tag}] {self.message}"

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "line": self.line,
            "rule": self.rule,
            "message": self.message,
            "level": self.level,
        }


@dataclass
class Stage:
    """One ``FROM`` in a Dockerfile."""
    line: int
    #: the image reference after ARG substitution, or "" when unresolvable
    image: str
    #: ``AS <name>`` if given
    name: Optional[str]
    #: the raw source line, for noqa
    raw: str


# ---------------------------------------------------------------------------
# file discovery
# ---------------------------------------------------------------------------


def _walk_dockerfiles(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")
        )
        for fname in sorted(filenames):
            if fname == "Dockerfile" or fname.startswith("Dockerfile."):
                yield Path(dirpath) / fname


# ---------------------------------------------------------------------------
# Dockerfile parsing
# ---------------------------------------------------------------------------


def _substitute(value: str, args: dict) -> str:
    """Replace ``$VAR`` / ``${VAR}`` with the ARG default in scope.

    A variable with no default in scope substitutes to the empty string, which
    is what makes the resulting reference unresolvable — and unresolvable is
    reported, never guessed at. Guessing is how a linter comes to certify a
    base image nobody has.
    """
    def repl(match: "re.Match") -> str:
        name = match.group("braced") or match.group("bare")
        return args.get(name, "")
    return _VAR_RE.sub(repl, value)


def parse_stages(text: str) -> tuple[list[Stage], dict]:
    """Every ``FROM`` in order, with ARG defaults substituted, plus the ARG
    table itself."""
    args: dict = {}
    stages: list[Stage] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        arg = _ARG_RE.match(raw)
        if arg:
            value = arg.group("value")
            if value is not None:
                args[arg.group("name")] = _substitute(value.strip().strip('"\''), args)
            continue
        match = _FROM_RE.match(raw)
        if not match:
            continue
        rest = match.group("rest")
        # strip a trailing comment, then --platform=... flags
        rest = rest.split("#", 1)[0].strip()
        parts = [p for p in rest.split() if not p.startswith("--")]
        if not parts:
            continue
        image = _substitute(parts[0], args)
        name = None
        if len(parts) >= 3 and parts[1].upper() == "AS":
            name = parts[2]
        stages.append(Stage(line=lineno, image=image, name=name, raw=raw))
    return stages, args


def resolve_final_base(stages: list[Stage]) -> Optional[Stage]:
    """The stage that decides what ships, with ``FROM <earlier stage>``
    followed back to the image it ultimately rests on.

    Returns the STAGE whose ``image`` is a real image reference (so the
    reported line is the one a reader has to edit), or None for an empty file.
    """
    if not stages:
        return None
    by_name = {s.name: s for s in stages if s.name}
    current = stages[-1]
    seen: set = set()
    while current.image in by_name and id(current) not in seen:
        seen.add(id(current))
        current = by_name[current.image]
    return current


# ---------------------------------------------------------------------------
# what the image reference IS
# ---------------------------------------------------------------------------


def stapel_base_name(image: str) -> Optional[str]:
    """``registry.gitlab.com/stapel-studio/stapel-images/media-base:tag`` ->
    ``"media-base"``; anything that is not one of ours -> None."""
    if not image:
        return None
    ref = image.split("@", 1)[0]
    # strip the tag, but only from the last path segment (a registry host may
    # carry a :port)
    head, _, last = ref.rpartition("/")
    last = last.split(":", 1)[0]
    if not head:
        return None
    if REGISTRY_PATH not in head.split("/"):
        return None
    return last if last in BASE_RANK else None


def is_immutable_reference(image: str) -> bool:
    """A digest, or a dated ``<YYYYMMDD>-<sha>`` tag. The bare major is not."""
    if "@sha256:" in image:
        return True
    _, _, last = image.rpartition("/")
    _, sep, tag = last.partition(":")
    if not sep or not tag:
        return False
    return bool(re.fullmatch(r"\d{8}-[0-9a-f]{7,40}", tag))


# ---------------------------------------------------------------------------
# what the service NEEDS
# ---------------------------------------------------------------------------


def _requirement_names(text: str) -> list[str]:
    """Distribution names (lowercased, extras kept alongside) from a
    requirements file. Comment lines, ``-r``/``--index-url`` lines and blank
    lines are not requirements."""
    names: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        names.append(line.lower())
    return names


def _need_from_requirements(text: str) -> str:
    need = "python"
    for entry in _requirement_names(text):
        base = re.split(r"[<>=!~;\s\[]", entry, maxsplit=1)[0]
        extras = ""
        bracket = re.search(r"\[([^\]]*)\]", entry)
        if bracket:
            extras = bracket.group(1)
        if base in ML_MARKERS:
            return "ml"
        if base in MEDIA_MARKERS:
            need = "media"
        # stapel-cdn is only a media consumer when it selects the image path;
        # a files-only CDN deployment genuinely does not need libvips.
        if base == "stapel-cdn" and "images" in extras:
            need = "media"
        # stapel-video's processing path shells out to ffmpeg.
        if base == "stapel-video":
            need = "media"
    return need


def _read_toml(path: Path) -> dict:
    if tomllib is None:  # pragma: no cover - 3.10 and below
        return {}
    # TOMLDecodeError subclasses ValueError. A malformed manifest reads as
    # "not declared" and the inference takes over — it must not crash the gate.
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}


def declared_base(dockerfile: Path, service_root: Optional[Path] = None) -> Optional[str]:
    """``[image] base`` from a ``stapel-service.toml`` beside the Dockerfile or
    at the service root, or None."""
    candidates = [dockerfile.parent / "stapel-service.toml"]
    if service_root is not None:
        candidates.append(service_root / "stapel-service.toml")
    for candidate in candidates:
        if not candidate.is_file():
            continue
        data = _read_toml(candidate)
        base = (data.get("image") or {}).get("base")
        if isinstance(base, str) and base in BASE_RANK:
            return base
    return None


def _requirements_for(dockerfile: Path, text: str) -> list[Path]:
    """The requirements files this Dockerfile actually installs.

    Read off its own ``COPY ... requirements.txt`` lines, because a fleet
    Dockerfile's build context is the REPO ROOT and the requirements file it
    names lives in the service subdirectory (``COPY svc-cdn/requirements.txt
    .``) — resolving "the requirements.txt next to the Dockerfile" would find
    the right file in one fleet's layout and nothing in the other's. Both
    interpretations are tried and whatever exists is read.
    """
    found: list[Path] = []
    roots = [dockerfile.parent, dockerfile.parent.parent]
    for raw in text.splitlines():
        match = _COPY_REQ_RE.match(raw)
        if not match:
            continue
        rel = match.group("path")
        for root in roots:
            candidate = (root / rel).resolve()
            if candidate.is_file() and candidate not in found:
                found.append(candidate)
    default = dockerfile.parent / "requirements.txt"
    if not found and default.is_file():
        found.append(default.resolve())
    return found


def service_need(dockerfile: Path, text: str) -> tuple[str, str]:
    """(need, how it was determined)."""
    declared = declared_base(dockerfile)
    if declared:
        return (
            {v: k for k, v in NEED_RANK.items()}[BASE_RANK[declared]],
            f"declared in stapel-service.toml ([image] base = \"{declared}\")",
        )
    need = "python"
    sources: list[str] = []
    for req in _requirements_for(dockerfile, text):
        try:
            found = _need_from_requirements(req.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        sources.append(req.name)
        if NEED_RANK[found] > NEED_RANK[need]:
            need = found
    if not sources:
        return need, "no requirements file found — assuming the lightest base"
    return need, f"inferred from {', '.join(sorted(set(sources)))}"


# ---------------------------------------------------------------------------
# lint driver
# ---------------------------------------------------------------------------


def lint_dockerfile(path: Path) -> list:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    if not is_python_service(text):
        return []

    stages, _args = parse_stages(text)
    final = resolve_final_base(stages)
    if final is None:
        return []

    suppressed = escape.parse_noqa(final.raw)

    def muted(rule: str) -> bool:
        return suppressed is not None and (not suppressed or rule in suppressed)

    image = final.image
    if image.lower() == "scratch":
        return []

    violations: list = []
    base = stapel_base_name(image)

    if base is None:
        if muted("IMG001"):
            return []
        if not image:
            message = (
                "the final FROM resolves to nothing — a build ARG with no "
                "default. Give it a stapel base image as its default: see "
                "docs/reference/base-images.md"
            )
        else:
            need, how = service_need(path, text)
            want = RANK_BASE[NEED_RANK[need]]
            message = (
                f"builds its base layer itself (FROM {image}) instead of "
                f"inheriting one. This service wants "
                f"registry.gitlab.com/stapel-studio/stapel-images/{want} "
                f"({how}) — see docs/reference/base-images.md for the table "
                f"and what each image contains"
            )
        violations.append(Violation(str(path), final.line, "IMG001", message, level="warning"))
        return violations

    # On a stapel base. Now the two questions that only make sense here.
    need, how = service_need(path, text)
    if BASE_RANK[base] < NEED_RANK[need] and not muted("IMG002"):
        want = RANK_BASE[NEED_RANK[need]]
        violations.append(Violation(
            str(path), final.line, "IMG002",
            f"FROM {base}, but this service needs {want}: {how}. "
            f"{base} does not carry what that requires, so the image builds "
            f"and the capability is missing at run time — see "
            f"docs/reference/base-images.md",
        ))

    if not is_immutable_reference(image) and not muted("IMG003"):
        violations.append(Violation(
            str(path), final.line, "IMG003",
            f"pins {base} by a moving tag. Use an immutable "
            f"<YYYYMMDD>-<short sha> tag or a @sha256: digest, so two builds "
            f"of this commit cannot rest on two different base images; the "
            f"moving tag is for --cache-from and for reading what 'current' "
            f"means. Newest tags: stapel-images/VERSIONS.md",
            level="warning",
        ))

    return violations


def lint_project(project: Path, notes: Optional[list] = None) -> list:
    violations: list = []
    count = 0
    for dockerfile in _walk_dockerfiles(project.resolve()):
        count += 1
        violations.extend(lint_dockerfile(dockerfile))
    if notes is not None and count == 0:
        notes.append("no Dockerfile in this tree — image rules are silent")
    violations.sort(key=lambda v: (v.path, v.line, v.rule))
    return violations


def lint_paths(paths: Iterable) -> list:
    violations: list = []
    for raw in paths:
        root = Path(raw)
        if not root.exists():
            raise SystemExit(f"Error: path does not exist: {root}")
        violations.extend(lint_project(root))
    violations.sort(key=lambda v: (v.path, v.line, v.rule))
    return violations


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-image-lint",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "paths", nargs="*", default=["."],
        help="Project roots or fleet repos to lint (default: .)",
    )
    parser.add_argument("--json", action="store_true", help="Machine output")
    parser.add_argument(
        "--strict", action="store_true",
        help="Promote warnings to errors — for a fleet that has already "
             "migrated and wants IMG001 to stay closed.",
    )
    args = parser.parse_args(argv)

    violations = lint_paths(args.paths)
    if args.strict:
        for violation in violations:
            violation.level = "error"
    errors = [v for v in violations if v.level == "error"]

    if args.json:
        print(json.dumps(
            {
                "ok": not errors,
                "errors": len(errors),
                "violations": [v.to_dict() for v in violations],
            },
            indent=2, sort_keys=True, ensure_ascii=False,
        ))
    else:
        for violation in violations:
            print(violation)
        if violations:
            print(f"\n{len(errors)} error(s), "
                  f"{len(violations) - len(errors)} warning(s) found.")
        else:
            print("No violations found.")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
