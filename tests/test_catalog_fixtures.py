"""Tests for ``stapel_tools.catalog_fixtures`` — the fixture contract itself.

The importers that produce these files are source-specific and live with
whoever owns the source. What is here is the part every one of them shares and
none of them should re-invent: the slug/dedup rules, the byte-stable writer and
the gate over what was emitted (``stapel-fixture-lint``).

The data below is invented for the tests. It has to be: a fixture in a public
tree is published, so the shapes are real and the content is not.
"""

import json
import os
import subprocess
import sys

import pytest

from stapel_tools.catalog_fixtures import slug, validate, writer
from stapel_tools.catalog_fixtures.cli import main

# --------------------------------------------------------------------------- slug

def test_transliteration_is_the_fixed_table():
    assert slug.slugify("Б/у") == "b-u"
    assert slug.slugify("Зависит от количества") == "zavisit-ot-kolichestva"
    assert slug.slugify("Ёлки-палки") == "elki-palki"
    assert slug.slugify("Citroën C4") == "citroen-c4"


def test_slugify_truncates_without_a_trailing_separator():
    assert slug.slugify("alpha beta gamma", max_length=10) == "alpha-beta"
    # The cut lands on the separator; the result must not end in one.
    assert slug.slugify("alpha beta gamma", max_length=11) == "alpha-beta"


def test_feature_slug_splits_camel_case():
    assert slug.feature_slug("WholesaleMinOrderType") == "wholesale_min_order_type"
    assert slug.feature_slug("VRamSize") == "v_ram_size"
    assert slug.feature_slug("OEM") == "oem"
    assert slug.feature_slug("Марка") == "marka"


def test_term_codes_are_deduplicated_in_order():
    assert slug.dedup(["a", "b", "a", "a"]) == ["a", "b", "a-2", "a-3"]


def test_a_dedup_suffix_never_takes_a_code_another_label_owns():
    # Trim levels named 'Exclusive', 'Exclusive 2' and 'Exclusive+': the first
    # and third both slugify to `exclusive`, and `exclusive-2` is NOT free
    # because the second is a term in its own right.
    assert slug.dedup(["exclusive", "exclusive-2", "exclusive"]) == [
        "exclusive", "exclusive-2", "exclusive-3"]
    # …and the suffix walks past every code the data itself claims, in either
    # direction ('Premium', 'Premium +', 'Premium 2', 'Premium 3', 'Premium+').
    assert slug.dedup(["premium", "premium", "premium-2", "premium-3", "premium"]) == [
        "premium", "premium-4", "premium-2", "premium-3", "premium-5"]


def test_vocabulary_slug_from_a_source_file_name():
    assert slug.vocabulary_slug("phone_catalog.xml") == "phone-catalog"
    assert slug.vocabulary_slug("phone_catalog") == "phone-catalog"


def test_a_prefix_namespaces_one_source_s_vocabularies():
    """Two sources shipping a ``brands.xml`` must not fight over one slug."""
    assert slug.vocabulary_slug("brands.xml", prefix="alpha") == "alpha-brands"
    assert slug.vocabulary_slug("brands.xml", prefix="beta") == "beta-brands"
    # The prefix is charged to the caller, not to the 64-character schema cap:
    # the stem budget is the argument, so a prefix can never push the slug over.
    long = slug.vocabulary_slug("x" * 200, prefix="alpha")
    assert len(long) == len("alpha-") + slug.DEFAULT_STEM_LENGTH <= 64


def test_path_hash_is_stable_and_short():
    assert slug.path_hash(["a", "b"]) == slug.path_hash(["a", "b"])
    assert slug.path_hash(["a", "b"]) != slug.path_hash(["a", "c"])
    assert len(slug.path_hash(["a", "b"])) == 8


# ------------------------------------------------------------------------- writer

def test_canonical_json_is_sorted_indented_unicode_and_newline_terminated():
    text = writer.canonical_json({"b": 1, "a": "щётка"})
    assert text == '{\n  "a": "щётка",\n  "b": 1\n}\n'


def test_the_writer_lays_out_both_halves(tmp_path):
    features = [{"slug": "colour"}]
    categories = [{"slug": "root"}]
    writer.write_catalog(str(tmp_path), features, categories)
    written = writer.write_vocabularies(str(tmp_path), [_fixture()])
    assert sorted(os.listdir(tmp_path / "catalog")) == ["categories.json", "features.json"]
    assert [os.path.basename(path) for path in written] == ["brands.json"]
    assert json.loads((tmp_path / "catalog" / "features.json").read_text()) == features


def test_two_writes_of_the_same_content_are_byte_identical(tmp_path):
    first = tmp_path / "one"
    second = tmp_path / "two"
    for out in (first, second):
        writer.write_vocabularies(str(out), [_fixture()])
    assert ((first / "vocabularies" / "brands.json").read_bytes()
            == (second / "vocabularies" / "brands.json").read_bytes())


