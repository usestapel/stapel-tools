"""The scaffold's iOS shell and stapel-kmp's reference app agree on the iOS floor and the fallbacks."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from stapel_tools.composition.kmp import ios_parity
from stapel_tools.composition.kmp.ios_parity import (
    IOS_FLOOR,
    PAIRS,
    SAMPLE,
    TEMPLATES,
    parity_problems,
)


@pytest.fixture
def trees(tmp_path: Path) -> tuple[Path, Path]:
    tpl, smp = tmp_path / "templates", tmp_path / "sample"
    for tpl_rel, smp_rel in PAIRS.items():
        (tpl / tpl_rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(TEMPLATES / tpl_rel, tpl / tpl_rel)
        smp.mkdir(exist_ok=True)
        shutil.copy(SAMPLE / smp_rel, smp / smp_rel)
    return tpl, smp


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} not in {path}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_the_shipped_template_and_reference_app_agree():
    assert parity_problems() == []
    assert ios_parity.main() == 0


def test_the_floor_is_fifteen():
    assert IOS_FLOOR == "15.0"
    assert "IPHONEOS_DEPLOYMENT_TARGET = 15.0;" in (TEMPLATES / "ios/project.pbxproj").read_text(encoding="utf-8")


def test_inversion_a_raised_template_deployment_target_fails(trees):
    tpl, smp = trees
    _edit(tpl / "ios/project.pbxproj", "IPHONEOS_DEPLOYMENT_TARGET = 15.0;", "IPHONEOS_DEPLOYMENT_TARGET = 18.2;")
    problems = parity_problems(tpl, smp)
    assert any("template ios/project.pbxproj: IPHONEOS_DEPLOYMENT_TARGET ['15.0', '18.2']" in p for p in problems), problems


def test_inversion_a_raised_reference_app_fails(trees):
    tpl, smp = trees
    _edit(smp / "project.pbxproj", "IPHONEOS_DEPLOYMENT_TARGET = 15.0;", "IPHONEOS_DEPLOYMENT_TARGET = 16.0;")
    assert any(p.startswith("reference app project.pbxproj") for p in parity_problems(tpl, smp))


def test_inversion_a_build_without_the_konan_pin_fails(trees):
    tpl, smp = trees
    text = (tpl / "shared.build.gradle.kts").read_text(encoding="utf-8")
    line = next(x for x in text.splitlines() if "-Xoverride-konan-properties" in x)
    (tpl / "shared.build.gradle.kts").write_text(text.replace(line + "\n", ""), encoding="utf-8")
    assert any("do not pin osVersionMin" in p for p in parity_problems(tpl, smp))


def test_inversion_a_different_floor_value_fails(trees):
    tpl, smp = trees
    _edit(tpl / "shared.build.gradle.kts", 'val IOS_FLOOR = "15.0"', 'val IOS_FLOOR = "17.0"')
    assert any("val IOS_FLOOR is 17.0" in p for p in parity_problems(tpl, smp))


def test_inversion_a_fallback_in_one_shell_only_fails(trees):
    tpl, smp = trees
    _edit(tpl / "ios/iOSApp.swift", "WindowGroup {", "WindowGroup {\n            if #available(iOS 16, *) { }")
    assert any("availability guards" in p for p in parity_problems(tpl, smp))


def test_inversion_a_drifted_content_view_fails(trees):
    tpl, smp = trees
    _edit(tpl / "ios/ContentView.swift", ".ignoresSafeArea()", ".ignoresSafeArea(.keyboard)")
    assert any("differs from the reference app's ContentView.swift" in p for p in parity_problems(tpl, smp))


def test_every_vendored_directory_ships_in_the_wheel():
    """0.85.2 shipped pin.json naming sample-ios/* without the files: vendor --check failed when installed."""
    import fnmatch
    import tomllib

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    patterns = tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["setuptools"]["package-data"][
        "stapel_tools.composition.kmp"
    ]
    vendored = TEMPLATES.parent / "vendored"
    missing = [
        p.relative_to(TEMPLATES.parent).as_posix()
        for p in vendored.rglob("*")
        if p.is_file() and not any(fnmatch.fnmatch(p.relative_to(TEMPLATES.parent).as_posix(), pat) for pat in patterns)
    ]
    assert missing == [], f"vendored files outside package-data: {missing}"
