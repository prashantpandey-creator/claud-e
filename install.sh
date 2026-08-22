#!/bin/bash
# meditate install — one-command setup.
#
# What it does:
#   1. Checks prerequisites (Python 3.9+, Claude Code)
#   2. Ensures the hook file exists and is executable
#   3. Wires the hook into ~/.claude/settings.json (SessionStart + PreToolUse)
#   4. Creates the meditation output directory
#   5. Runs the test suite
#   6. Reports
#
# It never asks for sudo. Everything it writes goes inside ~/.claude/.
# Safe to re-run — it skips steps that are already done.

set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
# The hook lives IN the repo — that is the source of truth. It used to be read
# from ~/.claude/hooks/ (untracked), so the merged hook existed on exactly one
# machine and a fresh install wired a filename that no longer existed.
HOOK_SRC="$SKILL_DIR/hooks/meditate-hook.sh"
HOOK_DST="$HOME/.claude/hooks/meditate-hook.sh"
BIN_DIR="$HOME/.local/bin"
SETTINGS="$HOME/.claude/settings.json"
MEDITATION_DIR="$HOME/.claude/meditation"
SESSIONS_DIR="$MEDITATION_DIR/sessions"
VERSION=$(cat "$SKILL_DIR/VERSION" 2>/dev/null || echo "unknown")

echo
echo "  meditate v${VERSION} — install"
echo "  ================================"
echo

# ---- 1. Prerequisites
echo "  Checking prerequisites..."

if ! command -v python3 >/dev/null 2>&1; then
    echo "  ERROR: Python 3 is not installed (or not on your PATH)."
    echo "  Install it from https://python.org — 3.9 or newer — then re-run."
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 9 ]; }; then
    echo "  ERROR: Python $PY_VERSION found, but 3.9+ is required."
    exit 1
fi
echo "  [ok]  Python $PY_VERSION"

if command -v claude >/dev/null 2>&1; then
    echo "  [ok]  Claude Code found"
else
    echo "  [warn]  Claude Code not on PATH — /meditate works inside Claude Code sessions"
fi

# ---- 2. Hook file
echo
echo "  Setting up hook..."
mkdir -p "$(dirname "$HOOK_DST")"

# Always copy when the source differs. The old "skip if it exists" branch meant
# an upgrade never shipped a new hook — the stale one lived forever.
if [ -f "$HOOK_SRC" ]; then
    if [ -f "$HOOK_DST" ] && cmp -s "$HOOK_SRC" "$HOOK_DST"; then
        echo "  [ok]  Hook current at $HOOK_DST"
    else
        cp "$HOOK_SRC" "$HOOK_DST"
        chmod +x "$HOOK_DST"
        echo "  [ok]  Installed hook -> $HOOK_DST"
    fi
else
    echo "  [ERROR]  Hook source missing: $HOOK_SRC"
    exit 1
fi

# Retire the two hooks this one replaced, so they cannot fire alongside it.
for old in meditate-checkpoint.sh rules-inject.sh; do
    if [ -f "$HOME/.claude/hooks/$old" ]; then
        mv "$HOME/.claude/hooks/$old" "$HOME/.claude/hooks/$old.retired"
        echo "  [ok]  Retired $old"
    fi
done

# ---- 2b. CLI on PATH
echo
echo "  Putting 'meditate' on your PATH..."
mkdir -p "$BIN_DIR"
if [ -f "$SKILL_DIR/meditate" ]; then
    chmod +x "$SKILL_DIR/meditate"
    ln -sf "$SKILL_DIR/meditate" "$BIN_DIR/meditate"
    echo "  [ok]  $BIN_DIR/meditate -> $SKILL_DIR/meditate"
    case ":$PATH:" in
        *":$BIN_DIR:"*) : ;;
        *) echo "  [warn]  $BIN_DIR is not on your PATH. Add to ~/.zshrc:"
           echo "          export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
    esac
else
    echo "  [warn]  CLI wrapper not found at $SKILL_DIR/meditate"
fi

# ---- 3. Wire into settings.json
echo
echo "  Wiring hooks into settings.json..."

if [ ! -f "$SETTINGS" ]; then
    echo '{}' > "$SETTINGS"
fi

python3 - "$SETTINGS" "$HOOK_DST" << 'PYTHON_WIRE'
import json, sys

settings_path = sys.argv[1]
hook_cmd = f"bash {sys.argv[2]}"

with open(settings_path) as f:
    settings = json.load(f)

hooks = settings.setdefault("hooks", {})
changed = False

# Every registration this hook owns. Matched by matcher, not by "is some
# meditate hook present?" — the old check saw ANY meditate command and
# declared victory, so a stale path was never repaired and the third
# registration (Write|Edit|MultiEdit) was never added at all.
WANTED = [
    ("SessionStart", None, 10),
    ("PreToolUse", "Bash", 5),
    ("PreToolUse", "Write|Edit|MultiEdit", 5),
]

