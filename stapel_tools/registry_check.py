"""``stapel-registry-check`` — a tag without a release is not a release.

Why this exists
---------------
Publishing is the one step in the wave nothing watches. Everything up to it is
gated: tests, lint, contract drift, the aggregate. Then a tag is pushed, a
workflow *should* pick it up, and whether a version actually reached the
registry is discovered — if at all — by somebody typing a URL.

It has failed silently at least three ways in this fleet:

* **Trusted publisher not configured.** Four libraries carried tags for
  versions that never existed on PyPI; the publish job failed on its own
  config and the tag looked like a release for weeks (tracker #48).
* **A lightweight tag is not pushed by ``--follow-tags``.** Two releases
  (0.15.2, 0.15.3) never left the machine. The push reported success because
  it had nothing to complain about.
* **The CI provider is simply down.** 2026-08-06: stapel-core 0.19.0 and
  stapel-tools 0.29.1 — commits and tags on the remote, Actions in
  ``major_outage``, no workflow run created at all. Found by hand, again.

The check is cheap and needs no credentials: git tags on one side, the
registry's public index on the other.

Rules
-----
REG001  (error)  A ``v<version>`` tag exists and that version is NOT in the
        registry. Whatever the reason — failed job, unpushed tag, provider
        outage — a consumer asking for that version gets nothing. This is the
        one that would have caught all three incidents above.

REG002  (warning) A version IS in the registry with no matching tag. Not
        fatal, but it means the released artifact cannot be traced back to a
        commit — the reverse of a release.

REG003  (warning) ``pyproject``'s current version is neither tagged nor
        released. Normal mid-development; worth stating once so "I thought I
        released that" has an answer.

Deliberately NOT a rule: a registry version NEWER than anything local. That
is a stale checkout, not a defect in the release.

Prehistory is excluded by default
---------------------------------
A tag OLDER than the package's earliest release is not a broken release: the
package was not on the registry at all back then, so no consumer could ever
have asked for that version. Every fleet library carries a handful of such
tags from before publishing was set up.

This is not cosmetic. Measured on this fleet 2026-08-07: 100 missing tags in
total, 60 of them prehistory. Reporting all 100 buries the ~40 that are real
— including stapel-core 0.19.0, stuck that same day — under noise nobody can
act on, and a gate nobody reads is a gate that does not exist.

How many were excluded is always printed, never dropped silently;
``--all-history`` includes them as findings.

Honest limits
-------------
* Network is required. With no network the check reports that it could not
  reach the registry and FAILS — a publish gate that silently passes offline
  is the exact defect class this module is about (see ``--offline-ok`` for
  the deliberate, noisy opt-out).
* Only PyPI (Python) and npm (JS) are understood. Another registry is
  reported as unchecked, by name, rather than skipped.
* A yanked PyPI release still counts as present: it exists, it resolves, and
  deciding whether yanking was intended is not this gate's business.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

PYPI_URL = "https://pypi.org/pypi/{name}/json"
NPM_URL = "https://registry.npmjs.org/{name}"
TIMEOUT = 15.0


@dataclass
class Finding:
    path: str
    rule: str
    message: str
    level: str = "error"

    def __str__(self) -> str:
        tag = self.rule if self.level == "error" else f"{self.rule} warning"
        return f"{self.path}: [{tag}] {self.message}"

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "rule": self.rule,
            "message": self.message,
            "level": self.level,
        }


# ---------------------------------------------------------------------------
# reading the checkout
# ---------------------------------------------------------------------------


def project_name_and_version(repo: Path) -> tuple[Optional[str], Optional[str], str]:
    """``(name, version, kind)`` — kind is "pypi" | "npm" | "unknown"."""
    pyproject = repo / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text(encoding="utf-8")
        name = _first(re.findall(r'(?m)^name\s*=\s*["\']([^"\']+)["\']', text))
        version = _first(re.findall(r'(?m)^version\s*=\s*["\']([^"\']+)["\']', text))
        return name, version, "pypi"
    package_json = repo / "package.json"
    if package_json.is_file():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except Exception:
            return None, None, "unknown"
        return data.get("name"), data.get("version"), "npm"
    return None, None, "unknown"


def _first(values: list[str]) -> Optional[str]:
    return values[0] if values else None


def version_key(version: str) -> tuple:
    """Sortable numeric key; anything unparsable sorts first (treated as old).

    Deliberately lenient — a version this cannot parse must not crash a gate
    whose whole job is to be cheap enough to always run.
    """
    parts = []
    for chunk in re.split(r"[.\-+]", version)[:3]:
        digits = re.match(r"\d+", chunk)
        parts.append(int(digits.group()) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def git_version_tags(repo: Path) -> list[str]:
    """Versions from ``v<x.y.z>`` tags, in tag order, deduplicated."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "tag", "--list", "v*"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except Exception:
        return []
    seen: list[str] = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if re.fullmatch(r"v\d+\.\d+\.\d+.*", line):
            version = line[1:]
            if version not in seen:
                seen.append(version)
    return seen


# ---------------------------------------------------------------------------
# asking the registry
# ---------------------------------------------------------------------------


class RegistryUnreachable(RuntimeError):
    """The registry could not be asked — NOT the same as 'nothing published'."""


def released_versions(name: str, kind: str) -> set[str]:
    url = (PYPI_URL if kind == "pypi" else NPM_URL).format(name=name)
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
            data = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return set()  # the package genuinely has no releases yet
        raise RegistryUnreachable(f"{url} -> HTTP {exc.code}") from exc
    except Exception as exc:
        raise RegistryUnreachable(f"{url} -> {exc!r}") from exc
    if kind == "pypi":
        return set(data.get("releases") or {})
    return set((data.get("versions") or {}))


