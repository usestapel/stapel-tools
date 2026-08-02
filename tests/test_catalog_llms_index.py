"""Root ``llms.txt`` fleet index (badge-canon §3 p.5, backlog #188/#69).

Covers :func:`stapel_tools.catalog.build_llms_index` and
:func:`stapel_tools.catalog.module_llms_link`: the aggregate that a
not-yet-oriented agent reads FIRST, before it knows which of the 26+ modules
it needs. Exercises the same three properties as
:mod:`stapel_tools.llms_txt` (deterministic, hard token budget, LOUD on a
partial rollout) plus the CLI wiring (write / --check drift / --llms-budget).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from stapel_tools.catalog import (
    build_llms_index,
    load_documents_with_roots,
    main,
    module_llms_link,
)

FIX = Path(__file__).parent / "fixtures" / "catalog"
FULL = FIX / "mod-full"
MINI = FIX / "mod-minimal"


def _repo(tmp_path, name, provides="Does a thing.", version="1.0.0", with_llms=False):
    root = tmp_path / name
    docs = root / "docs"
    docs.mkdir(parents=True)
    (docs / "capabilities.json").write_text(
        json.dumps({"module": name, "version": version, "provides": provides})
    )
    if with_llms:
        (docs / "llms.txt").write_text(f"# {name}\n\nsome rendered surface\n")
    return root


# ── module_llms_link ─────────────────────────────────────────────────────────


def test_link_prefers_stapel_libs_github_url():
    assert module_llms_link("stapel-auth") == (
        "https://github.com/usestapel/stapel-auth/blob/main/docs/llms.txt"
    )


def test_link_strips_dot_git_suffix():
    link = module_llms_link("stapel-core")
    assert link.startswith("https://github.com/usestapel/stapel-core/blob/main/")
    assert ".git" not in link


def test_link_falls_back_to_relative_path_for_unregistered_module():
    # stapel-demo/stapel-mini are test fixtures, not real STAPEL_LIBS entries
    assert module_llms_link("stapel-demo") == "stapel-demo/docs/llms.txt"


# ── build_llms_index: honest partial coverage ───────────────────────────────


def test_described_and_missing_split_on_docs_llms_txt_presence(tmp_path):
    described_root = _repo(tmp_path, "stapel-auth", provides="Authn/authz.", with_llms=True)
    missing_root = _repo(tmp_path, "stapel-billing", provides="Billing.", with_llms=False)

    pairs, _ = load_documents_with_roots([described_root, missing_root])
    text, described, total = build_llms_index(pairs)

    assert (described, total) == (1, 2)
    assert "## Described (1)" in text
    assert "## Not yet described (1)" in text
    assert "stapel-auth" in text.split("## Described")[1].split("## Not yet described")[0]
    assert "stapel-billing — no docs/llms.txt yet" in text
    # the described module carries its provides one-liner + a real link
    assert "Authn/authz." in text
    assert "https://github.com/usestapel/stapel-auth/blob/main/docs/llms.txt" in text
    # the undescribed one gets no fabricated link
    assert "stapel-billing/docs/llms.txt" not in text


def test_zero_of_n_is_loud_not_silent(tmp_path):
    """Every module missing llms.txt (today's real fleet state) must still
    render a clear count, not an empty or misleadingly-silent file."""
    root = _repo(tmp_path, "stapel-zeta", with_llms=False)
    pairs, _ = load_documents_with_roots([root])
    text, described, total = build_llms_index(pairs)
    assert (described, total) == (0, 1)
    assert "0/1 modules describe their own surface" in text
    assert "## Not yet described (1)" in text


def test_all_described_has_no_placeholder_noise(tmp_path):
    root = _repo(tmp_path, "stapel-auth", with_llms=True)
    pairs, _ = load_documents_with_roots([root])
    text, described, total = build_llms_index(pairs)
    assert (described, total) == (1, 1)
    assert "## Not yet described (0)" in text
    assert "(none — every module in this build has one)" in text


# ── determinism ──────────────────────────────────────────────────────────────


def test_deterministic_regardless_of_input_order(tmp_path):
    a = _repo(tmp_path, "stapel-alpha", with_llms=True)
    b = _repo(tmp_path, "stapel-beta", with_llms=False)

    pairs1, _ = load_documents_with_roots([a, b])
    pairs2, _ = load_documents_with_roots([b, a])
    text1, *_ = build_llms_index(pairs1)
    text2, *_ = build_llms_index(pairs2)
    assert text1 == text2
    assert text1.index("stapel-alpha") < text1.index("stapel-beta")


def test_no_timestamp_or_double_blank_lines(tmp_path):
    root = _repo(tmp_path, "stapel-auth", with_llms=True)
    pairs, _ = load_documents_with_roots([root])
    text, *_ = build_llms_index(pairs)
    assert "2026" not in text
    assert "\n\n\n" not in text
    assert text.endswith("\n") and not text.endswith("\n\n")


# ── hard token budget: fails loud, never truncates ──────────────────────────


def test_over_budget_raises_and_reports_costs(tmp_path):
    from stapel_tools.llms_txt import EmitError

    root = _repo(tmp_path, "stapel-auth", provides="x" * 2000, with_llms=True)
    pairs, _ = load_documents_with_roots([root])
    with pytest.raises(EmitError) as exc_info:
        build_llms_index(pairs, budget=10)
    assert "over the 10-token budget" in str(exc_info.value)


# ── CLI wiring: written alongside catalog.json/catalog.md, --check, --llms-budget ──


def test_cli_writes_llms_txt_alongside_catalog(tmp_path, capsys):
    described_root = _repo(tmp_path, "stapel-auth", provides="Authn.", with_llms=True)
    missing_root = _repo(tmp_path, "stapel-billing", provides="Billing.", with_llms=False)
    out = tmp_path / "out"

    rc = main([str(described_root), str(missing_root), "--out-dir", str(out)])
    assert rc == 0
    llms = (out / "llms.txt").read_text()
    assert "1/2 modules describe" in llms
    err = capsys.readouterr().err
    assert "1/2 describe llms.txt" in err
    assert f"{out}/llms.txt" in err


def test_cli_check_passes_when_up_to_date_then_fails_on_drift(tmp_path):
    root = _repo(tmp_path, "stapel-auth", with_llms=True)
    out = tmp_path / "out"
    assert main([str(root), "--out-dir", str(out)]) == 0
    assert main([str(root), "--out-dir", str(out), "--check"]) == 0

    # a new module appears (drift) → --check must catch it, out-of-band write
    other = _repo(tmp_path, "stapel-billing", with_llms=False)
    rc = main([str(root), str(other), "--out-dir", str(out), "--check"])
    assert rc == 1


def test_cli_check_catches_llms_txt_specifically(tmp_path):
    """A module gaining docs/llms.txt between builds is drift too, even when
    catalog.json/catalog.md (which don't look at that file) are unaffected."""
    root = _repo(tmp_path, "stapel-auth", with_llms=False)
    out = tmp_path / "out"
    assert main([str(root), "--out-dir", str(out)]) == 0
    assert main([str(root), "--out-dir", str(out), "--check"]) == 0

    (root / "docs" / "llms.txt").write_text("# stapel-auth\n\nsurface\n")
    rc = main([str(root), "--out-dir", str(out), "--check"])
    assert rc == 1


def test_cli_llms_budget_flag_fails_loud_and_writes_nothing(tmp_path):
    root = _repo(tmp_path, "stapel-auth", provides="x" * 2000, with_llms=True)
    out = tmp_path / "out"
    rc = main([str(root), "--out-dir", str(out), "--llms-budget", "10"])
    assert rc == 1
    assert not out.exists()


# ── --from-installed generalizes: the wheel-shipped layout works the same ──


class _FakeDist:
    def __init__(self, name, root, files):
        self.metadata = {"Name": name}
        self._root = Path(root)
        self._files = [Path(f) for f in files]

    @property
    def files(self):
        return self._files

    def locate_file(self, path):
        return self._root / path

    def read_text(self, filename):
        return None


def test_installed_distribution_shipping_llms_txt_is_seen_as_described(tmp_path, monkeypatch):
    """Proves the aggregator does not need a workspace checkout: once a
    module's wheel ships docs/llms.txt next to docs/capabilities.json (the
    same package-data list capabilities.json already rides in), the fleet
    index counts it as described purely from the installed environment."""
    import importlib.metadata as md

    site = tmp_path / "site-packages"
    pkg_root = site / "stapel_auth"
    (pkg_root / "docs").mkdir(parents=True)
    (pkg_root / "docs" / "capabilities.json").write_text(
        json.dumps({"module": "stapel-auth", "version": "2.0.0", "provides": "Authn/authz."})
    )
    (pkg_root / "docs" / "llms.txt").write_text("# stapel-auth\n\nsurface\n")

    dist = _FakeDist(
        "stapel-auth",
        root=site,
        files=["stapel_auth/docs/capabilities.json", "stapel_auth/docs/llms.txt"],
    )
    monkeypatch.setattr(md, "distributions", lambda: iter([dist]))

    out = tmp_path / "out"
    rc = main(["--from-installed", "--out-dir", str(out)])
    assert rc == 0
    llms = (out / "llms.txt").read_text()
    assert "1/1 modules describe" in llms
    assert "https://github.com/usestapel/stapel-auth/blob/main/docs/llms.txt" in llms


def test_installed_distribution_without_shipped_llms_txt_is_honestly_missing(
    tmp_path, monkeypatch
):
    """The negative of the above: a wheel built BEFORE the module started
    shipping docs/llms.txt must show up as 'not yet described', not silently
    vanish or falsely count as described."""
    import importlib.metadata as md

    site = tmp_path / "site-packages"
    pkg_root = site / "stapel_billing"
    (pkg_root / "docs").mkdir(parents=True)
    (pkg_root / "docs" / "capabilities.json").write_text(
        json.dumps({"module": "stapel-billing", "version": "1.0.0", "provides": "Billing."})
    )

    dist = _FakeDist(
        "stapel-billing", root=site, files=["stapel_billing/docs/capabilities.json"]
    )
    monkeypatch.setattr(md, "distributions", lambda: iter([dist]))

    out = tmp_path / "out"
    rc = main(["--from-installed", "--out-dir", str(out)])
    assert rc == 0
    llms = (out / "llms.txt").read_text()
    assert "0/1 modules describe" in llms
    assert "stapel-billing — no docs/llms.txt yet" in llms
