"""Writing the fixture files an importer produces.

``catalog/{features,categories}.json`` follow ``stapel-categories``'
``catalog_fixtures.canonical_json`` byte-for-byte (sorted keys, ``indent=2``,
``ensure_ascii=False``, trailing newline) so ``load_catalog`` can apply them and
a re-export diffs to nothing. ``vocabularies/<slug>.json`` follows the
vocabulary-fixture schema beside this module.

Byte-stability is the whole point: fixtures are reviewed as code, and two runs
of the same importer over the same input must produce files a ``diff`` calls
identical — otherwise every regeneration is an unreadable churn commit.
"""

from __future__ import annotations

import json
import os

FEATURES_FILE = "features.json"
CATEGORIES_FILE = "categories.json"
CATALOG_DIRNAME = "catalog"
VOCABULARY_DIRNAME = "vocabularies"


def canonical_json(obj) -> str:
    """Byte-stable JSON text for a fixture file (trailing newline included)."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def write_catalog(out_dir, features, categories):
    """``<out>/catalog/features.json`` + ``categories.json``."""
    directory = os.path.join(out_dir, CATALOG_DIRNAME)
    write_text(os.path.join(directory, FEATURES_FILE), canonical_json(features))
    write_text(os.path.join(directory, CATEGORIES_FILE), canonical_json(categories))
    return directory


def write_vocabularies(out_dir, vocabularies):
    """One ``<out>/vocabularies/<slug>.json`` per vocabulary."""
    directory = os.path.join(out_dir, VOCABULARY_DIRNAME)
    written = []
    for fixture in vocabularies:
        path = os.path.join(directory, "%s.json" % fixture["slug"])
        write_text(path, canonical_json(fixture))
        written.append(path)
    return written
