#!/usr/bin/env python3
"""Golden whole-tree hashes of what the scaffold generates.

The emitter refactor (platform-neutral composition, React as the first of
several emitters) moves every planner and template table out from under the
code that renders them. Each step must leave the generated projects
byte-identical, and "the tests still pass" does not say that: the suite
asserts properties of the output, not the output. This gate does.

For every case of a matrix of compositions it runs the real CLI in-process
(``stapel-create-project`` / ``stapel-frontend-repo-init``) into a temporary
directory and records ``{relative path: sha256}`` of every generated FRONTEND
file: the whole ``frontend/`` tree of a project, the whole repository of a
``stapel-frontend-repo-init`` case, and for a ``mobile-*`` case the KMP
emitter's tree (``mobile/`` plus ``service-configs/nginx/``, where it serves
the deep-link association files). Mobile cases pass ``--mobile-maven-repo
local``, never a path, so no machine's directory reaches a hash. The three generated secrets come from a
deterministic source.

Why not the backend half: the scaffold reads every selected module's
``docs/capabilities.json`` and ``CONFIG.MD`` from a workspace sibling checkout
or the INSTALLED distribution, so generated settings move with whichever
sibling versions the machine has (CI installs four of them from their main
branches). Hashing them would make the gate a function of the environment;
the backend templates have their own content tests.

    python scripts/golden_scaffold.py            # compare (make golden)
    python scripts/golden_scaffold.py --update   # rewrite, print the diff (make golden-update)
    python scripts/golden_scaffold.py --case shop  # one case

A pin bump moves hashes on purpose (package.json, requirements.txt): run
``--update`` in the same commit and read the printed diff — it names every
file that changed, and nothing else may be in it.

What it cannot see: the backend half of a project (above), stdout notes, and
cases not in the matrix. Add a case before you refactor the code path it
would cover.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = ROOT / "tests" / "golden"

#: Environment the generators read; cleared so a developer's shell cannot
#: move a hash.
_ENV_KEYS = ("STAPEL_BASE_IMAGE", "STAPEL_IMAGE_BASE", "STAPEL_FLEET_TAG")

_COMMON = [
    "--title", "Golden App", "--url", "https://golden.example",
    "--company-name", "Acme", "--company-email", "hello@golden.example",
    "--no-git",
]


def _create(*flags: str, name: str = "golden", project_type: str = "monolith"):
    return ("create", [name, "--type", project_type, *_COMMON, *flags])


def _mobile(*flags: str, project_type: str = "monolith"):
    """A case whose manifest is the KMP emitter's tree (mobile/ + what it adds to nginx)."""
    return ("create-mobile", ["golden", "--type", project_type, *_COMMON, *flags,
                              "--mobile", "kmp", "--mobile-maven-repo", "local"])


def _repo_init(*flags: str):
    return ("repo-init", list(flags))


def _released_pairs() -> list[str]:
    from stapel_tools.create_project import FRONTEND_REACT_LIBS, STAPEL_LIBS, unreleased

    keys = [k for k in FRONTEND_REACT_LIBS if k in STAPEL_LIBS]
    blocked = set(unreleased(keys))
    return [k for k in keys if k not in blocked
            and FRONTEND_REACT_LIBS[k].get("released", True)]


