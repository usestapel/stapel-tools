"""
stapel-storefront — the public face of the fleet, assembled from the same
catalogue aggregate the agents read.

Why this exists
---------------
We publish 26 Python packages and an ``@stapel`` npm scope, and every one of
them ships an ``llms.txt`` so that an agent arriving at the fleet can find out
what it contains. A *human* who follows the same link arrives at an empty
organisation page and a list of bare repository names. The machines have a
front door and the people do not.

The obvious fix — write a nice page with a table of the libraries — is the
wrong one, and the fleet has already paid for that lesson three times: a
hand-maintained table of 26 versions is stale within a fortnight (tracker
#226, hand-written ``capabilities.json`` files carrying versions several
releases behind whatever the wheel actually shipped). So the storefront is
generated from the catalogue aggregate (``stapel_tools.catalog``, drift-gated
per #184) plus each module's ``pyproject.toml``, and the version numbers are
not typed anywhere: the table's version and download cells are **live badges**,
rendered by shields/pepy against PyPI at the moment a reader looks at them.
Even a stale committed page tells the truth about versions.

Badges come from :mod:`stapel_tools.readme`, unchanged — one badge canon
(``docs/pending/badge-canon.md``), one implementation, used by both the module
READMEs and this page. A module whose ``[tool.stapel.readme] pypi`` says it is
unpublished gets an "unreleased" marker here too, for the same reason it gets
one there: a badge that cannot be true is not emitted.

Two renderings, one source
--------------------------
``--format md`` (default) writes ``index.md`` — the shape GitHub renders as an
organisation profile page (``usestapel/.github`` → ``profile/README.md``).
This needs no hosting, no DNS and no build step, which makes it the fastest
route from "no face at all" to a real front door.

``--format html`` writes ``index.html`` — one self-contained file (inline CSS,
no external assets beyond the badge images themselves), suitable for GitHub
Pages, Netlify, an S3 bucket or a `python -m http.server`.

``--format all`` writes both.

Determinism: modules are sorted by name, nothing carries a timestamp, so
``--check`` is a meaningful drift gate over a committed page.
"""
from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

from stapel_tools.catalog import build_catalog, discover_workspace, load_documents_with_roots
from stapel_tools.readme import (
    EmitError,
    badges,
    is_published,
    load_inputs,
    repo_slug,
)

#: Headline. Kept here rather than in a data file because it is the one piece
#: of the page that is an editorial claim rather than a derived fact — and a
#: claim belongs in review, not in a config nobody reads.
TAGLINE = (
    "Composable Django apps that deploy as a monolith or as microservices — "
    "without changing module code."
)

PITCH = """\
Stapel is a set of pip-installable Django packages built on one rule: **modules
never import each other.** Every cross-module call goes through a
name-addressed primitive with a pluggable transport, so the same code runs
in-process in a monolith and over NATS or Kafka in a service mesh. Deployment
topology is configuration, not a rewrite.

| Primitive | For | Guarantee |
|---|---|---|
| **Action** — `emit("user.deleted", {...})` | a fact, 0..N subscribers | at-least-once through a transactional outbox: the event leaves if and only if your database transaction commits |
| **Function** — `call("cdn.media_exists", {...})` | a synchronous call, one provider | a result or an exception, with a protocol-level timeout |
| **Task** — `start("llm.summarize", {...})` | long work, a result "eventually" | a persistent state machine — retries, deadline sweep, completion Actions |
"""

#: The quickstart. Every command here was RUN, not transcribed: see
#: ``tests/test_storefront.py`` for the shape check and the commit message for
#: the run. The first two commands are what a newcomer types; the third is what
#: the project they just generated tells them to type, in its own README.
QUICKSTART = """\
```bash
pip install stapel-tools
stapel-create-project my-app          # interactive wizard
```

The wizard asks for a topology (monolith, microservices or minimal), a broker
and the modules to wire in; every answer is also a flag, so it scripts:

```bash
stapel-create-project my-app \\
    --type monolith --modules auth notifications \\
    --title "My App" --url https://myapp.example \\
    --company-name "ACME" --company-email hello@myapp.example
```

What you get is not a skeleton. It is a Django service with the modules you
picked already installed and mounted, a Vite/React frontend wired to the
backend's generated client, Docker Compose files for local and production, an
nginx config with the reserved-path routing, deploy scripts that refuse a
development environment file, and pre-commit hooks running the convention
linters and every drift gate. No LLM is involved: the scaffold is assembled
deterministically from the libraries' own manifests.

```bash
cd my-app
docker compose -f docker-compose.local.yml --env-file .env.local up
```

`.env.local` is committed on purpose — recognisable development values, no
secrets — so a clone runs with nothing to fill in by hand.
"""