# ---------------------------------------------------------------------------
# the check
# ---------------------------------------------------------------------------


def check_repo(
    repo: Path,
    *,
    notes: Optional[list[str]] = None,
    all_history: bool = False,
) -> list[Finding]:
    notes = notes if notes is not None else []
    repo = repo.resolve()
    label = repo.name
    name, version, kind = project_name_and_version(repo)

    if name is None:
        notes.append(
            f"{label}: no pyproject.toml and no package.json — nothing "
            f"publishable here, nothing checked."
        )
        return []
    if kind == "unknown":
        notes.append(
            f"{label}: package manifest is not one this check understands "
            f"(only PyPI and npm) — {name} was NOT verified."
        )
        return []

    tags = git_version_tags(repo)
    if not tags and version is None:
        notes.append(f"{label}: no version tags and no version in the manifest.")
        return []

    try:
        published = released_versions(name, kind)
    except RegistryUnreachable as exc:
        raise RegistryUnreachable(
            f"{label}: cannot reach the {kind} registry for {name!r} ({exc}). "
            f"Refusing to report success — a publish gate that passes when it "
            f"could not ask is exactly the failure this check exists to catch. "
            f"Use --offline-ok to accept that, loudly, on purpose."
        ) from exc

    findings: list[Finding] = []
    missing = [t for t in tags if t not in published]
    # Prehistory: tags older than the very first release. The package didn't
    # exist on the registry yet, so there was nobody to publish that version
    # — not a broken release. Counted and named, but not flagged.
    prehistoric: list[str] = []
    if published and not all_history:
        floor = min(published, key=version_key)
        prehistoric = [m for m in missing if version_key(m) < version_key(floor)]
        missing = [m for m in missing if m not in prehistoric]
    if prehistoric:
        notes.append(
            f"{label}: {len(prehistoric)} tag(s) older than the first release "
            f"({min(published, key=version_key)}) — the package didn't exist "
            f"on the registry yet, these tags were NOT checked as releases "
            f"(--all-history will show them)."
        )
    for tagged in missing:
        findings.append(Finding(
            label, "REG001",
            f"tag v{tagged} exists but {name} {tagged} is NOT on "
            f"{'PyPI' if kind == 'pypi' else 'npm'}. Whatever the cause — a "
            f"failed publish job, a tag that never left the machine, the CI "
            f"provider being down — anyone asking for that version gets "
            f"nothing, and the tag looks like a release that happened.",
        ))
    for release in sorted(published - set(tags)):
        findings.append(Finding(
            label, "REG002",
            f"{name} {release} is published but no v{release} tag exists here — "
            f"the artifact cannot be traced back to a commit.",
            level="warning",
        ))
    if version and version not in published and version not in tags:
        findings.append(Finding(
            label, "REG003",
            f"the manifest says {version}, which is neither tagged nor "
            f"published — expected mid-development, stated once so it is not "
            f"mistaken for a release.",
            level="warning",
        ))
    return findings


def discover_repos(target: Path) -> list[Path]:
    """*target* itself if it is a package, else every ``stapel-*`` under it."""
    if (target / "pyproject.toml").is_file() or (target / "package.json").is_file():
        return [target]
    return sorted(
        p for p in target.glob("stapel-*")
        if p.is_dir() and ((p / "pyproject.toml").is_file() or (p / "package.json").is_file())
    )


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-registry-check",
        description=(
            "Verify that every v<version> git tag corresponds to a version "
            "actually present in the package registry — a tag without a "
            "release is not a release."
        ),
    )
    parser.add_argument("target", nargs="?", default=".", type=Path)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--strict", action="store_true", help="fail on warnings too (REG002/REG003)"
    )
    parser.add_argument(
        "--all-history", action="store_true",
        help=(
            "also report tags older than the package's earliest release "
            "(excluded by default: back then the package was not on the "
            "registry at all, so nobody could have asked for that version)"
        ),
    )
    parser.add_argument(
        "--offline-ok", action="store_true",
        help="do not fail when the registry cannot be reached (says so loudly)",
    )
    args = parser.parse_args(argv)

    repos = discover_repos(args.target.resolve())
    if not repos:
        print(
            f"stapel-registry-check: nothing publishable found under "
            f"{args.target} — NOTHING WAS VERIFIED (this is not a pass).",
            file=sys.stderr,
        )
        return 1

    findings: list[Finding] = []
    notes: list[str] = []
    unreachable: list[str] = []
    for repo in repos:
        try:
            findings.extend(check_repo(repo, notes=notes, all_history=args.all_history))
        except RegistryUnreachable as exc:
            unreachable.append(str(exc))

    if args.json:
        print(json.dumps({
            "ok": not findings and not unreachable,
            "checked": [p.name for p in repos],
            "findings": [f.to_dict() for f in findings],
            "notes": notes,
            "unreachable": unreachable,
        }, indent=2, ensure_ascii=False))
    else:
        for note in notes:
            print(f"stapel-registry-check: {note}", file=sys.stderr)
        for problem in unreachable:
            print(f"stapel-registry-check: {problem}", file=sys.stderr)
        for finding in findings:
            print(finding)
        errors = sum(1 for f in findings if f.level == "error")
        warnings = len(findings) - errors
        if findings:
            print(f"\n{errors} error(s), {warnings} warning(s).")
        elif not unreachable:
            print(f"Every tag has a release across {len(repos)} package(s).")

    if unreachable and not args.offline_ok:
        return 1
    if any(f.level == "error" for f in findings):
        return 1
    if args.strict and findings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