def is_ours(entry):
    for h in entry.get("hooks", []):
        c = h.get("command", "")
        if "meditate-hook.sh" in c or "meditate-checkpoint.sh" in c or "rules-inject.sh" in c:
            return True
    return False

for event, matcher, timeout in WANTED:
    entries = hooks.setdefault(event, [])
    # Drop any prior registration of ours for this matcher (incl. retired hooks).
    kept = [e for e in entries if not (is_ours(e) and e.get("matcher") == matcher)]
    if len(kept) != len(entries):
        changed = True
    entry = {"hooks": [{"type": "command", "command": hook_cmd, "timeout": timeout}]}
    if matcher:
        entry["matcher"] = matcher
    kept.append(entry)
    hooks[event] = kept
    changed = True
    print(f"  [ok]  {event}{' (' + matcher + ')' if matcher else ''} registered")

# Purge registrations pointing at the two retired hooks, wherever they sit.
for event, entries in list(hooks.items()):
    pruned = []
    for e in entries:
        cmds = [h.get("command", "") for h in e.get("hooks", [])]
        if any("meditate-checkpoint.sh" in c or "rules-inject.sh" in c for c in cmds):
            print(f"  [ok]  Removed retired hook from {event}")
            changed = True
            continue
        pruned.append(e)
    hooks[event] = pruned

if changed:
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
PYTHON_WIRE

# ---- 4. Meditation directory
echo
echo "  Setting up output directory..."
mkdir -p "$SESSIONS_DIR"
echo "  [ok]  $MEDITATION_DIR"

# ---- 5. Nidra grading engine
echo
echo "  Checking nidra grading engine..."
NIDRA_ROOT="$HOME/projects/nidra"
if [ -d "$NIDRA_ROOT/nidra" ]; then
    echo "  [ok]  nidra at $NIDRA_ROOT"
    # Run initial bridge to populate the graded store
    if python3 "$SKILL_DIR/nidra_bridge.py" --sleep > /dev/null 2>&1; then
        NIDRA_COUNT=$(python3 -c "
import json, os
p = os.path.expanduser('~/.claude/meditation/nidra_store/memories.jsonl')
if os.path.exists(p):
    n = sum(1 for l in open(p) if l.strip() and json.loads(l).get('active'))
    print(n)
else:
    print(0)
" 2>/dev/null || echo 0)
        echo "  [ok]  nidra store: $NIDRA_COUNT graded memories"
    else
        echo "  [warn]  nidra bridge failed — run manually: python3 $SKILL_DIR/nidra_bridge.py --sleep"
    fi
else
    echo "  [warn]  nidra not found at $NIDRA_ROOT — grading disabled"
    echo "          Get it: git clone https://github.com/prashantpandey-creator/nidra ~/projects/nidra"
fi

# ---- 5b. Heartbeat — grade runs every 6h without being asked
echo
echo "  Installing heartbeat (launchd, every 6h)..."
PLIST="$HOME/Library/LaunchAgents/com.meditate.grade.plist"
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.meditate.grade</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>-lc</string>
    <string>python3 "$SKILL_DIR/nidra_bridge.py" --sleep >> "$HOME/.claude/meditation/heartbeat.log" 2>&1</string></array>
  <key>StartInterval</key><integer>21600</integer>
  <key>RunAtLoad</key><false/>
</dict></plist>
PLIST_EOF
launchctl unload "$PLIST" 2>/dev/null || true
if launchctl load -w "$PLIST" 2>/dev/null; then
    echo "  [ok]  heartbeat loaded — grade + drift + repair queue every 6h"
else
    echo "  [warn]  could not load launchd agent — run: launchctl load -w $PLIST"
fi

# ---- 6. Run tests
echo
echo "  Running test suite..."
TEST_PASS=true
for tf in test_sessions.py test_launch.py test_scan.py test_still.py test_doctor.py test_nidra_bridge.py test_metrics.py test_hook.py test_coordination.py test_archive.py test_report.py test_goals.py test_ask.py test_formation.py test_integration.py; do
    if [ -f "$SKILL_DIR/$tf" ]; then
        if python3 "$SKILL_DIR/$tf" > /dev/null 2>&1; then
            echo "  [ok]  $tf"
        else
            echo "  [FAIL]  $tf"
            TEST_PASS=false
        fi
    fi
done

# ---- 7. Report
echo
echo "  ================================"
if [ "$TEST_PASS" = true ]; then
    echo "  meditate v${VERSION} installed. All tests green."
    echo
    echo "  Use:   /meditate            (inside Claude Code)"
    echo "         meditate             (health check)"
    echo "         meditate grade       (scan + grade + consolidate)"
    echo "         meditate metrics     (drift, coverage, health)"
    echo "         meditate launch      (see live threads)"
else
    echo "  meditate v${VERSION} installed with test failures."
    echo "  Run: python3 $SKILL_DIR/doctor.py  for details."
fi
echo