FOOTER_NOTE = (
    "This page is generated by `stapel-storefront` from the catalogue "
    "aggregate — the same `docs/capabilities.json` documents the libraries "
    "ship and the agents read. The version and download cells are live badges, "
    "so they cannot go stale between regenerations."
)

#: Inline stylesheet for the HTML rendering. Dark mode is not a nicety here:
#: the badge images are transparent PNG/SVG designed for both, and a page that
#: ignores the reader's preference is the kind of detail that says "nobody
#: looked at this".
CSS = """\
:root { color-scheme: light dark; --fg: #14161a; --muted: #5b6470;
        --bg: #ffffff; --line: #e3e7ec; --accent: #1c6feb; --code-bg: #f5f7fa; }
@media (prefers-color-scheme: dark) {
  :root { --fg: #e7eaee; --muted: #9aa4b1; --bg: #0f1115; --line: #262b33;
          --accent: #6ea8ff; --code-bg: #171a20; }
}
* { box-sizing: border-box; }
body { margin: 0; padding: 0 1.25rem 5rem; background: var(--bg); color: var(--fg);
       font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
       Helvetica, Arial, sans-serif; }
main { max-width: 62rem; margin: 0 auto; }
header { padding: 4.5rem 0 2rem; border-bottom: 1px solid var(--line); margin-bottom: 2.5rem; }
h1 { font-size: clamp(2.25rem, 6vw, 3.5rem); line-height: 1.1; margin: 0 0 1rem;
     letter-spacing: -0.02em; }
h2 { font-size: 1.5rem; margin: 3rem 0 1rem; letter-spacing: -0.01em; }
.tagline { font-size: 1.25rem; color: var(--muted); max-width: 44rem; margin: 0; }
a { color: var(--accent); }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.9em; }
pre { background: var(--code-bg); border: 1px solid var(--line); border-radius: 8px;
      padding: 1rem; overflow-x: auto; }
code { background: var(--code-bg); padding: 0.1em 0.35em; border-radius: 4px; }
pre code { background: none; padding: 0; }
.totals { display: flex; flex-wrap: wrap; gap: 2rem; margin: 2rem 0 0; padding: 0; list-style: none; }
.totals li { margin: 0; }
.totals b { display: block; font-size: 2rem; line-height: 1.1; letter-spacing: -0.02em; }
.totals span { color: var(--muted); font-size: 0.85rem; text-transform: uppercase;
               letter-spacing: 0.06em; }
.table-scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; }
th, td { text-align: left; padding: 0.6rem 0.75rem; border-bottom: 1px solid var(--line);
         vertical-align: top; }
th { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }
td.pkg { white-space: nowrap; font-weight: 600; }
td.badges { white-space: nowrap; }
td.badges img { vertical-align: middle; margin-right: 0.25rem; }
td.provides { color: var(--muted); font-size: 0.95rem; min-width: 22rem; }
footer { margin-top: 4rem; padding-top: 1.5rem; border-top: 1px solid var(--line);
         color: var(--muted); font-size: 0.9rem; }
"""


def module_rows(workspace: Path, sources: list[Path] | None = None) -> list[dict]:
    """One row per module: name, one-liner, badge markdown, published flag.

    Sourced from the catalogue aggregate (``provides``) joined with each
    module's own repo (``pyproject.toml`` for the badge preconditions). A repo
    that ships a ``capabilities.json`` but no parseable ``pyproject.toml`` is
    skipped loudly rather than listed with a guess.
    """
    picked = sources if sources is not None else discover_workspace(workspace)
    pairs, _skipped = load_documents_with_roots(picked)
    rows = []
    for doc, root in pairs:
        try:
            inputs = load_inputs(root)
            org, repo = repo_slug(inputs)
        except EmitError as exc:
            print(f"stapel-storefront: skipping {root.name}: {exc}", file=sys.stderr)
            continue
        rows.append(
            {
                "module": doc.get("module") or repo,
                "repo": repo,
                "org": org,
                "provides": " ".join((doc.get("provides") or "").split()),
                "badges": badges(inputs),
                "published": is_published(inputs),
            }
        )
    return sorted(rows, key=lambda row: row["module"])


def _first_sentence(text: str, limit: int = 180) -> str:
    """The one-liner, trimmed to a table cell.

    ``provides`` is written for an agent's context window and runs long; a
    storefront row has to be scannable. Cut at a sentence boundary when there
    is one inside the limit, so the cell never ends mid-clause.
    """
    if len(text) <= limit:
        return text
    cut = text.rfind(". ", 0, limit)
    # A sentence boundary wins whenever it still leaves a usable cell; below a
    # quarter of the budget it would summarise the module as a fragment, so the
    # word cut plus an ellipsis is the more honest "there is more".
    if cut > limit // 4:
        return text[: cut + 1]
    cut = text.rfind(" ", 0, limit)
    return text[:cut].rstrip(",;:") + "…"


