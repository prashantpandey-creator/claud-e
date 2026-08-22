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

# Ad-hoc signature: enough for TCC to pin consent to this bundle, so the user
# is asked once instead of on every rebuild.
codesign --force --sign - --identifier com.meditate.casper \
         --options runtime "$APP" 2>/dev/null \
  || codesign --force --sign - --identifier com.meditate.casper "$APP"

cp "$BIN" ./casper          # bare binary stays, for --render and tests
echo "built $APP  ($(stat -f%z "$BIN") bytes)"
codesign -dv "$APP" 2>&1 | grep -E "Identifier|Signature" || true
