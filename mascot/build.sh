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

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<'PLIST'
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
  <key>NSSpeechRecognitionUsageDescription</key>
  <string>Casper turns what you say into text on this Mac, on-device, so he can
  answer questions about your own work.</string>
</dict>
PLIST
echo '</plist>' >> "$APP/Contents/Info.plist"

swiftc -O -o "$BIN" main.swift Casper.swift Voice.swift \
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
           --options runtime --entitlements Casper.entitlements "$APP" \
    && echo "signed as: $SIGN_ID"
else
  echo "no developer certificate — signing ad-hoc (macOS will re-ask for the"
  echo "microphone after every rebuild; that is the cost of no identity)"
  codesign --force --sign - --identifier com.meditate.casper \
           --options runtime --entitlements Casper.entitlements "$APP"
fi

# TCC binds its decision to a bundle's code signature. Changing the signing
# identity (ad-hoc -> a developer certificate, say) leaves a STALE record, and
# a stale record makes the app abort at launch with
# "must contain an NSSpeechRecognitionUsageDescription key" — while the key is
# sitting right there in the Info.plist. Two crash reports, 0.2s after launch,
# before that was clear. If the signature changed since the last build, clear
# the decisions so the next launch re-reads the plist instead of dying.
SIGFILE="$APP/../.last-signature"
NOWSIG=$(codesign -dvvv "$APP" 2>&1 | awk -F= '/^Authority=/{print $2; exit}')
if [ -f "$SIGFILE" ] && [ "$(cat "$SIGFILE")" != "$NOWSIG" ]; then
  echo "signing identity changed — clearing stale privacy decisions"
  tccutil reset SpeechRecognition com.meditate.casper >/dev/null 2>&1 || true
  tccutil reset Microphone com.meditate.casper >/dev/null 2>&1 || true
fi
printf '%s\n' "$NOWSIG" > "$SIGFILE"

cp "$BIN" ./casper          # bare binary stays, for --render and tests
echo "built $APP  ($(stat -f%z "$BIN") bytes)"
codesign -dv "$APP" 2>&1 | grep -E "Identifier|Signature" || true
