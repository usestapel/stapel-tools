"""stapel-preset-gate — a composite's preset equals the scaffold registry.

A composite library (stapel-shop, -classified, -booking, -social) ships a
``preset.py``: the INSTALLED_APPS, URL mounts and STAPEL_<MOD> defaults a
hand-wired host copies. ``STAPEL_LIBS`` in :mod:`stapel_tools.create_project`
is what a generated project gets for the same selection. Two sources for one
fact drifted in all three directions at once (AUDIT-BRICKS-2 BK-1/2/3, SO-1,
SO-3, SE-1): booking's preset left out the categories provider its listings
publish through, presets mounted ``listings/`` where the registry and every
client say ``listings/api/``, and social's scenario defaults were read by
nothing. This gate holds the two equal:

* INSTALLED_APPS: the preset's list equals the registry's member closure
  (``requires`` only, not ``companions``) in the order the scaffold emits it;
* URL mounts: every preset include mounts at the registry's prefix, and every
  member with an HTTP surface is mounted;
* SETTINGS_DEFAULTS: a mapping of ``STAPEL_<MOD>`` names to dicts, which is
  the shape the generated settings merge at runtime (``_wiring``), so the
  scaffold's defaults ARE the preset's;
* FRONTEND_COMPOSITES: every pair a frontend preset installs has its backend
  in the composite's selection (members plus companions).

The presets are read from the INSTALLED packages, so the gate checks the
versions this environment resolved — in CI, the declared ``test`` extra.

Usage::

    stapel-preset-gate            # every composite in the registry
    stapel-preset-gate shop booking
"""
from __future__ import annotations

import argparse
import importlib
import sys
from types import ModuleType


def composite_keys() -> list[str]:
    from .create_project import STAPEL_LIBS

    return [key for key, info in STAPEL_LIBS.items() if info.get("composite")]


def registry_members(key: str) -> list[str]:
    """The composite's member closure in emission order (registry order)."""
    from .create_project import _expand_with_requires

    return _expand_with_requires([key], companions=False)


def preset_drift(key: str, preset: ModuleType | object) -> list[str]:
    """Every way ``preset`` disagrees with ``STAPEL_LIBS`` for composite ``key``.

    Empty list = equal. Each finding is one printable line naming the fix.
    """
    from ._url_mounts import url_mount_for
    from .create_project import STAPEL_LIBS

    findings: list[str] = []
    members = registry_members(key)
    expected_apps = [
        STAPEL_LIBS[m]["dir"] for m in members if STAPEL_LIBS[m].get("django_app", True)
    ]
    actual_apps = list(getattr(preset, "INSTALLED_APPS", []))

    missing = [a for a in expected_apps if a not in actual_apps]
    extra = [a for a in actual_apps if a not in expected_apps]
    if missing:
        findings.append(
            f"{key}: preset INSTALLED_APPS lacks {missing} that the registry "
            "installs (add them to the preset, or drop them from the registry's "
            "requires)"
        )
    if extra:
        findings.append(
            f"{key}: preset INSTALLED_APPS has {extra} that the registry does not "
            f"install (add them to STAPEL_LIBS[{key!r}]['requires'])"
        )
    if not missing and not extra and actual_apps != expected_apps:
        findings.append(
            f"{key}: preset INSTALLED_APPS order {actual_apps} differs from the "
            f"order a generated project emits {expected_apps} (ready() runs in "
            "this order; reorder the preset or the registry)"
        )

    expected_urls = {}
    for m in members:
        mount = url_mount_for(STAPEL_LIBS[m]["dir"])
        if mount is not None:
            expected_urls[f"{STAPEL_LIBS[m]['dir']}.urls"] = mount
    actual_urls: dict[str, str] = {}
    for prefix, module in getattr(preset, "URL_INCLUDES", []):
        actual_urls[module] = prefix
    for module, prefix in expected_urls.items():
        if module not in actual_urls:
            findings.append(f"{key}: preset URL_INCLUDES does not mount {module} (registry: {prefix!r})")
        elif actual_urls[module] != prefix:
            findings.append(
                f"{key}: preset mounts {module} at {actual_urls[module]!r}, the "
                f"registry at {prefix!r}"
            )
    for module in actual_urls:
        if module not in expected_urls:
            findings.append(f"{key}: preset mounts {module}, which the registry does not mount")

    defaults = getattr(preset, "SETTINGS_DEFAULTS", None)
    if not isinstance(defaults, dict):
        findings.append(f"{key}: preset SETTINGS_DEFAULTS is not a dict")
    else:
        for name, value in defaults.items():
            if not (isinstance(name, str) and name.startswith("STAPEL_") and name.isupper()):
                findings.append(f"{key}: SETTINGS_DEFAULTS key {name!r} is not a STAPEL_<MOD> name")
            elif not isinstance(value, dict):
                findings.append(f"{key}: SETTINGS_DEFAULTS[{name!r}] is not a dict")
    return findings


def frontend_drift(name: str) -> list[str]:
    """Pairs a frontend composite installs whose backend the selection lacks."""
    from .create_project import FRONTEND_COMPOSITES, STAPEL_LIBS, _expand_with_requires

    preset = FRONTEND_COMPOSITES[name]
    selection = set(_expand_with_requires([preset["backend"]]))
    return [
        f"{name}: frontend pair {pair!r} has no backend in the {preset['backend']!r} "
        "selection (add it to the composite's requires or companions)"
        for pair in preset["pairs"]
        if pair in STAPEL_LIBS and pair not in selection
    ]


def load_preset(key: str) -> ModuleType:
    from .create_project import STAPEL_LIBS

    return importlib.import_module(f"{STAPEL_LIBS[key]['dir']}.preset")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stapel-preset-gate", description=__doc__.split("\n\n")[0])
    parser.add_argument("composites", nargs="*", help="composite keys (default: all)")
    args = parser.parse_args(argv)
    keys = args.composites or composite_keys()
    findings: list[str] = []
    for key in keys:
        try:
            preset = load_preset(key)
        except ImportError as exc:
            findings.append(f"{key}: preset not importable ({exc}); install the composite")
            continue
        findings.extend(preset_drift(key, preset))
    from .create_project import FRONTEND_COMPOSITES

    for name in FRONTEND_COMPOSITES:
        if FRONTEND_COMPOSITES[name]["backend"] in keys:
            findings.extend(frontend_drift(name))
    for line in findings:
        print(line)
    if findings:
        print(f"stapel-preset-gate: {len(findings)} finding(s)", file=sys.stderr)
        return 1
    print(f"stapel-preset-gate: {len(keys)} preset(s) equal the registry")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
