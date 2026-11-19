#!/usr/bin/env bash
# The mobile app's proof gates, no LLM: `make check` in mobile/ (or `make mobile-check` at the root).
#
#   scripts/mobile-check.sh                         # every gate this machine can run
#   WALK_BASE_URL=http://127.0.0.1:8000/ scripts/mobile-check.sh   # + the sign-in walk
#   STRICT=1 scripts/mobile-check.sh                # a gate that cannot run is a failure
#
# Each gate prints PASS / FAIL / NOT RUN (with the missing tool). NOT RUN is never PASS.
# Toolchain: JDK 21 (JAVA_HOME, else Homebrew openjdk@21), ANDROID_HOME (default
# ~/Library/Android/sdk), Xcode for iOS (DEVELOPER_DIR, default /Applications/Xcode.app).
set -uo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"
OUT="$HERE/build/mobile-check"
mkdir -p "$OUT"

if [[ -z "${JAVA_HOME:-}" ]] || ! "$JAVA_HOME/bin/java" -version 2>&1 | grep -q 'version "21'; then
    if command -v brew >/dev/null 2>&1 && [[ -d "$(brew --prefix openjdk@21 2>/dev/null)/libexec/openjdk.jdk" ]]; then
        export JAVA_HOME="$(brew --prefix openjdk@21)/libexec/openjdk.jdk/Contents/Home"
    elif [[ -x /usr/libexec/java_home ]] && /usr/libexec/java_home -v 21 >/dev/null 2>&1; then
        export JAVA_HOME="$(/usr/libexec/java_home -v 21)"
    fi
fi
export ANDROID_HOME="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
if [[ "$(uname -s)" == "Darwin" ]]; then
    export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
fi

have_jdk() { [[ -n "${JAVA_HOME:-}" && -x "$JAVA_HOME/bin/java" ]]; }
have_android() { [[ -d "$ANDROID_HOME/platforms" ]]; }
have_xcode() { [[ "$(uname -s)" == "Darwin" && -d "${DEVELOPER_DIR:-/nonexistent}" ]] && command -v xcrun >/dev/null 2>&1; }

RESULTS=()
FAILED=0
NOTRUN=0
gate() { # name needs... -- command...
    local name="$1"; shift
    local missing=()
    while [[ "$1" != "--" ]]; do
        case "$1" in
            jdk) have_jdk || missing+=("JDK 21") ;;
            android) have_android || missing+=("Android SDK at $ANDROID_HOME") ;;
            xcode) have_xcode || missing+=("Xcode") ;;
            walk) [[ -n "${WALK_BASE_URL:-}" ]] || missing+=("WALK_BASE_URL (a running backend)") ;;
        esac
        shift
    done
    shift
    if (( ${#missing[@]} )); then
        RESULTS+=("NOT RUN  $name  (missing: ${missing[*]})"); NOTRUN=$((NOTRUN + 1)); return
    fi
    local log="$OUT/$name.log" start=$SECONDS
    echo "[mobile-check] $name"
    if "$@" >"$log" 2>&1; then
        RESULTS+=("PASS     $name  ($((SECONDS - start)) s)")
    else
        RESULTS+=("FAIL     $name  ($((SECONDS - start)) s, log: build/mobile-check/$name.log)")
        FAILED=$((FAILED + 1)); tail -30 "$log"
    fi
}

gate generated-and-tokens jdk -- ./gradlew --console=plain :shared:stapelGenerateCheck :shared:checkStapelTokens
gate nav-gate-jvm jdk -- ./gradlew --console=plain :shared:jvmTest
gate android-assemble jdk android -- ./gradlew --console=plain :androidApp:assembleDebug
gate desktop-smoke jdk -- ./gradlew --console=plain :desktopApp:run --args=--smoke
gate nav-gate-ios jdk xcode -- ./gradlew --console=plain :shared:iosSimulatorArm64Test
gate ios-launch-smoke jdk xcode -- scripts/ios-smoke.sh
gate sign-in-walk-jvm jdk walk -- ./gradlew --console=plain :shared:jvmTest "-Pstapel.walk.baseUrl=${WALK_BASE_URL:-}"
gate sign-in-walk-ios jdk xcode walk -- ./gradlew --console=plain :shared:iosSimulatorArm64Test "-Pstapel.walk.baseUrl=${WALK_BASE_URL:-}"

echo
printf '%s\n' "${RESULTS[@]}"
if (( FAILED )); then exit 1; fi
if (( NOTRUN )) && [[ "${STRICT:-0}" == "1" ]]; then echo "STRICT: $NOTRUN gate(s) not run"; exit 2; fi
exit 0
