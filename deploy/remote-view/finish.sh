#!/usr/bin/env bash
# Post-apply finisher for the hosted remote VIEW — the last owner step, in one
# command. After the Render Blueprint is applied (card-free GitHub OAuth), Render
# mints INGEST_TOKEN and VIEW_SECRET (service -> Environment tab). Then run:
#
#   deploy/remote-view/finish.sh <INGEST_TOKEN> <VIEW_SECRET> [BASE_URL]
#
# BASE_URL defaults to the Blueprint's service name. This wires
# ~/.claude/meditation/remote-config.json (so every heartbeat pushes the
# summary), pushes the first snapshot now, and VERIFIES the phone view returns
# the rendered summary over HTTPS. Prints PASS + the phone URL, or FAIL + why.
#
# It only ever WRITES a summary out and READS a status back — remote.py's push()
# has no eval/exec/subprocess, so the host can never command this machine.
set -euo pipefail

ING="${1:?INGEST_TOKEN required — Render service -> Environment}"
VIEW="${2:?VIEW_SECRET required — Render service -> Environment}"
BASE="${3:-https://meditate-remote-view.onrender.com}"
BASE="${BASE%/}"
CFG="${REMOTE_CONFIG:-$HOME/.claude/meditation/remote-config.json}"
REMOTE_PY="$(cd "$(dirname "$0")/../.." && pwd)/remote.py"

mkdir -p "$(dirname "$CFG")"
python3 - "$CFG" "$BASE" "$ING" "$VIEW" <<'PY'
import json, sys
cfg, base, ing, view = sys.argv[1:5]
json.dump({"url": base + "/ingest", "ingest_token": ing,
           "view_secret": view, "view_url": base + "/?k=" + view},
          open(cfg, "w"), indent=2)
print("wired", cfg)
PY

echo "pushing first snapshot -> $BASE/ingest"
python3 "$REMOTE_PY" push --url "$BASE/ingest" --token "$ING"

echo "verifying phone view -> $BASE/?k=<VIEW_SECRET>"
code="$(curl -s -o /tmp/rv-view.$$ -w '%{http_code}' "$BASE/?k=$VIEW")"
if [ "$code" = "200" ] && grep -q "MEDITATE" /tmp/rv-view.$$; then
  rm -f /tmp/rv-view.$$
  echo "PASS — hosted remote VIEW is live and read-only."
  echo "  phone: $BASE/?k=$VIEW"
  exit 0
fi
echo "FAIL — GET returned HTTP $code (expected 200 + rendered summary)."
echo "  body head:"; head -c 200 /tmp/rv-view.$$ 2>/dev/null; echo
rm -f /tmp/rv-view.$$
exit 1
