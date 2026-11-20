"""Vendor stapel-kmp's scaffold data into this package, pinned by digest.

The KMP emitter reads only the vendored copy (``vendored/``), never a sibling
checkout, so a generated app is a function of the installed stapel-tools
version — the same rule stapel-kmp follows for our ``composition-registry.json``
(its ``tools/composition/vendor.sh`` + ``gradle/composition/pin.json``).

What is vendored, and from where in stapel-kmp:

* ``kmp-registry.json`` — train, group, BOM/catalog ids, plugin ids, the app
  builder, the offline module, per brick its artifacts and descriptor;
* ``shell-templates.json`` — ``substrate/stapel-shell/shell-templates.json``;
* ``nav-manifests/<brick>.json`` — ``bricks/<brick>/ui/nav-manifest.json``,
  for every brick whose descriptor the registry marks present;
* ``sample-ios/`` — ``samples/shop-mobile``'s Xcode project, ``shared`` build and Swift shell,
  the reference ``ios_parity`` compares the iOS templates with;
* ``gradle/`` — the Gradle wrapper (``gradle-wrapper.jar``, ``gradlew``,
  ``gradle-wrapper.properties``). The jar's sha256 is checked against the
  checksum Gradle publishes for that version AT VENDOR TIME (the only network
  access, explicit); the scaffold never downloads anything, it copies these
  bytes and refuses to if their digest is not the pinned one.

    python -m stapel_tools.composition.kmp.vendor --from ../stapel-kmp   # re-vendor + pin
    python -m stapel_tools.composition.kmp.vendor --check                # digests (make check)
    python -m stapel_tools.composition.kmp.vendor --check --against ../stapel-kmp
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENDORED = HERE / "vendored"
PIN = VENDORED / "pin.json"
REPOSITORY = "https://github.com/usestapel/stapel-kmp"

#: vendored path -> path in a stapel-kmp checkout (nav manifests are added per brick).
FIXED_SOURCES: dict[str, str] = {
    "kmp-registry.json": "kmp-registry.json",
    "shell-templates.json": "substrate/stapel-shell/shell-templates.json",
    "gradle/gradle-wrapper.jar": "gradle/wrapper/gradle-wrapper.jar",
    "gradle/gradle-wrapper.properties": "samples/shop-mobile/gradle/wrapper/gradle-wrapper.properties",
    "gradle/gradlew": "gradlew",
    # The reference app's iOS shell: what ``ios_parity`` holds the templates to (the iOS floor,
    # the availability fallbacks).
    "sample-ios/project.pbxproj": "samples/shop-mobile/iosApp/iosApp.xcodeproj/project.pbxproj",
    "sample-ios/shared.build.gradle.kts": "samples/shop-mobile/shared/build.gradle.kts",
    "sample-ios/iOSApp.swift": "samples/shop-mobile/iosApp/iosApp/iOSApp.swift",
    "sample-ios/ContentView.swift": "samples/shop-mobile/iosApp/iosApp/ContentView.swift",
    "sample-ios/Info.plist": "samples/shop-mobile/iosApp/iosApp/Info.plist",
    # The Swift halves of the vendor providers: the scaffold copies the selected ones into the app
    # shell (ARCH-PUSH-ANALYTICS §4.3); never a second copy that can drift.
    "ios-bridges/core/StapelAppDelegate.swift": "ios-bridges/core/StapelAppDelegate.swift",
    "ios-bridges/firebase/StapelFirebase.swift": "ios-bridges/firebase/StapelFirebase.swift",
    "ios-bridges/firebase/StapelFirebasePush.swift": "ios-bridges/firebase/StapelFirebasePush.swift",
    "ios-bridges/firebase/StapelFirebaseAnalytics.swift": "ios-bridges/firebase/StapelFirebaseAnalytics.swift",
    "ios-bridges/appmetrica/StapelAppMetrica.swift": "ios-bridges/appmetrica/StapelAppMetrica.swift",
    "ios-bridges/appmetrica/StapelAppMetricaAnalytics.swift":
        "ios-bridges/appmetrica/StapelAppMetricaAnalytics.swift",
    # The vendor Swift packages at the exact versions the bridges are compiled against (CI job
    # ios-bridges), and the Android Gradle plugin version the reference app applies.
    "ios-bridges/Package.swift": "ios-bridges/Package.swift",
    "sample-android/androidApp.build.gradle.kts": "samples/shop-mobile/androidApp/build.gradle.kts",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sources(checkout: Path) -> dict[str, str]:
    """Every file to vendor from *checkout*: vendored path -> source path."""
    out = dict(FIXED_SOURCES)
    registry = json.loads((checkout / "kmp-registry.json").read_text(encoding="utf-8"))
    for key, brick in sorted(registry.get("bricks", {}).items()):
        if (brick.get("descriptor") or {}).get("present"):
            out[f"nav-manifests/{key}.json"] = f"bricks/{key}/ui/nav-manifest.json"
    return out


def gradle_version(properties: str) -> str:
    match = re.search(r"gradle-([0-9][0-9.]*[0-9])-(?:bin|all)\.zip", properties)
    if not match:
        raise SystemExit("gradle-wrapper.properties names no distribution version")
    return match.group(1)


def published_wrapper_sha(version: str) -> str:
    url = f"https://services.gradle.org/distributions/gradle-{version}-wrapper.jar.sha256"
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 - fixed https host
        return resp.read().decode().strip()


def vendor(checkout: Path) -> int:
    checkout = checkout.resolve()
    commit = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "-C", str(checkout), "status", "--porcelain"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    if dirty:
        print(f"refusing: {checkout} has uncommitted changes; vendor a commit", file=sys.stderr)
        return 1
    files = sources(checkout)
    props = (checkout / files["gradle/gradle-wrapper.properties"]).read_text(encoding="utf-8")
    version = gradle_version(props)
    jar = (checkout / files["gradle/gradle-wrapper.jar"]).read_bytes()
    published = published_wrapper_sha(version)
    if sha256(jar) != published:
        print(f"refusing: gradle-wrapper.jar sha256 {sha256(jar)} is not the one Gradle "
              f"publishes for {version} ({published})", file=sys.stderr)
        return 1
    if VENDORED.exists():
        shutil.rmtree(VENDORED)
    pinned: dict[str, dict] = {}
    for rel, src in files.items():
        data = (checkout / src).read_bytes()
        dest = VENDORED / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        pinned[rel] = {"source": src, "sha256": sha256(data)}
    pin = {
        "repository": REPOSITORY,
        "commit": commit,
        "gradle": {"version": version, "wrapperJarSha256": published,
                   "wrapperJarChecksumSource":
                       f"https://services.gradle.org/distributions/gradle-{version}-wrapper.jar.sha256"},
        "how": "python -m stapel_tools.composition.kmp.vendor --from <stapel-kmp checkout>",
        "files": pinned,
    }
    PIN.write_text(json.dumps(pin, indent=2) + "\n", encoding="utf-8")
    print(f"vendored stapel-kmp {commit[:12]} ({len(pinned)} files) into {VENDORED}")
    return 0


def load_pin() -> dict:
    return json.loads(PIN.read_text(encoding="utf-8"))


def digest_problems() -> list[str]:
    """Vendored files whose bytes are not the pinned ones (or missing / unpinned)."""
    pin = load_pin()
    problems = []
    for rel, meta in pin["files"].items():
        path = VENDORED / rel
        if not path.is_file():
            problems.append(f"{rel}: pinned but missing")
        elif sha256(path.read_bytes()) != meta["sha256"]:
            problems.append(f"{rel}: sha256 differs from pin.json")
    present = {p.relative_to(VENDORED).as_posix() for p in VENDORED.rglob("*") if p.is_file()}
    for rel in sorted(present - set(pin["files"]) - {"pin.json"}):
        problems.append(f"{rel}: vendored but not pinned")
    jar = pin["files"].get("gradle/gradle-wrapper.jar", {}).get("sha256")
    if jar != pin["gradle"]["wrapperJarSha256"]:
        problems.append("gradle/gradle-wrapper.jar: pin differs from Gradle's published checksum")
    return problems


def sibling_drift(checkout: Path) -> list[str]:
    """Files whose bytes in *checkout* differ from the vendored copy."""
    drift = []
    want = sources(checkout)
    pin = load_pin()
    for rel, src in want.items():
        path = checkout / src
        if not path.is_file():
            drift.append(f"{rel}: {src} is missing in the checkout")
            continue
        if rel not in pin["files"]:
            drift.append(f"{rel}: the checkout ships {src}, not vendored here")
        elif sha256(path.read_bytes()) != pin["files"][rel]["sha256"]:
            drift.append(f"{rel}: {src} moved in the checkout")
    for rel in sorted(set(pin["files"]) - set(want)):
        drift.append(f"{rel}: vendored here, no longer shipped by the checkout")
    return drift


def _git(checkout: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, check=False)


def is_git_checkout(checkout: Path) -> bool:
    """*checkout* is itself a git work tree (a plain directory has no pin to read)."""
    proc = _git(checkout, "rev-parse", "--show-toplevel")
    return proc.returncode == 0 and Path(proc.stdout.decode().strip()).resolve() == checkout.resolve()


def pinned_drift(checkout: Path) -> list[str]:
    """Vendored files whose bytes differ from the PINNED commit of *checkout*.

    This is the blocking comparison: the vendored copy claims to be stapel-kmp at
    ``pin.json``'s commit, and that claim does not change when stapel-kmp's main moves
    on. A checkout that does not contain the pinned commit cannot confirm it, which
    is a finding (fetch it), never a pass."""
    pin = load_pin()
    commit = pin["commit"]
    if _git(checkout, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}").returncode != 0:
        return [f"the pinned commit {commit[:12]} is not in {checkout} "
                f"(git -C {checkout} fetch origin) — the vendored copy cannot be confirmed"]
    drift = []
    registry = _git(checkout, "show", f"{commit}:kmp-registry.json")
    want = dict(FIXED_SOURCES)
    if registry.returncode == 0:
        for key, brick in sorted(json.loads(registry.stdout).get("bricks", {}).items()):
            if (brick.get("descriptor") or {}).get("present"):
                want[f"nav-manifests/{key}.json"] = f"bricks/{key}/ui/nav-manifest.json"
    for rel, src in want.items():
        shown = _git(checkout, "show", f"{commit}:{src}")
        if shown.returncode != 0:
            drift.append(f"{rel}: {src} is not in the pinned commit {commit[:12]}")
        elif rel not in pin["files"]:
            drift.append(f"{rel}: the pinned commit {commit[:12]} ships {src}, not vendored here")
        elif sha256(shown.stdout) != pin["files"][rel]["sha256"]:
            drift.append(f"{rel}: differs from {src} at the pinned commit {commit[:12]}")
    for rel in sorted(set(pin["files"]) - set(want)):
        drift.append(f"{rel}: vendored here, not shipped by the pinned commit {commit[:12]}")
    return drift


def read_bytes(rel: str) -> bytes:
    """A vendored file's bytes, refused when they are not the pinned ones."""
    pin = load_pin()
    meta = pin["files"].get(rel)
    if meta is None:
        raise KeyError(f"{rel} is not vendored")
    data = (VENDORED / rel).read_bytes()
    if sha256(data) != meta["sha256"]:
        raise RuntimeError(
            f"vendored {rel} does not match its pinned sha256 — the installed stapel-tools is damaged")
    return data


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--from", dest="source", type=Path, help="stapel-kmp checkout to vendor from")
    p.add_argument("--check", action="store_true", help="verify digests (no network)")
    p.add_argument("--against", type=Path, help="with --check: also compare to this checkout")
    args = p.parse_args(argv)
    if args.source:
        return vendor(args.source)
    if not args.check:
        p.error("pass --from <checkout> or --check")
    problems = digest_problems()
    if args.against:
        problems += [f"drift: {d}" for d in sibling_drift(args.against)]
    pin = load_pin()
    if problems:
        print("kmp vendor: FAILED\n  - " + "\n  - ".join(problems))
        return 1
    print(f"kmp vendor: {len(pin['files'])} files match pin.json "
          f"(stapel-kmp {pin['commit'][:12]}, gradle {pin['gradle']['version']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
