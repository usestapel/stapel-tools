"""``stapel-shell-lint``: a page renders inside the shell, and the generated
navigation is what the scaffold wrote (navigation-templates spec §6.3).

Static, no node, no browser. Reads a project built by ``stapel-create-project``
(monolith: ``frontend/``) or ``stapel-frontend-repo-init`` (the repository root):

| Code | Finding |
|---|---|
| NAV001 | a route mounted outside the shell layout route (``id: "stapel-shell"``) in ``src/routes*.tsx``, or route files with no shell layout at all |
| NAV002 | a page module under ``src/pages/**`` that neither ``src/app.pages.ts`` nor a route file references |
| NAV003 | a ``stapel.app-nav.json`` entry with no ``app.pages.ts`` binding, or a binding with no entry |
| NAV004 | a generated file whose SHA-256 differs from ``navigation.resolved.generated`` (or is missing) |
| NAV005 | ``stapel.composition.json`` has no ``navigation.resolved``, or ``signIn`` is set and ``slots.account`` resolved to nothing |

What it cannot see: a route tree built at run time (the generated ``shell``
and ``nav`` vitest gates walk the real router object; both are kept), a page
linked from nowhere (the nav gate), and mobile screens registered outside the
app's ``NavContribution`` (NAV006, queued until the KMP app has a registration
surface a static reader can see).

A project that predates the navigation block records its accepted findings in
a baseline (``--write-baseline``); a finding listed there is a warning, every
other finding an error. Exit 1 on any error.

    stapel-shell-lint [PROJECT] [--baseline FILE] [--write-baseline FILE]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

SHELL_ID = "stapel-shell"
_ROUTE_KEY = re.compile(r"(?<![\w$])(path\s*:|index\s*:\s*true\b|<Route\b[^>]*\bpath=)")
_SHELL_KEY = re.compile(r"""\bid\s*:\s*["']""" + SHELL_ID + r"""["']""")
_IMPORT = re.compile(r"""(?:from\s+|import\s*\(\s*|import\s+)["']([^"']+)["']""")


@dataclass(frozen=True)
class Finding:
    code: str
    path: str
    line: int
    message: str
    baseline: bool = False

    def render(self) -> str:
        level = "warning" if self.baseline else "error"
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{where}: {self.code} {level}: {self.message}"


def _strip_comments(text: str) -> str:
    """Comments blanked out, line breaks kept (so offsets map to lines)."""
    def blank(m: re.Match) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))

    return re.sub(r"/\*.*?\*/|//[^\n]*", blank, text, flags=re.S)


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _enclosing_object(text: str, offset: int) -> Optional[tuple[int, int]]:
    """The ``{ … }`` span that contains *offset* (string contents ignored)."""
    depth = 0
    start = None
    for i in range(offset, -1, -1):
        ch = text[i]
        if ch == "}":
            depth += 1
        elif ch == "{":
            if depth == 0:
                start = i
                break
            depth -= 1
    if start is None:
        return None
    depth = 0
    for j in range(start, len(text)):
        ch = text[j]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return start, j
    return None


def _frontend(project: Path) -> Path:
    return project / "frontend" if (project / "frontend" / "src").is_dir() else project


def _rel(project: Path, path: Path) -> str:
    try:
        return path.relative_to(project).as_posix()
    except ValueError:
        return path.as_posix()


def check_routes(project: Path) -> list[Finding]:
    """NAV001."""
    src = _frontend(project) / "src"
    files = sorted(src.glob("routes*.tsx"))
    findings: list[Finding] = []
    shells = 0
    for path in files:
        text = _strip_comments(path.read_text(encoding="utf-8"))
        spans = []
        for m in _SHELL_KEY.finditer(text):
            span = _enclosing_object(text, m.start())
            if span:
                spans.append(span)
        shells += len(spans)
        for m in _ROUTE_KEY.finditer(text):
            if any(a < m.start() < b for a, b in spans):
                continue
            findings.append(Finding(
                "NAV001", _rel(project, path), _line(text, m.start()),
                f"a route outside the shell layout route (id \"{SHELL_ID}\"): it renders "
                "without the header and navigation; mount it under the shell (stapel.app-nav.json "
                "+ stapel-nav-regenerate)"))
    if files and not shells:
        findings.append(Finding(
            "NAV001", _rel(project, files[0]), 0,
            f"no shell layout route (id \"{SHELL_ID}\") in {', '.join(p.name for p in files)}"))
    return findings


def check_pages(project: Path) -> list[Finding]:
    """NAV002."""
    src = _frontend(project) / "src"
    pages = sorted(p for p in (src / "pages").rglob("*.tsx") if "__tests__" not in p.parts) \
        if (src / "pages").is_dir() else []
    if not pages:
        return []
    referenced: set[str] = set()
    for path in [src / "app.pages.ts", src / "app.pages.tsx", *src.glob("routes*.tsx")]:
        if path.is_file():
            text = _strip_comments(path.read_text(encoding="utf-8"))
            referenced |= {re.sub(r"\.(js|jsx|ts|tsx)$", "", spec) for spec in _IMPORT.findall(text)}
    out = []
    for page in pages:
        module = page.relative_to(src).with_suffix("").as_posix()      # pages/Foo
        if not any(spec == module or spec.endswith("/" + module) for spec in referenced):
            out.append(Finding(
                "NAV002", _rel(project, page), 0,
                "a page module that src/app.pages.ts does not bind and no route imports: "
                "add its stapel.app-nav.json entry and app.pages.ts binding, or delete it"))
    return out