def cases() -> dict[str, tuple[str, list[str]]]:
    """name -> (tool, argv). Order is the order they run and print in."""
    from stapel_tools._shell_templates import MONOLITH_RENDERABLE
    from stapel_tools._theme_presets import THEME_PRESETS

    all_pairs = _released_pairs()
    out: dict[str, tuple[str, list[str]]] = {
        "no-modules": _create("--modules", "core"),
        "auth": _create("--modules", "auth"),
        "auth-no-auth": _create("--modules", "auth", "--no-auth"),
        "landing-no-modules": _create("--modules", "core", "--landing"),
        "auth-landing": _create("--modules", "auth", "--landing"),
        "shop": _create("--modules", "shop"),
        "classified": _create("--modules", "classified"),
        "booking": _create("--modules", "booking"),
        "social": _create("--modules", "social"),
        "every-pair": _create("--modules", *all_pairs),
        "every-pair-landing": _create("--modules", *all_pairs, "--landing"),
        "auth-notifications-translate-workspaces": _create(
            "--modules", "auth", "notifications", "translate", "workspaces"),
        "profiles-cdn": _create("--modules", "auth", "profiles", "cdn"),
        "listings-cdn": _create("--modules", "auth", "listings", "cdn"),
        "slot-override": _create(
            "--modules", "auth", "notifications", "translate",
            "--slot", "topbar.end=notifications.bell,auth.account_menu"),
        "slot-none": _create(
            "--modules", "auth", "notifications", "--slot", "topbar.end=none"),
        "minimal-auth": _create("--modules", "auth", project_type="minimal"),
        "minimal-none": _create("--modules", "core", project_type="minimal"),
        "microservices": _create("--modules", "core", project_type="microservices"),
        "nats-broker": _create("--modules", "auth", "--broker", "nats"),
        "posture-live": _create("--modules", "auth", "--posture", "live"),
        "image-fleet": _create("--modules", "auth", "--image-base", "fleet"),
    }
    for model in ("none", "anonymous", "public", "internal"):
        out[f"auth-model-{model}"] = _create("--modules", "auth", "--auth-model", model)
    for shell in sorted(MONOLITH_RENDERABLE):
        out[f"shell-{shell}"] = _create(
            "--modules", "auth", "notifications", "workspaces", "--shell", shell)
    # The navigation presets each shape draws today, on a storefront and on an
    # app-like selection (navigation-templates spec §7.4).
    for preset in ("shop", "dashboard", "site"):
        out[f"nav-{preset}"] = _create("--modules", "shop", "--nav-preset", preset)
        out[f"nav-{preset}-app"] = _create(
            "--modules", "auth", "profiles", "notifications", "--nav-preset", preset)
    out["nav-explicit-drawer"] = _create(
        "--modules", "shop", "--nav", "compact=drawer", "--nav-hide", "admin.root",
        "--nav-order", "categories.catalog=1")
    out["nav-default-no-accounts"] = _create("--modules", "categories", "listings")
    for preset in THEME_PRESETS:
        out[f"theme-{preset}"] = _create("--modules", "auth", "--theme", preset)
    for preset in ("shop", "classified", "booking", "social"):
        out[f"repo-init-{preset}"] = _repo_init(
            "--surface", "public", "--preset", preset, "--name", "golden-web",
            "--title", "Golden Web")
    out["repo-init-pairs"] = _repo_init(
        "--surface", "public", "--pairs", "search,listings,categories",
        "--name", "golden-web", "--doc-type", "listing", "--locale", "ru")
    out["repo-init-delivery-github"] = _repo_init("--ci", "github")
    out["repo-init-shop-dashboard"] = _repo_init(
        "--surface", "public", "--preset", "shop", "--nav-preset", "dashboard",
        "--name", "golden-web", "--title", "Golden Web")
    # The KMP emitter: one mobile/ tree per composition, offline on and off.
    mobile = {
        "minimal-auth": _mobile("--modules", "auth", "--auth-model", "public",
                                "--auth-kinds", "phone,email,password", "--posture", "prototype",
                                project_type="minimal"),
        "minimal-profiles": _mobile("--modules", "auth", "profiles", "--auth-model", "public",
                                    "--auth-kinds", "phone,email,password", "--posture", "prototype",
                                    "--mobile-avatar-upload", project_type="minimal"),
        "shop": _mobile("--modules", "shop"),
        "classified": _mobile("--modules", "classified"),
        "booking": _mobile("--modules", "booking"),
        "social": _mobile("--modules", "social"),
    }
    for name, (tool, argv) in mobile.items():
        out[f"mobile-{name}"] = (tool, argv)
        out[f"mobile-{name}-offline"] = (tool, [*argv, "--mobile-offline"])
    return out


