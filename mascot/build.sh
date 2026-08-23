#!/bin/bash
# Build Casper as a real .app bundle.
#
# A bare Mach-O executable cannot hold microphone or speech-recognition
# permission on modern macOS: TCC attributes consent to a bundle identity, and
# the usage-description strings must live in an Info.plist. So "actively
# listen" is not a code change — it is a packaging change first.
#
#   ./build.sh          -> mascot/Casper.app  (and the bare ./casper for tests)
set -euo pipefail
cd "$(dirname "$0")"

APP="Casper.app"
BIN="$APP/Contents/MacOS/casper"

# Build somewhere else and SWAP it in, under a lock.
#
# This script used to open with `rm -rf Casper.app`, so for the whole length of
# a build the app did not exist. With more than one session working here that
# window is not theoretical: launches failed with "no such file or directory",
# a running Casper had its bundle deleted underneath it, and the resulting
# "app exits immediately with no crash report" cost a long time to chase —
# it was never the app, it was the other build.
LOCK="/tmp/meditate-casper-build.lock"
for _ in $(seq 1 120); do
    if mkdir "$LOCK" 2>/dev/null; then break; fi
    sleep 1
done
trap 'rmdir "$LOCK" 2>/dev/null || true; rm -rf "$STAGE" 2>/dev/null || true' EXIT

STAGE="$(mktemp -d "${TMPDIR:-/tmp}/casper-build.XXXXXX")/Casper.app"
mkdir -p "$STAGE/Contents/MacOS" "$STAGE/Contents/Resources"

cat > "$STAGE/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>               <string>Casper</string>
  <key>CFBundleDisplayName</key>        <string>Casper</string>
  <key>CFBundleIdentifier</key>         <string>com.meditate.casper</string>
  <key>CFBundleExecutable</key>         <string>casper</string>
  <key>CFBundlePackageType</key>        <string>APPL</string>
  <key>CFBundleShortVersionString</key> <string>1.0</string>
  <key>CFBundleVersion</key>            <string>1</string>
  <key>LSMinimumSystemVersion</key>     <string>13.0</string>
  <!-- accessory: floats with your work, never takes a Dock slot -->
  <key>LSUIElement</key>                <true/>
  <key>NSMicrophoneUsageDescription</key>
  <string>Casper listens so you can talk to him instead of typing. Audio is
  recognised on this Mac and never leaves it.</string>
  <!-- Casper shells out to its own python helpers, and some of those ask the
       system which app is in front (to know whether you are mid-meeting) or
       post a notice. Those are Apple Events, and macOS attributes them to
       CASPER. Without this string TCC does not deny the event — it KILLS the
       app: __TCC_CRASHING_DUE_TO_PRIVACY_VIOLATION__ at launch, no stderr,
       exits before it can draw. -->
  <key>NSAppleEventsUsageDescription</key>
  <string>Casper checks which app is in front, so it stays quiet while you are
  in a meeting, and opens your dashboard when you ask.</string>
  <key>NSSpeechRecognitionUsageDescription</key>
  <string>Casper turns what you say into text on this Mac, on-device, so he can
  answer questions about your own work.</string>
</dict>
PLIST
echo '</plist>' >> "$STAGE/Contents/Info.plist"

swiftc -O -o "$STAGE/Contents/MacOS/casper" main.swift Casper.swift Voice.swift \
    -framework Cocoa -framework AVFoundation -framework Speech

# Sign with a REAL identity when the machine has one.
#
# An ad-hoc signature has no stable identity: its cdhash changes with every
# build, so macOS treats each rebuild as a brand-new app and asks for the
# microphone again, every single time. A developer certificate is stable, so
# consent is given once and stays given.
SIGN_ID="${MEDITATE_SIGN_ID:-}"
if [ -z "$SIGN_ID" ]; then
  SIGN_ID=$(security find-identity -v -p codesigning 2>/dev/null \
            | grep "Apple Development" | head -1 \
            | sed -E 's/.*"(.*)"/\1/')
fi
if [ -n "$SIGN_ID" ]; then
  codesign --force --sign "$SIGN_ID" --identifier com.meditate.casper \
           --options runtime --entitlements Casper.entitlements "$STAGE" \
    && echo "signed as: $SIGN_ID"
else
  echo "no developer certificate — signing ad-hoc (macOS will re-ask for the"
  echo "microphone after every rebuild; that is the cost of no identity)"
  codesign --force --sign - --identifier com.meditate.casper \
           --options runtime --entitlements Casper.entitlements "$STAGE"
fi

# The swap: the old bundle only disappears once the new one is ready, and the
# gap is a rename rather than a compile.
OLD="$(mktemp -d "${TMPDIR:-/tmp}/casper-old.XXXXXX")"
[ -d "$APP" ] && mv "$APP" "$OLD/" 2>/dev/null || true
mv "$STAGE" "$APP"
rm -rf "$OLD" 2>/dev/null || true

# TCC binds its decision to a bundle's code signature. Changing the signing
# identity (ad-hoc -> a developer certificate, say) leaves a STALE record, and
# a stale record makes the app abort at launch with
# "must contain an NSSpeechRecognitionUsageDescription key" — while the key is
# sitting right there in the Info.plist. Two crash reports, 0.2s after launch,
# before that was clear. If the signature changed since the last build, clear
# the decisions so the next launch re-reads the plist instead of dying.
SIGFILE="$APP/../.last-signature"
# `set -e` is on: codesign returns non-zero on a bundle it cannot read, which
# is exactly the state this line runs in on a first build. Never let a probe
# abort the build it is only observing.
NOWSIG=$( (codesign -dvvv "$APP" 2>&1 || true) | awk -F= '/^Authority=/{print $2; exit}' )
NOWSIG="${NOWSIG:-unsigned}"
if [ -f "$SIGFILE" ] && [ "$(cat "$SIGFILE")" != "$NOWSIG" ]; then
  echo "signing identity changed — clearing stale privacy decisions"
  tccutil reset SpeechRecognition com.meditate.casper >/dev/null 2>&1 || true
  tccutil reset Microphone com.meditate.casper >/dev/null 2>&1 || true
fi
printf '%s\n' "$NOWSIG" > "$SIGFILE"

cp "$BIN" ./casper          # bare binary stays, for --render and tests
echo "built $APP  ($(stat -f%z "$BIN") bytes)"
codesign -dv "$APP" 2>&1 | grep -E "Identifier|Signature" || true
