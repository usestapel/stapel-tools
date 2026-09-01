"""The fixture contract shared by every catalogue/vocabulary importer.

An importer turns some external classified's schema into two kinds of file —
a catalogue half (``catalog/features.json``, ``catalog/categories.json``) and a
vocabulary half (``vocabularies/<slug>.json``) — which the loaders in
``stapel-categories`` and ``stapel-vocabularies`` apply. The importers
themselves are source-specific and belong to whoever owns the source; the parts
below are the contract they all share and none of them should re-invent:

* :mod:`~stapel_tools.catalog_fixtures.slug` — the deterministic slug, term-code
  and dedup rules, so the same label yields the same identity on every run;
* :mod:`~stapel_tools.catalog_fixtures.writer` — the byte-stable fixture writer;
* :mod:`~stapel_tools.catalog_fixtures.validate` — the gate over what was
  emitted (``stapel-fixture-lint``), including the duplicate-``(level, code)``
  check that must stop a build rather than reach a loader;
* ``schemas/vocabulary-fixture.schema.json`` — the vocabulary fixture's shape.
"""

from .slug import dedup, feature_slug, path_hash, slugify, transliterate, vocabulary_slug
from .validate import (
    VocabularyCodeCollision,
    assert_unique_codes,
    validate_file,
    validate_tree,
    validate_vocabulary,
)
from .writer import canonical_json, write_catalog, write_vocabularies

__all__ = [
    "VocabularyCodeCollision", "assert_unique_codes", "canonical_json", "dedup",
    "feature_slug", "path_hash", "slugify", "transliterate", "validate_file",
    "validate_tree", "validate_vocabulary", "vocabulary_slug", "write_catalog",
    "write_vocabularies",
]