def _deterministic_secret() -> Callable[..., str]:
    counter = iter(range(1_000_000))

    def secret(length: int = 64) -> str:
        n = next(counter)
        seed = hashlib.sha256(f"golden-secret-{n}".encode()).hexdigest()
        return (seed * (length // len(seed) + 1))[:length]

    return secret


def tree_manifest(root: Path) -> dict[str, str]:
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def run_case(name: str, spec: tuple[str, list[str]]) -> dict[str, str]:
    """Generate one case into a temp dir; return its manifest."""
    from stapel_tools import create_project as cp
    from stapel_tools import frontend_repo_init, ui_wiring

    tool, argv = spec
    saved_env = {k: os.environ.pop(k) for k in _ENV_KEYS if k in os.environ}
    saved_secret = cp._random_secret
    saved_version = ui_wiring._version
    saved_argv = sys.argv
    cp._random_secret = _deterministic_secret()
    # the ui-wiring script's stamp names the INSTALLED stapel-tools version;
    # a release must not move every hash
    ui_wiring._version = lambda: "0.0.0-golden"
    try:
        with tempfile.TemporaryDirectory(prefix="golden-") as tmp:
            out_dir = Path(tmp)
            sink = io.StringIO()
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                if tool == "create-mobile":
                    sys.argv = ["stapel-create-project", *argv, "--output-dir", str(out_dir)]
                    try:
                        cp.main()
                    except SystemExit as exc:
                        if exc.code not in (None, 0):
                            raise RuntimeError(
                                f"case {name}: exit {exc.code}\n{sink.getvalue()}") from exc
                    project = out_dir / argv[0]
                    manifest = tree_manifest(project / "mobile")
                    manifest = {f"mobile/{k}": v for k, v in manifest.items()}
                    nginx = project / "service-configs" / "nginx"
                    if nginx.is_dir():
                        manifest.update({f"service-configs/nginx/{k}": v
                                         for k, v in tree_manifest(nginx).items()})
                    return manifest
                if tool == "create":
                    sys.argv = ["stapel-create-project", *argv, "--output-dir", str(out_dir)]
                    try:
                        cp.main()
                    except SystemExit as exc:
                        if exc.code not in (None, 0):
                            raise RuntimeError(
                                f"case {name}: exit {exc.code}\n{sink.getvalue()}") from exc
                    root = out_dir / argv[0] / "frontend"
                    if not root.is_dir():  # minimal / microservices: no frontend
                        root = out_dir / argv[0]
                        # PRESENTERS.MD exists only where the generated
                        # service's Django imports (environment, not code)
                        names = sorted(p.name for p in root.iterdir()
                                       if p.name != "PRESENTERS.MD")
                        return {"<no frontend/>": hashlib.sha256(
                            "\n".join(names).encode()).hexdigest()}
                else:
                    root = out_dir / "golden-web"
                    root.mkdir()
                    # a delivery-only init needs the repo's own package.json
                    (root / "package.json").write_text(
                        '{"name": "golden-web", "private": true, '
                        '"scripts": {"build": "vite build"}}\n',
                        encoding="utf-8")
                    code = frontend_repo_init.main([str(root), *argv])
                    if code not in (None, 0):
                        raise RuntimeError(f"case {name}: exit {code}\n{sink.getvalue()}")
            return tree_manifest(root)
    finally:
        cp._random_secret = saved_secret
        ui_wiring._version = saved_version
        sys.argv = saved_argv
        os.environ.update(saved_env)


def manifest_path(name: str) -> Path:
    return GOLDEN_DIR / f"{name}.json"


def diff(old: dict[str, str], new: dict[str, str]) -> list[str]:
    lines = []
    for rel in sorted(set(old) | set(new)):
        if rel not in new:
            lines.append(f"  - {rel}")
        elif rel not in old:
            lines.append(f"  + {rel}")
        elif old[rel] != new[rel]:
            lines.append(f"  ~ {rel}")
    return lines


def check(selected: list[str] | None = None, *, update: bool = False) -> int:
    all_cases = cases()
    names = selected or list(all_cases)
    unknown = [n for n in names if n not in all_cases]
    if unknown:
        print(f"unknown case(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    failed = 0
    for name in names:
        got = run_case(name, all_cases[name])
        if not got:
            print(f"{name}: generated NOTHING — the case is broken, not green")
            failed += 1
            continue
        path = manifest_path(name)
        want = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        changes = diff(want or {}, got)
        if update:
            if want != got:
                GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n",
                                encoding="utf-8")
                print(f"{name}: updated ({len(changes)} file(s) changed)")
                print("\n".join(changes))
            continue
        if want is None:
            print(f"{name}: no golden manifest — run `make golden-update`")
            failed += 1
        elif changes:
            print(f"{name}: {len(changes)} file(s) differ from the golden manifest")
            print("\n".join(changes))
            failed += 1
    if not update:
        stale = sorted(
            p.stem for p in GOLDEN_DIR.glob("*.json") if p.stem not in all_cases
        ) if selected is None else []
        for name in stale:
            print(f"{name}: golden manifest without a case — delete it or restore the case")
            failed += 1
        print(f"golden: checked {len(names)} case(s), {failed} failed")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--update", action="store_true", help="rewrite the manifests")
    p.add_argument("--case", action="append", help="run only this case (repeatable)")
    p.add_argument("--list", action="store_true", help="print the case names")
    args = p.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    if args.list:
        print("\n".join(cases()))
        return 0
    return check(args.case, update=args.update)


if __name__ == "__main__":
    sys.exit(main())
