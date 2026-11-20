#!/usr/bin/env bash
# iOS launch-and-deep-link smoke of the {{SLUG}} app on a simulator.
#
#   mobile/scripts/ios-smoke.sh [<simulator udid>]
#
# Builds iosApp with xcodebuild for the simulator (the Xcode build phase runs
# :shared:embedAndSignAppleFrameworkForXcode), boots the simulator, installs and launches the app,
# waits for the app's log markers (stapel.app.ready, stapel.nav <id>), opens a deep link and waits
# for stapel.link <uri> opened, and saves a screenshot. Artifacts in mobile/build/ios-smoke.
# Needs DEVELOPER_DIR (default /Applications/Xcode.app/Contents/Developer).
set -euo pipefail
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$HERE/build/ios-smoke"
DERIVED="$OUT/derived"
BUNDLE_ID="{{APP_ID}}"
LINK="${APP_SMOKE_LINK:-{{SMOKE_LINK}}}"
mkdir -p "$OUT"

UDID="${1:-}"
if [[ -z "$UDID" ]]; then
    UDID="$(xcrun simctl list devices available -j | python3 -c '
import json,sys
d=json.load(sys.stdin)["devices"]
for rt,devs in sorted(d.items(), reverse=True):
    if "iOS" not in rt: continue
    for x in devs:
        if x["name"].startswith("iPhone"): print(x["udid"]); sys.exit()')"
fi
echo "[ios-smoke] simulator $UDID"

echo "[ios-smoke] xcodebuild"
xcodebuild -project "$HERE/iosApp/iosApp.xcodeproj" -scheme iosApp -configuration Debug \
    -sdk iphonesimulator -destination "id=$UDID" -derivedDataPath "$DERIVED" \
    CODE_SIGNING_ALLOWED=NO build >"$OUT/xcodebuild.log" 2>&1 || { tail -40 "$OUT/xcodebuild.log"; exit 1; }
APP="$(find "$DERIVED/Build/Products" -maxdepth 2 -name '{{IOS_PRODUCT}}.app' | head -1)"
[[ -d "$APP" ]] || { echo "[ios-smoke] no {{IOS_PRODUCT}}.app built"; exit 1; }
# The app is built for the project's deployment target (the iOS floor).
FLOOR="$(sed -n 's/.*IPHONEOS_DEPLOYMENT_TARGET = \([0-9.]*\);.*/\1/p' "$HERE/iosApp/iosApp.xcodeproj/project.pbxproj" | sort -u)"
MINOS="$(otool -l "$APP/{{IOS_PRODUCT}}" | awk '/LC_BUILD_VERSION/{b=1} b&&/minos/{print $2; exit}')"
echo "[ios-smoke] deployment target $FLOOR, app binary minos $MINOS"
[[ "$MINOS" == "$FLOOR" ]] || { echo "[ios-smoke] the app binary's minos ($MINOS) is not the deployment target ($FLOOR)"; exit 1; }

xcrun simctl boot "$UDID" 2>/dev/null || true
xcrun simctl bootstatus "$UDID" -b >/dev/null
xcrun simctl terminate "$UDID" "$BUNDLE_ID" 2>/dev/null || true
xcrun simctl install "$UDID" "$APP"

LOG="$OUT/app.log"
: >"$LOG"
xcrun simctl spawn "$UDID" log stream --level info --style compact \
    --predicate 'process == "{{IOS_PRODUCT}}" AND eventMessage CONTAINS "stapel."' >"$LOG" 2>&1 &
STREAM=$!
trap 'kill $STREAM 2>/dev/null || true' EXIT
sleep 2

wait_for() { # pattern seconds
    for _ in $(seq 1 "$2"); do grep -q "$1" "$LOG" && return 0; sleep 1; done
    echo "[ios-smoke] '$1' did not appear within $2 s"; tail -20 "$LOG"; return 1
}

echo "[ios-smoke] launch"
xcrun simctl launch "$UDID" "$BUNDLE_ID" >"$OUT/launch.txt"
wait_for 'stapel.app.ready' 60
wait_for 'stapel.nav ' 30
xcrun simctl io "$UDID" screenshot "$OUT/launched.png" >/dev/null

echo "[ios-smoke] system link $LINK (expect the system's open-in-app prompt)"
xcrun simctl openurl "$UDID" "$LINK"
sleep 3
xcrun simctl io "$UDID" screenshot "$OUT/os-link-prompt.png" >/dev/null
if grep -q "stapel.link $LINK" "$LOG"; then echo "[ios-smoke] the system delivered the link without a prompt"; fi

echo "[ios-smoke] relaunch with the link as a launch argument (the app's URL entry point)"
xcrun simctl terminate "$UDID" "$BUNDLE_ID" 2>/dev/null || true
: >"$LOG.prev"; cp "$LOG" "$LOG.prev"
xcrun simctl launch "$UDID" "$BUNDLE_ID" -stapelOpenUrl "$LINK" >>"$OUT/launch.txt"
wait_for "stapel.link $LINK opened" 60
sleep 2
xcrun simctl io "$UDID" screenshot "$OUT/deep-link.png" >/dev/null
grep -E 'stapel\.(app|nav|link)' "$LOG" | sed 's/^/[ios-smoke] log: /'
echo "[ios-smoke] OK — artifacts in $OUT"