# ----------------------------------------------------------------------- the gate

def _fixture(**overrides):
    """A minimal well-formed vocabulary: two levels, three terms, two edges."""
    fixture = {
        "slug": "brands",
        "name": "Brands",
        "source": "https://example.invalid/brands.xml",
        "levels": [{"name": "Vendor"}, {"name": "Model", "parent": "Vendor"}],
        "terms": [
            ["Vendor", "alpha", "Alpha", None],
            ["Model", "one", "One", None],
            ["Model", "two", "Two", "42"],
        ],
        "edges": [
            ["Vendor", "alpha", "Model", "one"],
            ["Vendor", "alpha", "Model", "two"],
        ],
    }
    fixture.update(overrides)
    return fixture


def _rules(findings):
    return sorted({finding.rule for finding in findings})


def test_a_well_formed_fixture_has_no_findings():
    assert validate.validate_vocabulary(_fixture()) == []


def test_a_term_row_may_carry_an_explicit_sort_rank():
    """The optional 5th column: sort rank within the level.

    Fixture ROW order is canonical (level, code) for reviewability — VOC004
    — while the loader turns row order into ``Term.sort``, so without this
    column every picker on every stand was doomed to code-alphabetical order
    («0.1 МБ» first, «10 ГБ» before «2 ГБ» on a live RAM picker). The rank
    is data IN the row: review order and display order stop fighting.
    """
    ranked = _fixture(terms=[
        ["Vendor", "alpha", "Alpha", None, 0],
        ["Model", "one", "One", None, 2],
        ["Model", "two", "Two", "42", 1],
    ])
    assert validate.validate_vocabulary(ranked) == []
    # Mixed arity is fine — the column is per-row optional.
    mixed = _fixture(terms=[
        ["Vendor", "alpha", "Alpha", None],
        ["Model", "one", "One", None, 1],
        ["Model", "two", "Two", "42"],
    ])
    assert validate.validate_vocabulary(mixed) == []
    # But it is a rank, not free text.
    junk = _fixture(terms=[["Vendor", "alpha", "Alpha", None, "first"]], edges=[])
    assert _rules(validate.validate_vocabulary(junk)) == ["VOC001"]


def test_VOC001_an_unknown_key_and_a_missing_one():
    assert _rules(validate.validate_vocabulary(_fixture(colour="red"))) == ["VOC001"]
    broken = _fixture()
    del broken["edges"]
    assert _rules(validate.validate_vocabulary(broken)) == ["VOC001"]


@pytest.mark.parametrize("slug_value", ["Brands", "brands-", "a" * 65, ""])
def test_VOC001_the_schema_rejects_a_slug_that_is_not_one(slug_value):
    pytest.importorskip("jsonschema")
    assert "VOC001" in _rules(validate.validate_vocabulary(_fixture(slug=slug_value)))


def test_VOC002_two_terms_of_one_level_may_not_share_a_code():
    broken = _fixture(terms=[
        ["Vendor", "alpha", "Alpha", None],
        ["Vendor", "alpha", "Альфа", None],
    ], edges=[])
    findings = validate.validate_vocabulary(broken)
    assert "VOC002" in _rules(findings)
    message = next(f.message for f in findings if f.rule == "VOC002")
    assert "brands: 1 duplicate term code out of 2 terms" in message
    assert "Vendor:alpha = 'Alpha', 'Альфа'" in message


def test_VOC002_the_same_code_on_two_DIFFERENT_levels_is_fine():
    """A term code is unique per (vocabulary, level) — not per vocabulary."""
    ok = _fixture(terms=[
        ["Vendor", "alpha", "Alpha", None],
        ["Model", "alpha", "Alpha", None],
    ], edges=[["Vendor", "alpha", "Model", "alpha"]])
    assert validate.validate_vocabulary(ok) == []


def test_assert_unique_codes_raises_for_an_importer_that_wants_to_stop_early():
    terms = [["Vendor", "alpha", "Alpha", None], ["Vendor", "alpha", "Альфа", None]]
    with pytest.raises(validate.VocabularyCodeCollision) as excinfo:
        validate.assert_unique_codes("brands", terms)
    assert "brands: 1 duplicate term code out of 2 terms" in str(excinfo.value)
    validate.assert_unique_codes("brands", terms[:1])          # no raise


def test_VOC003_a_level_parent_must_be_declared_before_it():
    forward = _fixture(levels=[{"name": "Vendor", "parent": "Model"}, {"name": "Model"}])
    assert "VOC003" in _rules(validate.validate_vocabulary(forward))
    unknown = _fixture(levels=[{"name": "Vendor"}, {"name": "Model", "parent": "Nope"}])
    assert "VOC003" in _rules(validate.validate_vocabulary(unknown))