#: Badges shown per table row. The full seven-badge canon on 26 rows is a wall
#: of images; the storefront's job is "which library, is it alive, which
#: version" — CI health and license belong on the library's own page.
TABLE_BADGES = ("pypi", "downloads", "status")


def _row_badges(row: dict) -> list[str]:
    keep = []
    for badge in row["badges"]:
        label = badge.split("]", 1)[0].lstrip("[![")
        if label in TABLE_BADGES:
            keep.append(badge)
    return keep


# ── markdown ────────────────────────────────────────────────────────────────


def render_markdown(rows: list[dict], totals: dict) -> str:
    org = rows[0]["org"] if rows else "usestapel"
    lines = [
        "# Stapel",
        "",
        f"**{TAGLINE}**",
        "",
        PITCH.rstrip("\n"),
        "",
        "## Quickstart",
        "",
        QUICKSTART.rstrip("\n"),
        "",
        f"## The libraries ({totals['modules']})",
        "",
        f"{totals['operations']} HTTP operations · {totals['axes']} configuration "
        f"axes · {totals['extension_points']} fork-free extension points, "
        "across every module below. Each ships its contract as machine-readable "
        "artifacts (`docs/capabilities.json`, `schema.json`, `flows.json`, "
        "`errors.json`) and an `llms.txt` for coding agents.",
        "",
        "| Package | Version | Downloads | What it gives you |",
        "|---|---|---|---|",
    ]
    for row in rows:
        badge_cells = _row_badges(row)
        version = badge_cells[0] if badge_cells else ""
        downloads = badge_cells[1] if len(badge_cells) > 1 else ""
        name = f"[`{row['module']}`](https://github.com/{row['org']}/{row['repo']})"
        lines.append(
            f"| {name} | {version} | {downloads} | {_first_sentence(row['provides'])} |"
        )
    lines += [
        "",
        "React components for these modules are published under the "
        f"[`@stapel`](https://www.npmjs.com/org/stapel) npm scope — "
        f"see [stapel-react](https://github.com/{org}/stapel-react).",
        "",
        "## For coding agents",
        "",
        "Every module ships `docs/llms.txt`: its configuration axes, its usage "
        "surface, its HTTP operations and its error codes, generated from the "
        "same artifacts as its code and gated against drift. Point your agent "
        "at the module it needs before it writes anything — the answer to "
        '"does Stapel already have something for this?" is in that file.',
        "",
        "---",
        "",
        f"<sub>{FOOTER_NOTE}</sub>",
    ]
    return "\n".join(lines) + "\n"


# ── html ────────────────────────────────────────────────────────────────────


def _md_badges_to_html(markdown_badges: list[str]) -> str:
    """``[![alt](img)](href)`` → ``<a href><img src alt></a>``.

    A tiny, deliberate parser rather than a markdown dependency: the input is
    not arbitrary markdown, it is the badge lines this package generates, and
    a dependency-free tool is worth more here than generality.
    """
    out = []
    for badge in markdown_badges:
        try:
            alt = badge[badge.index("[![") + 3: badge.index("](")]
            rest = badge[badge.index("](") + 2:]
            img = rest[: rest.index(")")]
            href = rest[rest.index("](") + 2: rest.rindex(")")]
        except ValueError:  # pragma: no cover - malformed badge
            continue
        out.append(
            f'<a href="{html.escape(href, quote=True)}">'
            f'<img src="{html.escape(img, quote=True)}" alt="{html.escape(alt)}"></a>'
        )
    return "".join(out)