def check_app_nav(project: Path) -> list[Finding]:
    """NAV003."""
    from .nav_cli import bound_app_pages, load_app_nav

    app_nav = project / "stapel.app-nav.json"
    pages_ts = _frontend(project) / "src" / "app.pages.ts"
    entries = {e.get("id") for e in load_app_nav(app_nav) if e.get("id")}
    bound = bound_app_pages(pages_ts)
    out = []
    for dest in sorted(entries - bound):
        out.append(Finding("NAV003", _rel(project, app_nav), 0,
                           f"{dest}: an entry with no binding in {_rel(project, pages_ts)}"))
    for dest in sorted(bound - entries):
        out.append(Finding("NAV003", _rel(project, pages_ts), 0,
                           f"{dest}: a binding with no entry in {_rel(project, app_nav)}"))
    return out


def _resolved(project: Path) -> tuple[Optional[dict], Optional[Path]]:
    """The React ``navigation.resolved`` and the composition file it came from."""
    for path in (_frontend(project) / "stapel.composition.json", project / "stapel.composition.json"):
        if not path.is_file():
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        resolved = (doc.get("navigation") or {}).get("resolved") or (doc.get("resolved") or {}).get("react")
        if resolved:
            return resolved, path
    path = _frontend(project) / "stapel.composition.json"
    return None, path if path.is_file() else (project / "stapel.composition.json")


def check_generated(project: Path) -> list[Finding]:
    """NAV004."""
    resolved, origin = _resolved(project)
    out = []
    for rel, digest in sorted(((resolved or {}).get("generated") or {}).items()):
        path = project / rel
        if not path.is_file():
            out.append(Finding("NAV004", rel, 0, f"generated file recorded in {_rel(project, origin)} "
                               "is missing — run stapel-nav-regenerate"))
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            out.append(Finding("NAV004", rel, 0, "differs from its recorded hash (a hand edit): change "
                               "stapel.composition.json / stapel.app-nav.json and run "
                               "stapel-nav-regenerate instead"))
    return out


def check_composition(project: Path) -> list[Finding]:
    """NAV005."""
    resolved, origin = _resolved(project)
    where = _rel(project, origin) if origin else "stapel.composition.json"
    if resolved is None:
        return [Finding("NAV005", where, 0,
                        "no navigation.resolved: the project predates the navigation block "
                        "(regenerate, or baseline this finding until it adopts it)")]
    if resolved.get("signIn") and not (resolved.get("slots") or {}).get("account"):
        return [Finding("NAV005", where, 0,
                        f"signIn is {resolved['signIn']!r} and slots.account resolved to nothing: "
                        "a guest has no way to sign in from the shell")]
    return []


CHECKS = (check_routes, check_pages, check_app_nav, check_generated, check_composition)


def lint(project: Path, baseline: Optional[set[tuple[str, str]]] = None) -> list[Finding]:
    project = Path(project)
    findings = [f for check in CHECKS for f in check(project)]
    if baseline:
        findings = [Finding(f.code, f.path, f.line, f.message, baseline=(f.code, f.path) in baseline)
                    for f in findings]
    return findings


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="stapel-shell-lint", description=__doc__.split("\n\n")[0])
    parser.add_argument("project", type=Path, nargs="?", default=Path("."))
    parser.add_argument("--baseline", type=Path, help="accepted findings ([{code, path}]) -> warnings")
    parser.add_argument("--write-baseline", type=Path, help="write the current findings as a baseline")
    args = parser.parse_args(argv)
    if not (args.project / "stapel.composition.json").is_file() and not (
            args.project / "frontend" / "stapel.composition.json").is_file():
        print(f"stapel-shell-lint: {args.project} has no stapel.composition.json — not a "
              "scaffolded project; nothing checked", file=sys.stderr)
        return 1
    baseline = None
    if args.baseline:
        baseline = {(b["code"], b["path"]) for b in json.loads(args.baseline.read_text(encoding="utf-8"))}
    findings = lint(args.project, baseline)
    if args.write_baseline:
        args.write_baseline.write_text(json.dumps(
            [{"code": f.code, "path": f.path} for f in findings], indent=2) + "\n", encoding="utf-8")
    for f in findings:
        print(f.render())
    errors = sum(1 for f in findings if not f.baseline)
    src = _frontend(args.project) / "src"
    resolved, _ = _resolved(args.project)
    seen = (f"{len(list(src.glob('routes*.tsx')))} route file(s), "
            f"{len(list((src / 'pages').rglob('*.tsx'))) if (src / 'pages').is_dir() else 0} page "
            f"module(s), {len(((resolved or {}).get('generated') or {}))} recorded hash(es)")
    print(f"stapel-shell-lint: NAV001-NAV005 over {seen}: {errors} error(s), "
          f"{len(findings) - errors} baselined")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
