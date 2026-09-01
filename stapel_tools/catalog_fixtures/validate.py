"""The gate over an emitted catalogue/vocabulary fixture.

A fixture is a file some importer wrote and some loader will apply, hours or
weeks apart and on another machine. Everything this module checks is a
property the loader depends on and the importer can silently break:

VOC001  the file is not valid JSON, or does not match the vocabulary-fixture
        schema beside this module.
VOC002  two terms of one level carry the same code. A term code is the
        identity a facet, a rule and a listing's stored value all address, so
        this is a defect in the emitter, never a fixture to ship — the build
        stops here rather than letting the loader reject the file on the stand,
        26 000 rows in.
VOC003  a level's ``parent`` is not a level declared before it. Levels are an
        ordered chain: a forward or unknown parent has no meaning.
VOC004  the terms are not in canonical order — ``(level index, code)``.
VOC005  an edge names a term that the fixture does not declare.
VOC006  an edge joins two levels that are not a declared parent/child pair.
VOC007  the edges are not sorted, or one is repeated.
VOC008  the file's bytes are not what :func:`writer.canonical_json` produces
        for its own content — so a re-export diffs against a byte-identical
        fixture, and every regeneration is a churn commit.

CAT001  a catalogue fixture (``features.json`` / ``categories.json``) is not
        valid JSON or not canonical bytes. Its INNER shape belongs to
        ``stapel-categories``' loader, which owns that contract; the byte
        stability is this gate's business because it is what makes the file
        reviewable.

:func:`assert_unique_codes` is VOC002 as a raising call, for an importer that
wants to fail during the build rather than after writing the file.
"""

from __future__ import annotations

import collections
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .writer import CATALOG_DIRNAME, VOCABULARY_DIRNAME, canonical_json

SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "vocabulary-fixture.schema.json"

VOCABULARY_KEYS = frozenset({"slug", "name", "source", "levels", "terms", "edges"})
REQUIRED_KEYS = frozenset({"slug", "name", "levels", "terms", "edges"})


class VocabularyCodeCollision(ValueError):
    """Two terms of one level were given the same code (VOC002)."""


@dataclass
class Finding:
    path: str
    rule: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: [{self.rule}] {self.message}"

    def to_dict(self) -> dict:
        return {"path": self.path, "rule": self.rule, "message": self.message}


def load_schema() -> dict:
    """The vocabulary-fixture JSON Schema this package ships."""
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def collisions(terms) -> list:
    """``[((level, code), [labels])]`` for every code handed to two terms."""
    by_code = collections.defaultdict(list)
    for row in terms:
        level, code, label = row[0], row[1], row[2]
        by_code[(level, code)].append(label)
    return sorted((key, labels) for key, labels in by_code.items() if len(labels) > 1)


def _collision_message(slug, found, total) -> str:
    shown = ["%s:%s = %s" % (level, code, ", ".join(repr(label) for label in labels))
             for (level, code), labels in found[:5]]
    return ("%s: %d duplicate term code%s out of %d terms (%s%s)"
            % (slug, len(found), "" if len(found) == 1 else "s", total,
               "; ".join(shown), ", …" if len(found) > 5 else ""))


def assert_unique_codes(slug, terms) -> None:
    """Raise :class:`VocabularyCodeCollision` if any ``(level, code)`` repeats."""
    found = collisions(terms)
    if found:
        raise VocabularyCodeCollision(_collision_message(slug, found, len(terms)))


def _schema_findings(path: str, fixture) -> list[Finding]:
    """VOC001 through the schema, when ``jsonschema`` is installed.

    Without it the structural checks below still run, and they cover the
    invariants a loader actually trips over; the schema adds the field-level
    types and lengths. The import is optional so the package keeps no runtime
    dependency, and the test extra declares it so CI really takes this branch.
    """
    try:
        import jsonschema
    except ImportError:                                     # pragma: no cover
        return []
    validator = jsonschema.Draft202012Validator(load_schema())
    return [Finding(path, "VOC001", "%s: %s" % ("/".join(str(p) for p in error.path)
                                                or "<root>", error.message))
            for error in sorted(validator.iter_errors(fixture), key=lambda e: list(e.path))]