def render_html(rows: list[dict], totals: dict) -> str:
    org = rows[0]["org"] if rows else "usestapel"
    parts = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Stapel — composable Django apps</title>",
        f'<meta name="description" content="{html.escape(TAGLINE)}">',
        f"<style>{CSS}</style>",
        "</head>",
        "<body><main>",
        "<header>",
        "<h1>Stapel</h1>",
        f'<p class="tagline">{html.escape(TAGLINE)}</p>',
        '<ul class="totals">',
        f"<li><b>{totals['modules']}</b><span>libraries</span></li>",
        f"<li><b>{totals['operations']}</b><span>HTTP operations</span></li>",
        f"<li><b>{totals['axes']}</b><span>config axes</span></li>",
        f"<li><b>{totals['extension_points']}</b><span>extension points</span></li>",
        "</ul>",
        "</header>",
        "<h2>Quickstart</h2>",
        "<pre><code>pip install stapel-tools\nstapel-create-project my-app</code></pre>",
        "<p>The wizard asks for a topology (monolith, microservices or minimal), "
        "a broker and the modules to wire in. What comes out is a Django service "
        "with those modules mounted, a Vite/React frontend on the generated "
        "client, Compose files for local and production, nginx routing, deploy "
        "scripts that refuse a development environment, and pre-commit hooks "
        "running every drift gate. No LLM is involved — the scaffold is "
        "assembled deterministically from the libraries&rsquo; own manifests.</p>",
        "<pre><code>cd my-app\ndocker compose -f docker-compose.local.yml "
        "--env-file .env.local up</code></pre>",
        f"<h2>The libraries ({totals['modules']})</h2>",
        "<p>Each ships its contract as machine-readable artifacts "
        "(<code>capabilities.json</code>, <code>schema.json</code>, "
        "<code>flows.json</code>, <code>errors.json</code>) and an "
        "<code>llms.txt</code> for coding agents.</p>",
        '<div class="table-scroll"><table>',
        "<thead><tr><th>Package</th><th>Version</th><th>Downloads</th>"
        "<th>What it gives you</th></tr></thead><tbody>",
    ]
    for row in rows:
        badge_cells = _row_badges(row)
        version = _md_badges_to_html(badge_cells[:1])
        downloads = _md_badges_to_html(badge_cells[1:2])
        href = f"https://github.com/{row['org']}/{row['repo']}"
        parts.append(
            f'<tr><td class="pkg"><a href="{href}">{html.escape(row["module"])}</a></td>'
            f'<td class="badges">{version}</td>'
            f'<td class="badges">{downloads}</td>'
            f'<td class="provides">{html.escape(_first_sentence(row["provides"]))}</td></tr>'
        )
    parts += [
        "</tbody></table></div>",
        "<h2>For coding agents</h2>",
        "<p>Every module ships <code>docs/llms.txt</code>: its configuration "
        "axes, its usage surface, its HTTP operations and its error codes — "
        "generated from the same artifacts as its code and gated against "
        "drift.</p>",
        f'<footer>{html.escape(FOOTER_NOTE)} '
        f'<a href="https://github.com/{org}">github.com/{org}</a></footer>',
        "</main></body></html>",
    ]
    return "\n".join(parts) + "\n"


# ── CLI ──────────────────────────────────────────────────────────────────────


FORMATS = {"md": ("index.md", render_markdown), "html": ("index.html", render_html)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-storefront",
        description="Generate the public storefront (library table + quickstart) "
        "from the catalogue aggregate. Version and download cells are live "
        "badges, so the page cannot lie about them between regenerations.",
    )
    parser.add_argument(
        "--workspace",
        default=".",
        help="Directory holding the stapel-* module repos (default: cwd).",
    )
    parser.add_argument(
        "--format",
        choices=("md", "html", "all"),
        default="md",
        help="md → index.md (GitHub organisation profile page); html → a "
        "self-contained index.html; all → both.",
    )
    parser.add_argument("--out-dir", default=".", help="Where to write (default: cwd).")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Drift gate: render in memory and compare byte for byte against "
        "the committed page(s); nonzero exit and no write on any drift.",
    )
    args = parser.parse_args(argv)

    workspace = Path(args.workspace).resolve()
    rows = module_rows(workspace)
    if not rows:
        print(
            f"stapel-storefront: no modules found under {workspace} — expected "
            "stapel-*/docs/capabilities.json. A storefront with no libraries on "
            "it is worse than none: nothing was written.",
            file=sys.stderr,
        )
        return 1
    docs, _ = load_documents_with_roots(discover_workspace(workspace))
    totals = build_catalog([doc for doc, _root in docs])["totals"]

    out_dir = Path(args.out_dir).resolve()
    wanted = ("md", "html") if args.format == "all" else (args.format,)
    rendered = {name: FORMATS[name][1](rows, totals) for name in wanted}

    if args.check:
        drifted = []
        for name in wanted:
            target = out_dir / FORMATS[name][0]
            if not target.is_file() or target.read_text(encoding="utf-8") != rendered[name]:
                drifted.append(target.name)
        if drifted:
            print(
                f"DRIFT: {', '.join(drifted)} is stale — run `stapel-storefront` "
                "and commit",
                file=sys.stderr,
            )
            return 1
        print("stapel-storefront: --check: up to date", file=sys.stderr)
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    for name in wanted:
        (out_dir / FORMATS[name][0]).write_text(rendered[name], encoding="utf-8")
    unpublished = [row["module"] for row in rows if not row["published"]]
    print(
        f"stapel-storefront: {len(rows)} libraries → "
        f"{', '.join(FORMATS[name][0] for name in wanted)} in {out_dir}"
        + (f" ({len(unpublished)} marked unreleased: {', '.join(unpublished)})"
           if unpublished else ""),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
