"""
stapel-readme — assemble ``README.md`` from a human-written static body plus
generated facts, instead of maintaining one hand-written monolith per repo.

Why
---
Every README in this fleet is written by hand, so every README rots, and it
rots in the part a reader trusts most: the numbers. A hand-typed version, a
hand-typed operation count, a hand-copied badge row and a hand-curated list of
links are all statements about artifacts that already exist in machine form —
``pyproject.toml`` and ``docs/{capabilities,schema,errors,flows}.json`` — and
restating them by hand is a promise to restate them again on every release.
Nobody does. The fleet has three tracker items about exactly this failure
class, one of them (#226) about hand-written ``capabilities.json`` files whose
version lagged ``pyproject.toml`` by several releases.

So a README is split in two, and only one half is written:

**static** — what the library is, why it exists, how to think about it. Lives
in ``docs/readme.md`` (and ``docs/readme.<lang>.md`` for other languages).
Written by a human, reviewed like prose, changed when the ideas change.

**generated** — the title, the badge row, the install line, the key facts
(version, surface size, dependencies), the links to flow/error/contract docs,
and the cross-links to READMEs in other languages. Assembled here from the
artifacts, never retyped.

``README.md`` is then an *artifact*: emitted by ``stapel-readme``, drift-gated
by ``stapel-readme --check`` next to ``make contract-check``, and regenerated
by the same release step that bumps the version.

Badges are the canon, and a badge may not lie
---------------------------------------------
The badge row is the fleet canon (``docs/pending/badge-canon.md`` §1.1), and
the canon's central rule is that **a badge that cannot be true is not
emitted**: a ``pypi`` badge on an unpublished package renders "package or
version not found", a ``coverage`` badge with no upload step renders
"unknown", and both are worse than no badge because a reader reads them as
facts. Every badge here therefore has a *precondition checked in the repo*:

===============  ==========================================================
badge            emitted when
===============  ==========================================================
CI               ``.github/workflows/ci.yml`` exists
coverage         ``codecov.yml`` exists AND ``ci.yml`` mentions codecov
pypi/downloads   the package is published (see below)
python           ``pyproject.toml`` declares ``Programming Language ::
                 Python :: 3.x`` classifiers
license          a ``LICENSE`` file exists
llms.txt         ``docs/llms.txt`` exists
status           *replaces* pypi/downloads when the package is unpublished
===============  ==========================================================

Publication is the one fact this tool cannot derive from the checkout: the
four unreleased modules carry the same ``ci.yml``, ``publish.yml``, tags and
CHANGELOG as the published ones — their release runs failed on the PyPI side.
It is therefore declared, in the file that already governs packaging::

    [tool.stapel.readme]
    pypi = false          # default true; false ⇒ status badge + source install

and ``--verify`` turns the declaration into a checked one (a network HEAD
against PyPI) for a CI job that is allowed to reach the internet.

Version drift is an error, not a rendering
------------------------------------------
``pyproject.toml`` is the source of truth for the version. If
``docs/capabilities.json`` disagrees with it, this tool FAILS rather than
picking one — that disagreement is #226 verbatim, and a generator that
silently prefers one input teaches the fleet that the other may rot.

Usage (a two-line shim in the module's Makefile)::

    readme:       python3 -m stapel_tools.readme .
    readme-check: python3 -m stapel_tools.readme . --check

Exit codes: 0 clean, 1 emission failure or ``--check`` drift, 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

#: The GitHub organisation every fleet repo lives in. Overridable per repo via
#: ``[tool.stapel.readme] org`` for a fork or a vendored checkout.
DEFAULT_ORG = "usestapel"

#: One line, shown under the tagline of every module: a reader who arrived at
#: one package should be one click from the whole. Generated, so the sentence
#: cannot drift between 26 repos the way 26 hand-copied ones did.
FRAMEWORK_BLURB = {
    "en": (
        "Part of the [Stapel framework](https://github.com/{org}) — composable "
        "Django apps that deploy as a monolith or as microservices without "
        "changing module code."
    ),
    "ru": (
        "Часть [фреймворка Stapel](https://github.com/{org}) — компонуемые "
        "Django-приложения, которые разворачиваются монолитом или "
        "микросервисами без изменения кода модулей."
    ),
}

#: Section chrome per language. A language with no entry falls back to English
#: chrome around its own static body — visibly (the reader sees English
#: headings) rather than silently, so a missing translation looks like the
#: missing work it is instead of a finished page.
CHROME = {
    "en": {
        "name": "English",
        "install": "Install",
        "at_a_glance": "At a glance",
        "fact": "Fact",
        "value": "Value",
        "version": "Version",
        "python": "Python",
        "django": "Django",
        "operations": "HTTP operations",
        "axes": "Config axes",
        "surface": "Usage surface",
        "extension_points": "Extension points",
        "errors": "Error codes",
        "flows": "Documented flows",
        "requires": "Fleet dependencies",
        "optional": "optional",
        "documentation": "Documentation",
        "flows_link": "Flows",
        "errors_link": "Errors",
        "contract_link": "OpenAPI",
        "agent_link": "llms.txt (for agents)",
        "capabilities_link": "capabilities.json",
        "read_in": "Read this in",
        "license": "License",
        "license_line": "{spdx} — see [LICENSE]({url}).",
        "unpublished": (
            "Not published on PyPI yet. Install from source:\n\n"
            "```bash\npip install git+https://github.com/{org}/{repo}\n```"
        ),
        "generated": (
            "This page is assembled by `stapel-readme` from `docs/readme.md` "
            "plus the contract artifacts in `docs/`. Edit the prose in "
            "`docs/readme.md`; the badges, facts and links above and below it "
            "are generated — do not hand-edit `README.md`."
        ),
    },
    "ru": {
        "name": "Русский",
        "install": "Установка",
        "at_a_glance": "Коротко",
        "fact": "Факт",
        "value": "Значение",
        "version": "Версия",
        "python": "Python",
        "django": "Django",
        "operations": "HTTP-операций",
        "axes": "Осей конфигурации",
        "surface": "Поверхность использования",
        "extension_points": "Точек расширения",
        "errors": "Кодов ошибок",
        "flows": "Задокументированных флоу",
        "requires": "Зависимости по флоту",
        "optional": "опционально",
        "documentation": "Документация",
        "flows_link": "Флоу",
        "errors_link": "Ошибки",
        "contract_link": "OpenAPI",
        "agent_link": "llms.txt (для агентов)",
        "capabilities_link": "capabilities.json",
        "read_in": "Читать на",
        "license": "Лицензия",
        "license_line": "{spdx} — см. [LICENSE]({url}).",
        "unpublished": (
            "Пакет ещё не опубликован на PyPI. Установка из исходников:\n\n"
            "```bash\npip install git+https://github.com/{org}/{repo}\n```"
        ),
        "generated": (
            "Страница собрана `stapel-readme` из `docs/readme.ru.md` и "
            "контрактных артефактов в `docs/`. Правьте прозу в "
            "`docs/readme.ru.md`; бэйджи, факты и ссылки генерируются — "
            "`README.ru.md` руками не редактировать."
        ),
    },
}

#: Human names for language codes in the "read this in" switch, so the switch
#: reads in the language it offers rather than in the language you are in.
LANGUAGE_NAMES = {
    "en": "English",
    "ru": "Русский",
    "es": "Español",
    "de": "Deutsch",
    "fr": "Français",
    "pt": "Português",
}

#: Marker line. HTML comments render nowhere (GitHub, PyPI) but are the first
#: thing anyone opening the file in an editor sees.
GENERATED_MARKER = (
    "<!-- Generated by stapel-readme from docs/readme{suffix}.md + docs/*.json. "
    "Do not edit this file; edit docs/readme{suffix}.md and re-run "
    "`make readme`. -->"
)

_HTTP_METHODS = ("get", "put", "post", "delete", "patch", "head", "options", "trace")


class EmitError(Exception):
    """A loud, actionable emission failure (missing input, contradictory facts)."""


# ── inputs ───────────────────────────────────────────────────────────────────


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - malformed input
        raise EmitError(f"{path}: not valid JSON — {exc}") from exc


def _optional_json(path: Path) -> Any:
    return _read_json(path) if path.is_file() else None


def load_pyproject(repo: Path) -> dict:
    """``pyproject.toml`` → the packaging facts, the source of truth for version.

    A repo without one is not a distributable library, and this generator has
    nothing to say about it — that is an error rather than a partial render,
    for the same reason ``llms_txt`` refuses to write an empty context file.
    """
    path = repo / "pyproject.toml"
    if not path.is_file():
        raise EmitError(
            f"{path} does not exist — stapel-readme assembles a *package* page "
            "(name, version, install line, classifiers all come from here). "
            "For a non-package repo, write README.md by hand."
        )
    with path.open("rb") as handle:
        return tomllib.load(handle)


def readme_config(pyproject: dict) -> dict:
    """``[tool.stapel.readme]`` — the few facts no checkout can prove.

    Deliberately tiny and deliberately *here*: a separate hand-written JSON
    file next to generated ones is the exact shape that rotted in #226. This
    lives in the file whose version bump is already part of every release.
    """
    return ((pyproject.get("tool") or {}).get("stapel") or {}).get("readme") or {}


def python_versions(pyproject: dict) -> list[str]:
    """Declared Python versions, read from classifiers (what the badge reads).

    The ``python`` badge renders ``pypi/pyversions``, which reads *classifiers*
    and not the CI matrix. Deriving the precondition from the same place the
    badge does is the only way to know in advance whether the badge will tell
    the truth.
    """
    classifiers = (pyproject.get("project") or {}).get("classifiers") or []
    prefix = "Programming Language :: Python :: "
    out = []
    for item in classifiers:
        if not item.startswith(prefix):
            continue
        tail = item[len(prefix):].strip()
        # Keep only real ``major.minor`` values. The family classifiers
        # ("3", "3 :: Only") are statements about the language, not versions
        # this package is tested on, and listing them reads as a fifth
        # supported version.
        parts = tail.split(".")
        if len(parts) == 2 and all(part.isdigit() for part in parts):
            out.append(tail)
    return sorted(set(out), key=lambda v: [int(p) for p in v.split(".") if p.isdigit()])


def django_requirement(pyproject: dict) -> str:
    """The Django floor from ``dependencies`` — a fact a Django-app page owes."""
    for dep in (pyproject.get("project") or {}).get("dependencies") or []:
        cleaned = dep.strip()
        if cleaned.lower().startswith("django") and not cleaned.lower().startswith("django-"):
            return cleaned
    return ""


def load_inputs(repo: Path) -> dict:
    """Everything the render reads, gathered once.

    ``docs/capabilities.json`` is optional here — unlike in ``llms_txt``. A
    module page without it loses the facts table and keeps the badges, the
    install line and the static prose, which is still a complete page; an
    ``llms.txt`` without it would be a confident empty answer to "does the
    fleet have a mechanism for X?". Different artifacts, different floors.
    """
    docs = repo / "docs"
    pyproject = load_pyproject(repo)
    return {
        "pyproject": pyproject,
        "config": readme_config(pyproject),
        "capabilities": _optional_json(docs / "capabilities.json"),
        "schema": _optional_json(docs / "schema.json"),
        "errors": _optional_json(docs / "errors.json"),
        "flows": _optional_json(docs / "flows.json"),
        "has_llms_txt": (docs / "llms.txt").is_file(),
        "flow_langs": _flow_doc_languages(docs / "flows"),
        "error_langs": _error_doc_languages(docs),
        "has_ci": (repo / ".github" / "workflows" / "ci.yml").is_file(),
        "has_codecov": _uploads_coverage(repo),
        "license": _license_spdx(repo),
    }


def _flow_doc_languages(flows_dir: Path) -> list[str]:
    if not flows_dir.is_dir():
        return []
    return sorted(
        child.name for child in flows_dir.iterdir()
        if child.is_dir() and (child / "README.md").is_file()
    )


def _error_doc_languages(docs: Path) -> list[str]:
    if not docs.is_dir():
        return []
    out = []
    for child in docs.glob("errors.*.md"):
        lang = child.name[len("errors."):-len(".md")]
        if lang:
            out.append(lang)
    return sorted(out)


def _uploads_coverage(repo: Path) -> bool:
    """Precondition for the coverage badge: a config AND an actual upload step.

    Both halves matter. ``codecov.yml`` alone configures a service nothing
    talks to, and a codecov action with no ``codecov.yml`` is the shape that
    reported ``unknown`` for stapel-tools while its README claimed otherwise.
    """
    if not (repo / "codecov.yml").is_file():
        return False
    ci = repo / ".github" / "workflows" / "ci.yml"
    if not ci.is_file():
        return False
    return "codecov" in ci.read_text(encoding="utf-8", errors="replace").lower()


def _license_spdx(repo: Path) -> str:
    """The SPDX id if a LICENSE file is actually present, else "".

    Read from the file rather than from ``pyproject``: the badge renders
    ``github/license``, which looks at the file. A ``license = {text = "MIT"}``
    with no file renders ``not specified``.
    """
    path = repo / "LICENSE"
    if not path.is_file():
        return ""
    head = path.read_text(encoding="utf-8", errors="replace")[:400].upper()
    for spdx, needle in (
        ("MIT", "MIT LICENSE"),
        ("Apache-2.0", "APACHE LICENSE"),
        ("BSD-3-Clause", "BSD 3-CLAUSE"),
        ("GPL-3.0", "GNU GENERAL PUBLIC LICENSE"),
    ):
        if needle in head:
            return spdx
    return "See LICENSE"


# ── derived facts ────────────────────────────────────────────────────────────


def operation_count(schema: Any, capabilities: Any) -> int:
    """How many HTTP operations the module mounts.

    Counted from ``schema.json`` when it is there (the artifact the client
    generator and the docs are built from), falling back to the
    ``operations_total`` roll-up in ``capabilities.json`` for modules that
    ship the roll-up without the OpenAPI document.
    """
    if schema:
        # Membership, not truthiness: an operation object may legitimately be
        # empty (``{"get": {}}`` is a valid, if terse, OpenAPI operation), and
        # a count that silently skips those under-reports the surface.
        return sum(
            1
            for item in (schema.get("paths") or {}).values()
            for method in _HTTP_METHODS
            if method in item
        )
    if capabilities:
        return int(capabilities.get("operations_total") or 0)
    return 0


def resolve_version(inputs: dict) -> str:
    """The version, with the #226 gate built in.

    ``pyproject.toml`` wins because it is what gets published. A
    ``capabilities.json`` that disagrees is *not* rendered around — it is the
    defect (#226: three modules shipped a capabilities version several
    releases behind), and a generator that quietly prefers the fresher input
    would hide it forever.
    """
    project = inputs["pyproject"].get("project") or {}
    version = project.get("version")
    if not version:
        raise EmitError(
            "pyproject.toml has no [project] version — nothing to state. "
            "(Dynamic versions are not supported: this page is generated "
            "offline, from files, and cannot import the package.)"
        )
    capabilities = inputs.get("capabilities")
    if capabilities:
        declared = capabilities.get("version")
        if declared and declared != version:
            raise EmitError(
                f"version drift: pyproject.toml says {version}, "
                f"docs/capabilities.json says {declared}.\n"
                "  Nothing was written. This is tracker #226 verbatim — a "
                "hand-maintained capabilities.json lagging the package it "
                "describes.\n"
                "  Fix the contract artifact (`make contract`) or the "
                "pyproject version; stapel-readme will not pick a winner for "
                "you, because whichever it picked would make the other free "
                "to rot."
            )
    return str(version)


def is_published(inputs: dict) -> bool:
    """Whether pypi/downloads badges may be emitted (declared, canon §4.1)."""
    config = inputs["config"]
    if "pypi" in config:
        return bool(config["pypi"])
    return True


def repo_slug(inputs: dict) -> tuple[str, str]:
    """``(org, repo)`` — the coordinates every badge and doc link is built from."""
    config = inputs["config"]
    project = inputs["pyproject"].get("project") or {}
    name = project.get("name") or ""
    if not name:
        raise EmitError("pyproject.toml has no [project] name.")
    return str(config.get("org") or DEFAULT_ORG), str(config.get("repo") or name)


def blob_url(org: str, repo: str, path: str) -> str:
    """Absolute link into the repo's main branch.

    Absolute, not relative, on purpose: this same text is the PyPI long
    description, where every relative link is a 404. The fleet's current
    hand-written READMEs all ship broken links on PyPI for this reason.
    """
    return f"https://github.com/{org}/{repo}/blob/main/{path}"


# ── badges (docs/pending/badge-canon.md §1.1) ────────────────────────────────


def badges(inputs: dict) -> list[str]:
    """The canonical badge row, minus every badge whose precondition fails.

    Order is fixed by the canon: health → identity → compatibility → agent.
    The lines are contiguous (no blank line between them) so GitHub renders
    them as one row.
    """
    org, repo = repo_slug(inputs)
    shields = "https://img.shields.io"
    out: list[str] = []
    if inputs["has_ci"]:
        out.append(
            f"[![CI]({shields}/github/actions/workflow/status/{org}/{repo}/ci.yml"
            f"?branch=main&logo=github&label=CI)]"
            f"(https://github.com/{org}/{repo}/actions/workflows/ci.yml"
            f"?query=branch%3Amain)"
        )
    if inputs["has_codecov"]:
        out.append(
            f"[![coverage]({shields}/codecov/c/github/{org}/{repo}"
            f"?branch=main&logo=codecov&label=coverage)]"
            f"(https://app.codecov.io/gh/{org}/{repo})"
        )
    if is_published(inputs):
        out.append(
            f"[![pypi]({shields}/pypi/v/{repo}?logo=pypi&logoColor=white&label=pypi)]"
            f"(https://pypi.org/project/{repo}/)"
        )
        out.append(
            f"[![downloads](https://static.pepy.tech/badge/{repo}/month)]"
            f"(https://pepy.tech/project/{repo})"
        )
    else:
        # Canon §4.1: say "unreleased" out loud rather than render a broken
        # pypi badge — the reader learns the true state either way, and only
        # one of the two is honest about it.
        out.append(
            f"[![status]({shields}/badge/status-unreleased-orange)]"
            f"(https://github.com/{org}/{repo})"
        )
    if is_published(inputs) and python_versions(inputs["pyproject"]):
        out.append(
            f"[![python]({shields}/pypi/pyversions/{repo}?logo=python&logoColor=white)]"
            f"(https://pypi.org/project/{repo}/)"
        )
    if inputs["license"]:
        out.append(
            f"[![license]({shields}/github/license/{org}/{repo})]"
            f"({blob_url(org, repo, 'LICENSE')})"
        )
    if inputs["has_llms_txt"]:
        out.append(
            f"[![llms.txt]({shields}/badge/llms.txt-blue)]"
            f"({blob_url(org, repo, 'docs/llms.txt')})"
        )
    return out


# ── rendering ────────────────────────────────────────────────────────────────


def _chrome(lang: str) -> dict:
    return CHROME.get(lang) or CHROME["en"]


def render_facts(inputs: dict, lang: str) -> list[str]:
    """The "at a glance" table — every cell read from an artifact.

    Only non-zero rows are emitted: a thin module that mounts no HTTP surface
    should not advertise "HTTP operations: 0", which reads as a defect rather
    than as a design.
    """
    c = _chrome(lang)
    capabilities = inputs.get("capabilities") or {}
    rows: list[tuple[str, str]] = [(c["version"], f"`{resolve_version(inputs)}`")]

    project = inputs["pyproject"].get("project") or {}
    requires_python = project.get("requires-python")
    versions = python_versions(inputs["pyproject"])
    if requires_python or versions:
        detail = f"`{requires_python}`" if requires_python else ""
        if versions:
            detail += (" (" + ", ".join(versions) + ")") if detail else ", ".join(versions)
        rows.append((c["python"], detail))
    django = django_requirement(inputs["pyproject"])
    if django:
        rows.append((c["django"], f"`{django}`"))

    counts = (
        (c["operations"], operation_count(inputs.get("schema"), capabilities)),
        (c["axes"], len(capabilities.get("axes") or [])),
        (c["surface"], len(capabilities.get("surface") or [])),
        (c["extension_points"], len(capabilities.get("extension_points") or [])),
        (c["errors"], len(inputs.get("errors") or [])),
        (c["flows"], len(inputs.get("flows") or [])),
    )
    for label, count in counts:
        if count:
            rows.append((label, str(count)))

    org, _ = repo_slug(inputs)
    requires = capabilities.get("requires") or []
    if requires:
        parts = []
        for req in sorted(requires, key=lambda r: r["module"]):
            link = f"[`{req['module']}`](https://github.com/{org}/{req['module']})"
            parts.append(f"{link} ({c['optional']})" if req.get("optional") else link)
        rows.append((c["requires"], " · ".join(parts)))

    lines = [f"## {c['at_a_glance']}", "", f"| {c['fact']} | {c['value']} |", "|---|---|"]
    lines += [f"| {label} | {value} |" for label, value in rows]
    return lines


def render_install(inputs: dict, lang: str) -> list[str]:
    c = _chrome(lang)
    org, repo = repo_slug(inputs)
    lines = [f"## {c['install']}", ""]
    if is_published(inputs):
        lines += ["```bash", f"pip install {repo}", "```"]
    else:
        lines += c["unpublished"].format(org=org, repo=repo).split("\n")
    return lines


def render_doc_links(inputs: dict, lang: str) -> list[str]:
    """One line of links to every artifact the repo actually ships.

    Flow docs are linked *per language on one line* rather than through a
    language picker page: the picker was a level of hierarchy that carried no
    information (see ``docs/flows/<lang>/README.md``, which now also carries
    each flow's description inline, so the list itself is the content).
    """
    c = _chrome(lang)
    org, repo = repo_slug(inputs)
    parts: list[str] = []

    if inputs["flow_langs"]:
        langs = " · ".join(
            f"[{LANGUAGE_NAMES.get(fl, fl)}]"
            f"({blob_url(org, repo, f'docs/flows/{fl}/README.md')})"
            for fl in inputs["flow_langs"]
        )
        parts.append(f"**{c['flows_link']}:** {langs}")
    if inputs["error_langs"]:
        langs = " · ".join(
            f"[{LANGUAGE_NAMES.get(el, el)}]"
            f"({blob_url(org, repo, f'docs/errors.{el}.md')})"
            for el in inputs["error_langs"]
        )
        parts.append(f"**{c['errors_link']}:** {langs}")
    if inputs.get("schema"):
        parts.append(f"[{c['contract_link']}]({blob_url(org, repo, 'docs/schema.json')})")
    if inputs.get("capabilities"):
        parts.append(
            f"[{c['capabilities_link']}]({blob_url(org, repo, 'docs/capabilities.json')})"
        )
    if inputs["has_llms_txt"]:
        parts.append(f"[{c['agent_link']}]({blob_url(org, repo, 'docs/llms.txt')})")

    if not parts:
        return []
    return [f"## {c['documentation']}", "", " · ".join(parts)]


def render_language_switch(lang: str, languages: list[str]) -> list[str]:
    """"Read this in" — the other-language READMEs, as a switch, not a page."""
    if len(languages) < 2:
        return []
    c = _chrome(lang)
    parts = []
    for other in languages:
        name = LANGUAGE_NAMES.get(other, other)
        if other == lang:
            parts.append(f"**{name}**")
        else:
            parts.append(f"[{name}]({readme_filename(other)})")
    return [f"{c['read_in']}: " + " · ".join(parts)]


def render_license(inputs: dict, lang: str) -> list[str]:
    if not inputs["license"]:
        return []
    c = _chrome(lang)
    org, repo = repo_slug(inputs)
    return [
        f"## {c['license']}",
        "",
        c["license_line"].format(spdx=inputs["license"], url=blob_url(org, repo, "LICENSE")),
    ]


def static_body(repo_root: Path, lang: str) -> str:
    """The human half. Missing is an error naming the migration.

    A generated shell with no prose in it would be a page that says what the
    library *has* and never what it *is* — which is the half a reader came
    for, and the half no artifact can supply.
    """
    path = static_path(repo_root, lang)
    if not path.is_file():
        raise EmitError(
            f"{path} does not exist — the static half of the page is missing.\n"
            "  stapel-readme assembles README.md from generated facts (badges, "
            "version, surface, links) PLUS human prose (what this library is, "
            "why it exists, how to think about it).\n"
            "  Migrate an existing hand-written README once: move its prose "
            f"body into {path.relative_to(repo_root)} (drop the title, the "
            "badge row, the install snippet and the license footer — all four "
            "are generated), then run this again."
        )
    text = path.read_text(encoding="utf-8").strip("\n")
    if text.startswith("# "):
        raise EmitError(
            f"{path} starts with a level-1 heading — the title is generated "
            "from the package name and would be duplicated. Start the static "
            "body at `## ` (or plain prose)."
        )
    return text


def static_path(repo_root: Path, lang: str) -> Path:
    suffix = "" if lang == "en" else f".{lang}"
    return repo_root / "docs" / f"readme{suffix}.md"


def readme_filename(lang: str) -> str:
    return "README.md" if lang == "en" else f"README.{lang}.md"


def static_languages(repo_root: Path) -> list[str]:
    """Which languages have a static body — English first, then alphabetical."""
    docs = repo_root / "docs"
    langs = []
    if (docs / "readme.md").is_file():
        langs.append("en")
    if docs.is_dir():
        for child in sorted(docs.glob("readme.*.md")):
            lang = child.name[len("readme."):-len(".md")]
            if lang and lang != "en":
                langs.append(lang)
    return langs


def render(repo_root: Path, inputs: dict, lang: str, languages: list[str]) -> str:
    """Assemble one language's page: generated head, prose, generated foot."""
    c = _chrome(lang)
    org, repo = repo_slug(inputs)
    capabilities = inputs.get("capabilities") or {}
    suffix = "" if lang == "en" else f".{lang}"

    blocks: list[list[str]] = [
        [GENERATED_MARKER.format(suffix=suffix)],
        [f"# {repo}"],
        badges(inputs),
    ]

    provides = " ".join((capabilities.get("provides") or "").split())
    if provides:
        blocks.append([f"> {provides}"])
    blocks.append([FRAMEWORK_BLURB.get(lang, FRAMEWORK_BLURB["en"]).format(org=org)])

    switch = render_language_switch(lang, languages)
    if switch:
        blocks.append(switch)

    blocks.append(render_install(inputs, lang))
    if capabilities or inputs.get("schema"):
        blocks.append(render_facts(inputs, lang))
    doc_links = render_doc_links(inputs, lang)
    if doc_links:
        blocks.append(doc_links)

    blocks.append([static_body(repo_root, lang)])

    license_block = render_license(inputs, lang)
    if license_block:
        blocks.append(license_block)
    blocks.append(["---", "", f"<sub>{c['generated']}</sub>"])

    rendered = "\n\n".join("\n".join(block) for block in blocks if block)
    return rendered.rstrip("\n") + "\n"


# ── verification (opt-in, network) ───────────────────────────────────────────


def verify_published(repo: str, timeout: float = 10.0) -> bool:
    """Is ``repo`` actually on PyPI? Only called by ``--verify``.

    Kept out of the default path deliberately: generation must be hermetic and
    byte-stable, or the drift gate turns into a network flake. This is the
    separate, explicit check that keeps the ``[tool.stapel.readme] pypi``
    declaration from becoming another hand-maintained lie.
    """
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(
            f"https://pypi.org/pypi/{repo}/json", timeout=timeout
        ) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise EmitError(f"PyPI check for {repo} failed: HTTP {exc.code}") from exc
    except OSError as exc:
        raise EmitError(f"PyPI check for {repo} failed: {exc}") from exc


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-readme",
        description="Assemble README.md from docs/readme.md (human prose) plus "
        "the contract artifacts in docs/ (badges, version, surface, links). "
        "Deterministic and drift-gated — README.md is an artifact, not a "
        "hand-maintained file.",
    )
    parser.add_argument("repo", nargs="?", default=".", help="Module repo root.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Drift gate: render in memory and compare byte for byte against "
        "the committed README(s); nonzero exit and no write on any drift.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Also check the [tool.stapel.readme] pypi declaration against the "
        "live PyPI index. Needs network; keep it in a separate CI job, never "
        "in the hermetic drift gate.",
    )
    parser.add_argument(
        "--lang",
        action="append",
        default=None,
        help="Render only this language (repeatable). Default: every language "
        "with a docs/readme*.md static body.",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Write the render to stdout instead of files (no drift gate).",
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo).resolve()
    try:
        inputs = load_inputs(repo_root)
        languages = static_languages(repo_root)
        if not languages:
            raise EmitError(
                f"{repo_root / 'docs' / 'readme.md'} does not exist — no static "
                "body to assemble a page around. See the migration note in "
                "`stapel-readme --help` / the module docstring."
            )
        selected = args.lang or languages
        unknown = [lang for lang in selected if lang not in languages]
        if unknown:
            raise EmitError(
                f"no static body for language(s) {', '.join(unknown)}: expected "
                + ", ".join(str(static_path(repo_root, lang)) for lang in unknown)
            )
        if args.verify:
            _, repo_name = repo_slug(inputs)
            declared = is_published(inputs)
            actual = verify_published(repo_name)
            if declared != actual:
                raise EmitError(
                    f"[tool.stapel.readme] pypi = {str(declared).lower()} in "
                    f"pyproject.toml, but PyPI says the package "
                    f"{'exists' if actual else 'does not exist'}.\n"
                    "  Badges must not lie: fix the declaration (or publish "
                    "the package) before regenerating."
                )
        rendered = {
            lang: render(repo_root, inputs, lang, languages) for lang in selected
        }
    except EmitError as exc:
        print(f"stapel-readme: {exc}", file=sys.stderr)
        return 1

    if args.stdout:
        for lang in selected:
            sys.stdout.write(rendered[lang])
        return 0

    if args.check:
        drifted = []
        for lang in selected:
            target = repo_root / readme_filename(lang)
            if not target.is_file() or target.read_text(encoding="utf-8") != rendered[lang]:
                drifted.append(target.name)
        if drifted:
            print(
                f"DRIFT: {', '.join(drifted)} is stale — run `make readme` "
                "(python3 -m stapel_tools.readme .) and commit",
                file=sys.stderr,
            )
            return 1
        print(
            f"stapel-readme: --check: {', '.join(readme_filename(x) for x in selected)} "
            "up to date",
            file=sys.stderr,
        )
        return 0

    for lang in selected:
        (repo_root / readme_filename(lang)).write_text(rendered[lang], encoding="utf-8")
    print(
        f"stapel-readme: wrote {', '.join(readme_filename(x) for x in selected)} "
        f"in {repo_root}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