def validate_vocabulary(fixture, path: str = "<fixture>") -> list[Finding]:
    """Every VOC finding for one already-parsed vocabulary fixture."""
    findings: list[Finding] = []
    if not isinstance(fixture, dict):
        return [Finding(path, "VOC001", "a vocabulary fixture is a JSON object")]
    missing = sorted(REQUIRED_KEYS - set(fixture))
    if missing:
        return [Finding(path, "VOC001", "missing key(s): %s" % ", ".join(missing))]
    extra = sorted(set(fixture) - VOCABULARY_KEYS)
    if extra:
        findings.append(Finding(path, "VOC001", "unknown key(s): %s" % ", ".join(extra)))
    findings.extend(_schema_findings(path, fixture))

    levels = fixture["levels"]
    names = [level.get("name") for level in levels]
    index = {}
    for position, level in enumerate(levels):
        name = level.get("name")
        parent = level.get("parent")
        if parent is not None and parent not in index:
            findings.append(Finding(
                path, "VOC003",
                "level %r names parent %r, which is not a level declared before it"
                % (name, parent)))
        index[name] = position
    parent_of = {level.get("name"): level.get("parent") for level in levels}

    terms = fixture["terms"]
    for row in terms:
        if row[0] not in index:
            findings.append(Finding(path, "VOC001",
                                    "term %r sits on undeclared level %r" % (row[1], row[0])))
    found = collisions(terms)
    if found:
        findings.append(Finding(path, "VOC002",
                                _collision_message(fixture["slug"], found, len(terms))))
    ordered = sorted(terms, key=lambda row: (index.get(row[0], len(names)), row[1]))
    if terms != ordered:
        findings.append(Finding(
            path, "VOC004",
            "terms are not in canonical (level index, code) order — a fixture "
            "whose order depends on the emitter's iteration is not reviewable"))

    known = {(row[0], row[1]) for row in terms}
    edges = fixture["edges"]
    for edge in edges:
        parent_level, parent_code, child_level, child_code = edge
        for level, code in ((parent_level, parent_code), (child_level, child_code)):
            if (level, code) not in known:
                findings.append(Finding(
                    path, "VOC005",
                    "edge %s -> %s names %s:%s, which is not a declared term"
                    % (parent_code, child_code, level, code)))
        if child_level in parent_of and parent_of[child_level] != parent_level:
            findings.append(Finding(
                path, "VOC006",
                "edge joins %r to %r, but %r's declared parent is %r"
                % (parent_level, child_level, child_level, parent_of[child_level])))
    if edges != sorted(edges):
        findings.append(Finding(path, "VOC007", "edges are not sorted"))
    if len({tuple(edge) for edge in edges}) != len(edges):
        findings.append(Finding(path, "VOC007", "an edge is repeated"))
    return findings


def _canonical_findings(path: Path, rule: str) -> tuple[list[Finding], object]:
    """Parse a fixture file and check its bytes are the canonical rendering."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [Finding(str(path), rule, "unreadable: %s" % exc)], None
    try:
        data = json.loads(text)
    except ValueError as exc:
        return [Finding(str(path), rule, "not valid JSON: %s" % exc)], None
    if canonical_json(data) != text:
        return [Finding(
            str(path), "VOC008" if rule == "VOC001" else rule,
            "not canonical bytes (sorted keys, indent=2, ensure_ascii=False, "
            "trailing newline) — a re-export would diff against it")], data
    return [], data


def validate_file(path) -> list[Finding]:
    """Every finding for one vocabulary fixture FILE."""
    path = Path(path)
    findings, data = _canonical_findings(path, "VOC001")
    if data is None:
        return findings
    return findings + validate_vocabulary(data, str(path))


def validate_catalog_file(path) -> list[Finding]:
    """CAT001 for one ``features.json`` / ``categories.json``."""
    findings, _ = _canonical_findings(Path(path), "CAT001")
    return findings


def validate_tree(root, notes: Optional[list] = None) -> list[Finding]:
    """Validate an importer's whole output directory.

    ``<root>/vocabularies/*.json`` are vocabulary fixtures; ``<root>/catalog/``
    holds the catalogue half. A *root* that is itself one of those two
    directories is accepted too, so a fleet can gate just the half it commits.
    """
    root = Path(root)
    findings: list[Finding] = []
    seen = 0
    for directory, check in ((root / VOCABULARY_DIRNAME, validate_file),
                             (root / CATALOG_DIRNAME, validate_catalog_file)):
        if directory.is_dir():
            for path in sorted(directory.glob("*.json")):
                seen += 1
                findings.extend(check(path))
    if not seen:
        check = validate_catalog_file if root.name == CATALOG_DIRNAME else validate_file
        for path in sorted(root.glob("*.json")):
            seen += 1
            findings.extend(check(path))
    if not seen and notes is not None:
        notes.append("stapel-fixture-lint: no fixture files under %s — nothing checked" % root)
    return findings


def iter_fixture_files(root) -> Iterable[Path]:
    """The files :func:`validate_tree` would read, in the order it reads them."""
    root = Path(root)
    for name in (VOCABULARY_DIRNAME, CATALOG_DIRNAME):
        directory = root / name
        if directory.is_dir():
            yield from sorted(directory.glob("*.json"))
    if not any((root / name).is_dir() for name in (VOCABULARY_DIRNAME, CATALOG_DIRNAME)):
        yield from sorted(root.glob("*.json"))


__all__ = [
    "CATALOG_DIRNAME", "VOCABULARY_DIRNAME", "Finding", "VocabularyCodeCollision",
    "assert_unique_codes", "collisions", "iter_fixture_files", "load_schema",
    "validate_catalog_file", "validate_file", "validate_tree", "validate_vocabulary",
]
