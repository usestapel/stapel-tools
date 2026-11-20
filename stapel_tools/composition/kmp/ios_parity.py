"""The scaffold's iOS shell must not drift from stapel-kmp's reference app on what decides where it runs.

Compared, template (``templates/``) against the vendored reference app (``vendored/sample-ios/``,
copied from ``samples/shop-mobile`` at the pinned stapel-kmp commit):

* the iOS floor: every ``IPHONEOS_DEPLOYMENT_TARGET`` of both Xcode projects, and ``IOS_FLOOR`` of both
  ``shared`` builds, equal :data:`IOS_FLOOR`; both builds pin Kotlin/Native's ``osVersionMin`` of both
  iOS targets to it with the same line;
* the availability fallbacks: the ``#available`` / ``@available`` guards of the Swift shell are the
  same set in both (a fallback added to one is added to the other);
* ``ContentView.swift`` byte for byte (the app's only view, where a newer SwiftUI API would land).

    python -m stapel_tools.composition.kmp.ios_parity      # exit 1 and the differences
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"
SAMPLE = HERE / "vendored" / "sample-ios"

#: The minimum iOS of every app the scaffold generates (stapel-kmp ``IOS_FLOOR``, owner 2026-11-20).
IOS_FLOOR = "15.0"

#: template path -> vendored reference-app path.
PAIRS: dict[str, str] = {
    "ios/project.pbxproj": "project.pbxproj",
    "shared.build.gradle.kts": "shared.build.gradle.kts",
    "ios/iOSApp.swift": "iOSApp.swift",
    "ios/ContentView.swift": "ContentView.swift",
}

_TARGET = re.compile(r"IPHONEOS_DEPLOYMENT_TARGET\s*=\s*\"?([0-9.]+)\"?\s*;")
_FLOOR_VAL = re.compile(r'val\s+IOS_FLOOR\s*=\s*"([0-9.]+)"')
_OVERRIDE = re.compile(r".*-Xoverride-konan-properties=[^\n]*")
_GUARD = re.compile(r"[#@]available\s*\([^)]*\)")


def _floor_problems(label: str, path: str, text: str) -> list[str]:
    out = []
    if path.endswith("project.pbxproj"):
        found = set(_TARGET.findall(text))
        if found != {IOS_FLOOR}:
            out.append(f"{label} {path}: IPHONEOS_DEPLOYMENT_TARGET {sorted(found) or 'missing'}, must be only {IOS_FLOOR}")
    if path.endswith("build.gradle.kts"):
        m = _FLOOR_VAL.search(text)
        if not m or m.group(1) != IOS_FLOOR:
            out.append(f"{label} {path}: val IOS_FLOOR is {m.group(1) if m else 'missing'}, must be {IOS_FLOOR}")
        line = _OVERRIDE.search(text)
        if not line or not all(f"osVersionMin.{t}=$IOS_FLOOR" in line.group(0) for t in ("ios_arm64", "ios_simulator_arm64")):
            out.append(f"{label} {path}: Kotlin/Native binaries do not pin osVersionMin of both iOS targets to $IOS_FLOOR")
    return out


def parity_problems(templates: Path = TEMPLATES, sample: Path = SAMPLE) -> list[str]:
    problems: list[str] = []
    for tpl_rel, smp_rel in PAIRS.items():
        tpl = (templates / tpl_rel).read_text(encoding="utf-8")
        smp = (sample / smp_rel).read_text(encoding="utf-8")
        problems += _floor_problems("template", tpl_rel, tpl)
        problems += _floor_problems("reference app", smp_rel, smp)
        if tpl_rel.endswith("build.gradle.kts"):
            a, b = _OVERRIDE.search(tpl), _OVERRIDE.search(smp)
            if a and b and a.group(0).strip() != b.group(0).strip():
                problems.append(f"{tpl_rel}: the osVersionMin line differs from the reference app's")
        if tpl_rel.endswith(".swift"):
            ga, gb = sorted(_GUARD.findall(tpl)), sorted(_GUARD.findall(smp))
            if ga != gb:
                problems.append(f"{tpl_rel}: availability guards {ga} differ from the reference app's {gb}")
        if tpl_rel == "ios/ContentView.swift" and tpl != smp:
            problems.append(f"{tpl_rel}: differs from the reference app's ContentView.swift")
    return problems


def main() -> int:
    problems = parity_problems()
    if problems:
        print("kmp ios parity: FAILED\n  - " + "\n  - ".join(problems))
        return 1
    print(f"kmp ios parity: template and reference app agree (iOS floor {IOS_FLOOR})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
