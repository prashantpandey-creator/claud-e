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
HOOK_SRC="$SKILL_DIR/../../hooks/meditate-checkpoint.sh"
HOOK_DST="$HOME/.claude/hooks/meditate-checkpoint.sh"
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

if [ -f "$HOOK_DST" ]; then
    echo "  [ok]  Hook already at $HOOK_DST"
else
    if [ -f "$HOOK_SRC" ]; then
        cp "$HOOK_SRC" "$HOOK_DST"
        echo "  [ok]  Copied hook to $HOOK_DST"
    else
        echo "  [warn]  Hook source not at expected path ($HOOK_SRC)"
        echo "          If meditate-checkpoint.sh is already at $HOOK_DST, this is fine."
    fi
fi

if [ -f "$HOOK_DST" ]; then
    chmod +x "$HOOK_DST"
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

def has_meditate_hook(entries):
    for entry in entries:
        for h in entry.get("hooks", []):
            if "meditate" in h.get("command", ""):
                return True
    return False

# SessionStart
ss = hooks.setdefault("SessionStart", [])
if not has_meditate_hook(ss):
    ss.append({
        "hooks": [{
            "type": "command",
            "command": hook_cmd,
            "timeout": 10,
            "statusMessage": "Checking meditation checkpoint..."
        }]
    })
    changed = True
    print("  [ok]  SessionStart hook added")
else:
    print("  [ok]  SessionStart hook already registered")

# PreToolUse
ptu = hooks.setdefault("PreToolUse", [])
if not has_meditate_hook(ptu):
    ptu.append({
        "matcher": "Bash",
        "hooks": [{
            "type": "command",
            "command": hook_cmd,
            "timeout": 10,
            "statusMessage": "Meditation checkpoint..."
        }]
    })
    changed = True
    print("  [ok]  PreToolUse hook added")
else:
    print("  [ok]  PreToolUse hook already registered")

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

# ---- 6. Run tests
echo
echo "  Running test suite..."
TEST_PASS=true
for tf in test_sessions.py test_launch.py test_scan.py test_still.py test_doctor.py test_nidra_bridge.py; do
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
    echo "  Use:   /meditate                              (inside Claude Code)"
    echo "         python3 $SKILL_DIR/doctor.py            (health check)"
    echo "         python3 $SKILL_DIR/nidra_bridge.py      (grade sessions)"
    echo "         python3 $SKILL_DIR/launch.py            (see live threads)"
else
    echo "  meditate v${VERSION} installed with test failures."
    echo "  Run: python3 $SKILL_DIR/doctor.py  for details."
fi
echo
