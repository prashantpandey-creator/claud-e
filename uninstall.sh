#!/bin/bash
# meditate uninstall — take it back out, completely and reversibly.
#
# A tool that edits your settings.json, installs a launchd agent and drops a
# shim on your PATH is not honestly packaged until it can remove all three.
# Anything you might still want — the graded store, your goals, the memories —
# is KEPT unless you pass --purge, and --purge tells you exactly what it will
# delete and asks first.
#
#   bash uninstall.sh            remove the wiring, keep the data
#   bash uninstall.sh --purge    also delete the data (asks first)
#   bash uninstall.sh --dry-run  say what would happen, change nothing
#
# It never asks for sudo and never touches anything outside $HOME.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
SETTINGS="$HOME/.claude/settings.json"
HOOK_DST="$HOME/.claude/hooks/meditate-hook.sh"
MEDITATION_DIR="$HOME/.claude/meditation"
BIN="$HOME/.local/bin/meditate"
PLIST="$HOME/Library/LaunchAgents/com.meditate.grade.plist"

PURGE=0
DRY=0
for a in "$@"; do
    case "$a" in
        --purge)   PURGE=1 ;;
        --dry-run) DRY=1 ;;
        *) echo "unknown option: $a"; exit 2 ;;
    esac
done

say() { if [ "$DRY" = 1 ]; then echo "  would $1"; else echo "  $1"; fi; }
run() { if [ "$DRY" = 1 ]; then return 0; fi; "$@"; }

echo
echo "  meditate — uninstall$([ "$DRY" = 1 ] && echo ' (dry run)')"
echo "  ================================"
echo

# ---- 1. the background pass -------------------------------------------------
if [ -f "$PLIST" ]; then
    say "stop and remove the hourly self-check (com.meditate.grade)"
    run launchctl unload "$PLIST" 2>/dev/null || true
    run rm -f "$PLIST"
else
    echo "  [skip] no launchd agent installed"
fi

# Linux (or any machine without launchd) runs the same heartbeat via cron —
# install.sh's fallback. Leaving that line behind after uninstall means the
# heartbeat keeps firing against a skill directory that no longer exists.
if command -v crontab >/dev/null 2>&1 && crontab -l 2>/dev/null | grep -q "meditate-heartbeat"; then
    say "stop and remove the cron heartbeat (meditate-heartbeat)"
    if [ "$DRY" != 1 ]; then
        # `crontab -` reads from STDIN — piping into it was observed, live on
        # a fresh Linux CI runner, to report success while writing an empty
        # table. `crontab <file>` reads from a plain file instead, sidestepping
        # whatever about pipe-into-a-setgid-binary is unreliable here.
        CRON_TMP="$(mktemp)"
        crontab -l 2>/dev/null | grep -v "meditate-heartbeat" > "$CRON_TMP" || true
        crontab "$CRON_TMP" 2>/dev/null || true
        rm -f "$CRON_TMP"
    fi
else
    echo "  [skip] no cron heartbeat installed"
fi

# ---- 2. the running companion -----------------------------------------------
if pgrep -f "Casper.app/Contents" >/dev/null 2>&1; then
    say "close Casper"
    run pkill -f "Casper.app/Contents" || true
fi
if pgrep -f "meditate/brain.py" >/dev/null 2>&1; then
    say "stop the Pulse server"
    run pkill -f "meditate/brain.py" || true
fi
if pgrep -f "meditate/tts.py" >/dev/null 2>&1; then
    say "stop the voice server"
    run pkill -f "meditate/tts.py" || true
fi

# ---- 3. the hooks in settings.json ------------------------------------------
# Edited with python, matched on the hook PATH — a text edit of someone's
# settings.json is how you take their other tools out with you.
if [ -f "$SETTINGS" ]; then
    REMOVED=$(DRY="$DRY" python3 - "$SETTINGS" <<'PY'
import json, os, sys
path = sys.argv[1]
dry = os.environ.get("DRY") == "1"
try:
    with open(path) as f:
        cfg = json.load(f)
except Exception as e:
    print("could not read settings.json (%s) — left untouched" % e)
    sys.exit(0)

hooks = cfg.get("hooks") or {}
removed = 0
for event in list(hooks):
    entries = hooks.get(event) or []
    kept = []
    for entry in entries:
        inner = [h for h in (entry.get("hooks") or [])
                 if "meditate-hook" not in str(h.get("command", ""))]
        if len(inner) != len(entry.get("hooks") or []):
            removed += len(entry.get("hooks") or []) - len(inner)
        if inner:
            entry["hooks"] = inner
            kept.append(entry)
        elif not entry.get("hooks"):
            kept.append(entry)
    if kept:
        hooks[event] = kept
    else:
        hooks.pop(event, None)
if not hooks:
    cfg.pop("hooks", None)
else:
    cfg["hooks"] = hooks

if removed and not dry:
    import shutil
    # back up what is there NOW, then write the edited config
    shutil.copyfile(path, path + ".before-meditate-uninstall")
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
print(removed)
PY
)
    if [ "${REMOVED:-0}" = "0" ]; then
        echo "  [skip] no meditate hooks in settings.json"
    else
        say "remove $REMOVED meditate hook(s) from settings.json (backup kept alongside it)"
    fi
else
    echo "  [skip] no settings.json"
fi

# ---- 4. the hook file and the shim ------------------------------------------
[ -f "$HOOK_DST" ] && { say "remove $HOOK_DST"; run rm -f "$HOOK_DST"; } \
                   || echo "  [skip] no hook file"
[ -e "$BIN" ] && { say "remove $BIN"; run rm -f "$BIN"; } \
              || echo "  [skip] no meditate on PATH"

# ---- 5. the data ------------------------------------------------------------
echo
if [ "$PURGE" = 1 ]; then
    FACTS=0
    [ -f "$MEDITATION_DIR/nidra_store/memories.jsonl" ] && \
        FACTS=$(grep -c . "$MEDITATION_DIR/nidra_store/memories.jsonl" 2>/dev/null || echo 0)
    echo "  --purge will DELETE $MEDITATION_DIR"
    echo "  That is $FACTS graded memories, your goals, and every session split."
    echo "  Your Claude Code transcripts are NOT touched — they live elsewhere."
    if [ "$DRY" = 1 ]; then
        echo "  would delete (dry run — nothing removed)"
    else
        printf "  Type DELETE to confirm: "
        read -r ANSWER
        if [ "$ANSWER" = "DELETE" ]; then
            rm -rf "$MEDITATION_DIR"
            echo "  deleted."
        else
            echo "  kept — nothing deleted."
        fi
    fi
else
    echo "  Your data is KEPT: $MEDITATION_DIR"
    echo "  (graded memories, goals, session splits — re-running install.sh"
    echo "   picks up exactly where you left off. Add --purge to delete it.)"
fi

echo
echo "  The skill directory itself is yours to delete:"
echo "    rm -rf $SKILL_DIR"
echo
