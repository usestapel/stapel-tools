"""The 2026-11-12 brick wave in the registry, inert until released.

promotions, delivery, inventory, cms, courses and referrals are registry rows
so ``--modules`` knows them, but none has a PyPI release: a row carries
``"released": False`` and the scaffold refuses it unless the caller passes
``--allow-unreleased`` (then it installs the sibling workspace checkout,
never a PyPI resolution). stapel-assemble reports such a lib as a gap.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from stapel_tools._gdpr_owners import derive_data_owners
from stapel_tools.assemble_scaffold import assemble_scaffold
from stapel_tools.create_project import (
    STAPEL_LIBS,
    _expand_with_requires,
    create_project,
    require_released,
    unreleased,
)

WAVE = ["promotions", "delivery", "inventory", "cms", "courses", "referrals"]


class TestRows:
    @pytest.mark.parametrize("key", WAVE)
    def test_row_is_unreleased_and_never_resolved_from_pypi(self, key):
        info = STAPEL_LIBS[key]
        assert info["dir"] == f"stapel_{key}"
        assert info["released"] is False
        # The pip renderer's editable-sibling branch; no PyPI line.
        assert info["ahead_of_pypi"] is True
        assert info["http"] is True
        assert info["beat"] == (f"stapel_{key}.beat", f"get_{key}_beat_schedule")

    def test_mounts(self):
        prefixes = {k: STAPEL_LIBS[k]["url_prefix"] for k in WAVE}
        assert prefixes == {
            "promotions": "promotions/api/", "delivery": "delivery/api/",
            "inventory": "inventory/api/", "cms": "cms/", "courses": "courses/api/",
            "referrals": "referrals/api/",
        }

    def test_every_released_row_has_no_flag(self):
        """``released`` is only ever written as False; dropping it releases."""
        for key, info in STAPEL_LIBS.items():
            assert info.get("released", True) in (True, False), key
            if key not in WAVE:
                assert "released" not in info, key

    def test_no_composite_pulls_an_unreleased_brick(self):
        for key, info in STAPEL_LIBS.items():
            if info.get("composite"):
                assert unreleased(_expand_with_requires([key])) == [], key


class TestGate:
    def test_require_released(self):
        require_released(["orders", "shop"])
        with pytest.raises(ValueError, match="stapel-promotions.*--allow-unreleased"):
            require_released(["orders", "promotions"])
        require_released(["orders", "promotions"], allow_unreleased=True)

    def test_create_project_refuses_without_the_flag(self, tmp_path, capsys):
        with pytest.raises(SystemExit):
            create_project(
                name="p", project_type="minimal", title="p", url="https://x.dev",
                company_name="X", company_email="x@x.dev", modules=["core", "cms"],
                output_dir=tmp_path, use_submodules=False, init_git=False)
        assert "stapel-cms not released on PyPI yet" in capsys.readouterr().err
        assert not (tmp_path / "p").exists()

    def test_with_the_flag_the_sibling_checkout_is_installed(self, tmp_path):
        create_project(
            name="p", project_type="minimal", title="p", url="https://x.dev",
            company_name="X", company_email="x@x.dev", modules=["core", "cms"],
            output_dir=tmp_path, use_submodules=False, init_git=False,
            allow_unreleased=True)
        reqs = (tmp_path / "p" / "requirements.txt").read_text()
        assert "-e ../stapel-cms" in reqs
        assert "stapel-cms>=" not in reqs.replace("replace with: stapel-cms>=", "")

    def test_assemble_reports_a_gap(self, tmp_path):
        result = assemble_scaffold("p", libs=["orders", "referrals"], output_dir=tmp_path, verify=False)
        assert "referrals" in result.libs_unknown
        assert "referrals" not in result.libs_applied
        assert "orders" in result.libs_applied

    def test_cli_lists_them_apart(self):
        out = subprocess.run(
            [sys.executable, "-m", "stapel_tools.create_project", "--help"],
            capture_output=True, text=True, check=True).stdout
        flat = " ".join(out.split())
        assert "--allow-unreleased" in flat
        assert "Not on PyPI yet" in flat
        assert "promotions, delivery, inventory, cms, courses, referrals" in flat


def test_gdpr_owners_are_derived_from_the_checkouts():
    """No hand list: ``_gdpr_owners`` reads each brick's own declaration."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    if not all((root / f"stapel-{k}").is_dir() for k in WAVE):
        root = Path(__file__).resolve().parents[3]
    if not all((root / f"stapel-{k}").is_dir() for k in WAVE):
        pytest.skip("the wave's checkouts are not beside this repo")
    assert derive_data_owners(WAVE, workspace_root=root) == {k: ["account"] for k in sorted(WAVE)}