def test_VOC004_terms_are_ordered_by_level_then_code():
    shuffled = _fixture()
    shuffled["terms"] = [shuffled["terms"][2], shuffled["terms"][1], shuffled["terms"][0]]
    assert "VOC004" in _rules(validate.validate_vocabulary(shuffled))


def test_VOC005_an_edge_may_only_name_declared_terms():
    broken = _fixture(edges=[["Vendor", "alpha", "Model", "three"]])
    findings = validate.validate_vocabulary(broken)
    assert "VOC005" in _rules(findings)
    assert "Model:three" in next(f.message for f in findings if f.rule == "VOC005")


def test_VOC006_an_edge_joins_a_level_to_its_declared_parent():
    broken = _fixture(
        levels=[{"name": "Vendor"}, {"name": "Series"}, {"name": "Model", "parent": "Series"}],
        terms=[["Vendor", "alpha", "Alpha", None], ["Series", "s", "S", None],
               ["Model", "one", "One", None]],
        edges=[["Vendor", "alpha", "Model", "one"]])
    assert "VOC006" in _rules(validate.validate_vocabulary(broken))


def test_VOC007_edges_are_sorted_and_never_repeated():
    reversed_edges = _fixture()
    reversed_edges["edges"] = list(reversed(reversed_edges["edges"]))
    assert "VOC007" in _rules(validate.validate_vocabulary(reversed_edges))
    repeated = _fixture()
    repeated["edges"] = [repeated["edges"][0], repeated["edges"][0]]
    assert "VOC007" in _rules(validate.validate_vocabulary(repeated))


def test_VOC008_a_fixture_file_carries_the_canonical_bytes(tmp_path):
    path = tmp_path / "brands.json"
    path.write_text(json.dumps(_fixture()), encoding="utf-8")     # one line, no newline
    findings = validate.validate_file(path)
    assert _rules(findings) == ["VOC008"]
    path.write_text(writer.canonical_json(_fixture()), encoding="utf-8")
    assert validate.validate_file(path) == []


def test_a_file_that_is_not_json_at_all_is_one_finding(tmp_path):
    path = tmp_path / "brands.json"
    path.write_text("<html>404</html>", encoding="utf-8")
    findings = validate.validate_file(path)
    assert len(findings) == 1 and findings[0].rule == "VOC001"


def test_CAT001_a_catalogue_half_must_be_canonical_too(tmp_path):
    path = tmp_path / "features.json"
    path.write_text('[{"slug":"colour"}]', encoding="utf-8")
    assert _rules(validate.validate_catalog_file(path)) == ["CAT001"]
    path.write_text(writer.canonical_json([{"slug": "colour"}]), encoding="utf-8")
    assert validate.validate_catalog_file(path) == []


def test_the_shipped_schema_is_loadable_and_is_the_one_the_gate_uses():
    schema = validate.load_schema()
    assert schema["title"] == "Vocabulary fixture"
    assert validate.SCHEMA_PATH.is_file()


# ------------------------------------------------------------------------- the CLI

def _emit(root, fixture=None, features=None):
    writer.write_vocabularies(str(root), [fixture or _fixture()])
    writer.write_catalog(str(root), features if features is not None else [], [])
    return root


def test_the_cli_walks_both_halves_of_an_output_directory(tmp_path, capsys):
    _emit(tmp_path)
    assert main([str(tmp_path)]) == 0
    assert list(validate.iter_fixture_files(tmp_path)) == [
        tmp_path / "vocabularies" / "brands.json",
        tmp_path / "catalog" / "categories.json",
        tmp_path / "catalog" / "features.json",
    ]


def test_the_cli_fails_and_names_the_defect(tmp_path, capsys):
    _emit(tmp_path, fixture=_fixture(terms=[
        ["Vendor", "alpha", "Alpha", None],
        ["Vendor", "alpha", "Альфа", None],
    ], edges=[]))
    assert main([str(tmp_path)]) == 1
    assert "VOC002" in capsys.readouterr().out


def test_the_cli_takes_a_single_file_and_a_bare_directory(tmp_path):
    path = tmp_path / "vocabularies" / "brands.json"
    writer.write_vocabularies(str(tmp_path), [_fixture()])
    assert main([str(path)]) == 0
    assert main([str(tmp_path / "vocabularies")]) == 0


def test_an_empty_directory_says_so_instead_of_passing_quietly(tmp_path, capsys):
    assert main([str(tmp_path)]) == 0
    assert "nothing checked" in capsys.readouterr().err


def test_a_missing_target_is_an_error_not_a_pass(tmp_path):
    assert main([str(tmp_path / "nope")]) == 2


def test_json_output_is_machine_readable(tmp_path, capsys):
    _emit(tmp_path)
    assert main([str(tmp_path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == {"ok": True, "errors": 0, "findings": [], "notes": []}


def test_the_console_script_is_installed_and_runs(tmp_path):
    _emit(tmp_path)
    result = subprocess.run([sys.executable, "-m", "stapel_tools.catalog_fixtures.cli",
                             str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
