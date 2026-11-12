"""Install unreleased bricks and pairs from the workspace, inside the project.

A registry row with ``"released": False`` (``STAPEL_LIBS`` or
``FRONTEND_REACT_LIBS``) has no PyPI/npm release, so ``--allow-unreleased``
must install it from the sibling workspace checkout. An editable line
(``-e ../stapel-<name>``) only resolves on the machine that generated the
project: the service image is built from the project directory and never sees
a sibling. So the checkout is SNAPSHOTTED into the project instead:

* a backend brick becomes ``<service>/vendor/stapel-<name>-<version>.tar.gz``,
  a ``git archive`` of the sibling's committed ``HEAD`` (an sdist pip builds;
  never the working tree, whose uncommitted edits nobody reviewed), and the
  requirements line names that file;
* a frontend pair becomes ``frontend/vendor/<npm pack tarball>``, packed from
  the sibling ``stapel-react/packages/<name>-react`` (its built ``dist/``), and
  ``package.json`` depends on it with ``file:vendor/…``.

Each snapshot records the commit it came from. Where the siblings live:
``$SIBLING_ROOT`` (the workspace convention of ``check_nav_manifest_sync.py``
and every generated ``gen:*`` script), else the directory the project is
generated into — the same place ``-e ../stapel-<name>`` points at. A missing
checkout is reported by path, never skipped silently.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class UnreleasedSourceError(ValueError):
    """A sibling checkout an unreleased selection needs is absent or unusable."""


@dataclass(frozen=True)
class Snapshot:
    path: Path  # the file written into the project
    commit: str  # the sibling commit it was taken from (backend) or "" (pair)


def sibling_root(output_dir: Path) -> Path:
    env = os.environ.get("SIBLING_ROOT")
    return Path(env).expanduser().resolve() if env else Path(output_dir).resolve()


def backend_checkout(root: Path, key: str) -> Path:
    return root / f"stapel-{key}"


def pair_checkout(root: Path, key: str) -> Path:
    return root / "stapel-react" / "packages" / f"{key}-react"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise UnreleasedSourceError(
            f"git {' '.join(args)} failed in {repo}: {result.stderr.strip()}")
    return result.stdout.strip()


def _project_version(repo: Path) -> str:
    text = _git(repo, "show", "HEAD:pyproject.toml")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise UnreleasedSourceError(f"{repo}: HEAD:pyproject.toml declares no version")
    return match.group(1)


def available(root: Path, backend: list[str], pairs: list[str]) -> tuple[list[str], list[str], list[str]]:
    """``(backend, pairs, absent)``: the keys whose checkout can be snapshotted,
    and the paths that are missing. A missing brick keeps the editable
    ``-e ../stapel-<name>`` line (it builds only next to the workspace, never
    in the image); a missing pair keeps its planned npm range, which no
    registry serves. The caller prints both."""
    ok_backend = [k for k in backend if (backend_checkout(root, k) / ".git").exists()]
    ok_pairs = [k for k in pairs if (pair_checkout(root, k) / "dist").is_dir()]
    absent = [str(backend_checkout(root, k)) for k in backend if k not in ok_backend]
    absent += [f"{pair_checkout(root, k)}/dist" for k in pairs if k not in ok_pairs]
    return ok_backend, ok_pairs, absent


def snapshot_backend(root: Path, key: str, dest_dir: Path) -> Snapshot:
    """``git archive`` of the sibling brick's HEAD as an sdist in *dest_dir*."""
    repo = backend_checkout(root, key)
    commit = _git(repo, "rev-parse", "HEAD")
    version = _project_version(repo)
    name = f"stapel-{key}-{version}"
    # git runs in the sibling checkout: a relative destination would land there.
    dest_dir = Path(dest_dir).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"{name}.tar.gz"
    _git(repo, "archive", "--format=tar.gz", f"--prefix={name}/", "-o", str(target), "HEAD")
    return Snapshot(target, commit)


def snapshot_pair(root: Path, key: str, dest_dir: Path) -> Snapshot:
    """``npm pack`` of the sibling pair's built package into *dest_dir*."""
    package = pair_checkout(root, key)
    npm = shutil.which("npm")
    if npm is None:
        raise UnreleasedSourceError("--allow-unreleased packs a pair with npm, which is not on PATH")
    # npm runs in the sibling package: a relative destination would land there.
    dest_dir = Path(dest_dir).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [npm, "pack", "--json", "--ignore-scripts", "--pack-destination", str(dest_dir)],
        cwd=package, capture_output=True, text=True)
    if result.returncode != 0:
        raise UnreleasedSourceError(f"npm pack failed in {package}: {result.stderr.strip()}")
    filename = json.loads(result.stdout)[0]["filename"]
    commit = ""
    try:
        commit = _git(package, "rev-parse", "HEAD")
    except UnreleasedSourceError:
        pass
    return Snapshot(dest_dir / filename, commit)


def vendor_requirements(reqs: Path, service_dir: Path, root: Path, keys: list[str]) -> list[Snapshot]:
    """Replace each ``-e ../stapel-<key>`` line of *reqs* with the snapshot."""
    lines = reqs.read_text().splitlines()
    service_dir = Path(service_dir).resolve()
    snaps = []
    for key in keys:
        snap = snapshot_backend(root, key, service_dir / "vendor")
        rel = f"./{snap.path.relative_to(service_dir).as_posix()}"
        pattern = re.compile(rf"^-e \.\./stapel-{re.escape(key)}(\[[^\]]*\])?$")
        for i, line in enumerate(lines):
            match = pattern.match(line)
            if match:
                lines[i] = f"{rel}{match.group(1) or ''}"
                if i and lines[i - 1].startswith("#"):
                    lines[i - 1] = (
                        f"# stapel_{key} — not on PyPI yet: a snapshot of the workspace "
                        f"checkout at {snap.commit[:12]} (stapel-create-project "
                        "--allow-unreleased); replace with the released pin once published")
        snaps.append(snap)
    reqs.write_text("\n".join(lines) + "\n")
    return snaps


_REQS_COPY = re.compile(r"^COPY (?P<svc>[^ ]+)/requirements\.txt \.$", re.MULTILINE)


def vendor_dockerfile(dockerfile: Path) -> None:
    """The image copies ``vendor/`` before it installs the requirements."""
    text = dockerfile.read_text()
    match = _REQS_COPY.search(text)
    if match is None or f"COPY {match['svc']}/vendor ./vendor" in text:
        return
    line = f"COPY {match['svc']}/vendor ./vendor\n"
    dockerfile.write_text(text[:match.start()] + line + text[match.start():])


def vendor_package_json(package_json: Path, frontend_dir: Path, root: Path,
                        pairs: list[tuple[str, str]]) -> list[Snapshot]:
    """Point each unreleased pair's dependency at its packed tarball."""
    pkg = json.loads(package_json.read_text())
    frontend_dir = Path(frontend_dir).resolve()
    deps = pkg.setdefault("dependencies", {})
    snaps = []
    for key, package in pairs:
        snap = snapshot_pair(root, key, frontend_dir / "vendor")
        deps[package] = f"file:{snap.path.relative_to(frontend_dir).as_posix()}"
        snaps.append(snap)
    package_json.write_text(json.dumps(pkg, indent=2) + "\n")
    return snaps
