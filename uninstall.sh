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

# ---- 1b. the kept-warm servers ----------------------------------------------
# These are KeepAlive agents. Step 2 below pkills the processes, which without
# this step launchd simply restarts — "uninstalled" with the voice server back
# up within seconds. Unload the job before killing the process.
for label in com.meditate.tts com.meditate.brain com.meditate.ollama; do
    p="$HOME/Library/LaunchAgents/$label.plist"
    if [ -f "$p" ]; then
        say "stop and remove the kept-warm service ($label)"
        run launchctl unload "$p" 2>/dev/null || true
        run rm -f "$p"
    fi
done

# ---- 2. the running companion -----------------------------------------------
#
# Only what belongs to THIS home, matched on the full installed path.
#
# The header above promises this file never touches anything outside $HOME,
# and for every other step it was true. These three matched a bare substring —
# "Casper.app/Contents", "meditate/tts.py" — so they killed those processes
# for every user on the machine and, more to the point, for every HOME.
# test_packaging.py runs this script with a temp HOME to check that it spares
# other tools' hooks, which means every single run of the test suite shut down
# the owner's live mascot, his Pulse server and his voice server. Measured
# 2026-08-25: pid 4243 before `python3 test_packaging.py`, none after. The
# mascot was not crashing. It was being uninstalled, by its own tests.
case "$SKILL_DIR" in
    "$HOME"/*) OWN="$SKILL_DIR" ;;
    *)         OWN="" ;;   # installed outside $HOME — not ours to kill
esac

stop_mine() {   # stop_mine <path-under-SKILL_DIR> <what to say>
    [ -n "$OWN" ] || return 0
    if pgrep -f "$OWN/$1" >/dev/null 2>&1; then
        say "$2"
        run pkill -f "$OWN/$1" || true
    fi
}
stop_mine "mascot/Casper.app/Contents" "close Casper"
stop_mine "brain.py"                   "stop the Pulse server"
stop_mine "tts.py"                     "stop the voice server"

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
