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
SKILL_PATH_FILE="$HOME/.claude/meditation/skill-path"
mkdir -p "$HOME/.claude/meditation"
printf '%s\n' "$SKILL_DIR" > "$SKILL_PATH_FILE"
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
    # The squiggly. PostToolUse is the only event that can look at the RESULT
    # of a write; PreToolUse sees an intent. Its additionalContext shows in
    # the transcript, so a bad reference is corrected in the same turn instead
    # of surfacing as a traceback several turns later.
    ("PostToolUse", "Write|Edit|MultiEdit", 5),
    # Same squiggly for commands, but a DIFFERENT signal: not "the test
    # failed" (the model reads that fine) — "the test ran nothing", which
    # exits 0 and gets counted as green.
    ("PostToolUse", "Bash", 5),
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
# The engine lives in the tool's OWN directory, not scattered into the user's
# home. An existing checkout wins, so nobody's current setup moves.
if [ -d "$HOME/projects/nidra/nidra" ]; then
    NIDRA_ROOT="$HOME/projects/nidra"
else
    NIDRA_ROOT="$MEDITATION_DIR/nidra"
fi
# Prefer the PACKAGE over a git clone. Cloning on every install made this
# tool the loudest visitor to its own engine's repo: 106 clones and "52
# uniques" against 3 page views from 1 person — every number self-inflicted,
# so the only adoption signal nidra had was unreadable. pip installs do not
# touch repo traffic, and a released package is the right way to consume a
# library anyway. Git clone stays as the fallback for a machine without pip
# or before a release exists.
if [ ! -d "$NIDRA_ROOT/nidra" ] && ! python3 -c "import nidra" >/dev/null 2>&1; then
    echo "  Fetching the grading engine (one-time)..."
    if python3 -m pip install --quiet --disable-pip-version-check "nidra-agent-memory>=0.1.0,<0.2.0" >/dev/null 2>&1; then
        echo "  [ok]  grading engine installed (nidra-agent-memory)"
    elif git clone --depth 1 https://github.com/prashantpandey-creator/nidra "$NIDRA_ROOT" >/dev/null 2>&1; then
        echo "  [ok]  grading engine installed (source)"
    else
        echo "  [warn]  could not fetch the grading engine — check your network"
        echo "          meditate works without it; grading stays off until it is present."
    fi
fi
# Record where it landed, so every module resolves the same answer without
# guessing. paths.py reads this file first, after an explicit env override.
if [ -d "$NIDRA_ROOT/nidra" ]; then
    printf '%s\n' "$NIDRA_ROOT" > "$MEDITATION_DIR/nidra-path"
fi
if [ -d "$NIDRA_ROOT/nidra" ]; then
    echo "  [ok]  grading engine ready"
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
    echo "  [warn]  grading engine unavailable — install could not fetch it"
fi

# ---- 5b. Heartbeat — grade runs every 6h without being asked
echo
echo "  Installing heartbeat (grade + archive empties + dashboard, every 6h)..."
PLIST="$HOME/Library/LaunchAgents/com.meditate.rounds.plist"
# A brand-new macOS user has no LaunchAgents directory, so writing the plist
# raised FileNotFoundError and install.sh died at exit 1 — after wiring the
# hook but before running a single test. Caught by installing into a clean
# HOME rather than by reading this file.
mkdir -p "$HOME/Library/LaunchAgents"
# Generated by plistlib, never hand-written XML: the previous heredoc embedded
# raw `>>` and `2>&1` inside <string>, producing a plist launchd tolerated but
# no parser could read (caught 2026-08-22 when `meditate cadence` tried).
python3 - "$SKILL_DIR" "$PLIST" <<'PYPLIST'
import plistlib, sys
skill, plist = sys.argv[1], sys.argv[2]
# ONE step, not a chain. The chain used to be spelled out here AND again in
# the cron fallback below, and the two copies drifted: measured 2026-08-29,
# launchd had 7 steps, cron had 5 (no repair --apply, no go --auto — the only
# stage that acts), and the plist actually on disk had 6. heartbeat.sh owns
# the list now, and it also stamps the time and any nonzero exit, which the
# inline chain could not do: the log held 115 runs and one timestamp.
cmd = '"%s/heartbeat.sh"' % skill
# Keep any interval cadence.py already tuned. Hardcoding the default here
# meant every re-install silently reset the live heartbeat from a tuned 3600s
# back to 21600s — the tuning was real, and re-running the installer threw it
# away with no output. Preserve the existing value; 21600 is only the value
# for a machine that has never had one.
interval = 21600
try:
    with open(plist, "rb") as fh:
        interval = int(plistlib.load(fh).get("StartInterval") or interval)
except (OSError, ValueError, plistlib.InvalidFileException):
    pass
# StartInterval ALONE loses every firing the machine sleeps through, and the
# man page is explicit about it: "If the system is asleep during the time of
# the next scheduled interval firing, that interval will be missed due to
# shortcomings in kqueue(3)." No catch-up, ever. Measured on this machine
# 2026-08-29 from goals-history.jsonl: 60 heartbeat runs in 7 days where
# hourly would be ~168, with dead stretches of 60.9h, 20.8h, 20.7h and 15.4h.
#
# StartCalendarInterval behaves the opposite way — "launchd will start the job
# the next time the computer wakes up", coalescing whatever was missed into
# one run. So keep BOTH: the interval stays the tunable cadence for a machine
# that is awake (cadence.py rewrites it in place and preserves this key), and
# the calendar entries are a floor that guarantees a pass after any sleep.
# Neither key WAKES the machine; that would need pmset, which is the owner's
# call and not something an installer should be scheduling.
floor = [{"Hour": h, "Minute": 7} for h in (7, 19)]
plistlib.dump({"Label": "com.meditate.rounds",
               "ProgramArguments": ["/bin/bash", "-lc", cmd],
               "StartInterval": interval,
               "StartCalendarInterval": floor,
               "RunAtLoad": False},
              open(plist, "wb"))
print("  [ok]  heartbeat interval: %ds%s" % (
    interval, "" if interval == 21600 else " (preserved from cadence tuning)"))
PYPLIST
# Only register with launchd from a REAL home. A suite that runs this script
# with HOME pointed at a tmpdir used to load the plist from there — and that
# registration REPLACES the live one, system-wide. Measured 2026-08-23:
# launchd was holding com.meditate.rounds at a since-deleted
# /private/var/folders/.../T/tmp.XXXX/Library/LaunchAgents/ path, so every
# heartbeat exited 1 and wrote nothing to the log, silently, until someone
# looked. The tool's own tests had killed the thing the tool exists to run.
case "$HOME" in
    /Users/*|/home/*) REAL_HOME=1 ;;
    *)                REAL_HOME=0 ;;
esac
if [ "$REAL_HOME" = "0" ] || [ -n "${MEDITATE_NO_LAUNCHCTL:-}" ]; then
    echo "  [skip]  launchd registration — HOME=$HOME is not a real home."
    echo "          Plist written but NOT loaded: a test must never replace the live agent."
elif command -v launchctl >/dev/null 2>&1; then
    launchctl unload "$PLIST" 2>/dev/null || true
    if launchctl load -w "$PLIST" 2>/dev/null; then
        echo "  [ok]  heartbeat loaded — self-check every 6h (meditate cadence tunes it)"
    else
        echo "  [warn]  could not load launchd agent — run: launchctl load -w $PLIST"
    fi
elif command -v crontab >/dev/null 2>&1; then
    # Linux and anywhere else without launchd. Same chain, same log, every 6h.
    # Existing meditate lines are filtered out first so re-installing does not
    # stack duplicate heartbeats.
    # The SAME script launchd runs. This branch used to spell the chain
    # out again and was missing repair --apply and go --auto, so a
    # machine without launchd never healed its repair queue and never
    # moved any work forward — silently, for as long as it ran.
    HEARTBEAT_CMD="\"$SKILL_DIR/heartbeat.sh\""
    ( crontab -l 2>/dev/null | grep -v "meditate-heartbeat" || true
      echo "0 */6 * * * $HEARTBEAT_CMD # meditate-heartbeat" ) | crontab - 2>/dev/null \
      && echo "  [ok]  heartbeat installed via cron — self-check every 6h" \
      || echo "  [warn]  no launchd and cron refused; run the heartbeat yourself: meditate grade"
else
    echo "  [warn]  no launchd and no cron on this machine — the heartbeat will"
    echo "          not run by itself. Run 'meditate grade' when you want a pass."
fi

# ---- 6. Run tests
echo
echo "  Running test suite..."
TEST_PASS=true
for tf in $(cd "$SKILL_DIR" && ls test_*.py 2>/dev/null | grep -v '^test_doctor\.py$'); do
    if [ -f "$SKILL_DIR/$tf" ]; then
        if python3 "$SKILL_DIR/$tf" > /dev/null 2>&1; then
            echo "  [ok]  $tf"
        else
            echo "  [FAIL]  $tf"
            TEST_PASS=false
        fi
    fi
done

# ---- 6b. First meditation call — the pass is done, show the face
echo
# ---- Census notice — said out loud, not buried in a config file.
# A counter nobody was told about is telemetry. One told plainly, with the
# off switch in the same breath, is a maintainer asking how many people are
# there. It is INERT by default: no endpoint ships, so nothing is sent.
echo
echo "  Counting installs (off by default):"
if python3 "$SKILL_DIR/census.py" status 2>/dev/null | grep -q "census   on"; then
    echo "  [on]  sends: install_id (a random number), version, os, python, day"
    echo "        never: your name, paths, projects, goals, or anything you wrote"
    echo "        stop:  meditate census off      see it: meditate census show"
else
    echo "  [off] nothing is sent — no endpoint is configured."
    echo "        if you ever turn it on: meditate census show  prints the"
    echo "        exact five fields first."
fi

# ---- Census notice — said out loud, not buried in a config file.
# A counter nobody was told about is telemetry. One told plainly, with the
# off switch in the same breath, is a maintainer asking how many people are
# there. INERT by default: no endpoint ships, so nothing is sent.
echo
echo "  Counting installs:"
if python3 "$SKILL_DIR/census.py" status 2>/dev/null | grep -q "census   on"; then
    echo "  [on]  sends: install_id (a random number), version, os, python, day"
    echo "        never: your name, paths, projects, goals, or anything you wrote"
    echo "        stop:  meditate census off       see it: meditate census show"
else
    echo "  [off] nothing is sent — no endpoint is configured."
    echo "        if it is ever turned on, meditate census show prints the"
    echo "        exact five fields before anything leaves."
fi

echo "  Generating the dashboard..."
if python3 "$SKILL_DIR/dashboard.py" > /dev/null 2>&1; then
    echo "  [ok]  ~/.claude/meditation/dashboard.html (regenerates every heartbeat)"
    if [ -t 1 ]; then
        open "$HOME/.claude/meditation/dashboard.html" 2>/dev/null || true
    fi
fi

# ---- 6a. The three local servers become services
#
# Kokoro (voice), ollama (the model) and Pulse (brain.py, the console) used to
# start on demand and die with whatever launched them. That is why the voice
# was cold, fell back to a different speaker mid-conversation, and why an
# answer took seconds instead of milliseconds. Keeping them up is the whole
# difference between this and a hosted API.
#
# ONE LABEL, ONE PROGRAM. com.meditate.brain used to run `ollama serve` —
# because an earlier comment here called ollama "the brain" — while the thing
# actually named brain.py ran unsupervised, started by hand, reparented to
# pid 1. Measured 2026-08-29: `launchctl list` showed com.meditate.brain at
# pid 83090 = `ollama serve`, and brain.py at 21690 with PPID 1. So
# `launchctl kickstart -k gui/$UID/com.meditate.brain` restarted ollama, an
# edit to brain.py never reached the running server, and /api/state kept
# reporting the old code. ollama is now com.meditate.ollama; brain is brain.py.
#
# ProcessType Interactive, deliberately: a first attempt used Background,
# which launchd CPU-throttles, and a render measured at 0.86s by hand took
# 7.8-10.1s as a throttled service.
if [ "$(uname)" = "Darwin" ] && command -v launchctl >/dev/null 2>&1; then
    PY310="$(command -v python3.10 || true)"
    PY3="$(command -v python3 || true)"
    OLLAMA="$(command -v ollama || true)"
    mkdir -p "$HOME/Library/LaunchAgents"
    _svc() {   # label, keepalive(always|crash), program, args...
        local label="$1"; local keep="$2"; shift 2
        local plist="$HOME/Library/LaunchAgents/$label.plist"
        python3 - "$plist" "$label" "$keep" "$@" <<'PYSVC'
import plistlib, sys
plist, label, keep = sys.argv[1], sys.argv[2], sys.argv[3]
# "crash": restart on failure only. A server that exits 0 because its port is
# already held by a hand-started copy must not be respawned forever.
d = {"Label": label, "ProgramArguments": sys.argv[4:],
     "RunAtLoad": True,
     "KeepAlive": True if keep == "always" else {"SuccessfulExit": False},
     "ProcessType": "Interactive", "Nice": -5,
     "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/bin:/bin"},
     "StandardOutPath": "/tmp/%s.log" % label,
     "StandardErrorPath": "/tmp/%s.log" % label}
with open(plist, "wb") as f:
    plistlib.dump(d, f)
PYSVC
        launchctl unload "$plist" 2>/dev/null || true
        launchctl load -w "$plist" 2>/dev/null \
            && echo "  [ok]  $label kept warm" \
            || echo "  [--]  $label could not be loaded"
    }

    # Migration: retire a com.meditate.brain that still runs ollama, so the
    # label is free for the program it names.
    _OLD_BRAIN="$HOME/Library/LaunchAgents/com.meditate.brain.plist"
    if [ -f "$_OLD_BRAIN" ] && \
       plutil -p "$_OLD_BRAIN" 2>/dev/null | grep -q '"ollama"\|/ollama"'; then
        launchctl unload "$_OLD_BRAIN" 2>/dev/null || true
        rm -f "$_OLD_BRAIN"
        echo "  [ok]  com.meditate.brain no longer points at ollama (migrated)"
    fi

    if [ -n "$PY310" ] && [ -f "$SKILL_DIR/tts.py" ]; then
        _svc com.meditate.tts always "$PY310" "$SKILL_DIR/tts.py" --serve
    else
        echo "  [--]  voice server needs python3.10 (onnxruntime has no 3.14 wheel)"
    fi
    if [ -n "$OLLAMA" ]; then
        _svc com.meditate.ollama always "$OLLAMA" serve
    else
        echo "  [--]  no ollama — answers fall back to the slow path"
    fi
    if [ -n "$PY3" ] && [ -f "$SKILL_DIR/brain.py" ]; then
        # A hand-started Pulse holds port 7711 and would make the service exit
        # 0 on load; stop ours first so the supervised copy is the live one.
        case "$SKILL_DIR" in
            "$HOME"/*) pkill -U "$(id -u)" -f "$SKILL_DIR/brain.py" 2>/dev/null || true ;;
        esac
        _svc com.meditate.brain crash "$PY3" "$SKILL_DIR/brain.py" --no-open
    fi
fi

# ---- 6b. Casper appears
# The install should END with someone standing there, not with a list of
# commands to memorise. He builds once, gathers himself out of nothing on
# screen, and introduces himself — so the first thing a new user meets is the
# companion, not the CLI.
if [ -t 1 ] && [ "$(uname)" = "Darwin" ] && command -v swiftc >/dev/null 2>&1; then
    echo
    echo "  Waking Casper..."
    if bash "$SKILL_DIR/mascot/build.sh" >/dev/null 2>&1; then
        # first run only: clear the flag so he actually makes an entrance
        defaults delete com.meditate.casper hasArrived >/dev/null 2>&1 || true
        open "$SKILL_DIR/mascot/Casper.app" 2>/dev/null \
            && echo "  [ok]  Casper is on your screen, bottom-right. He'll say hello."
    else
        echo "  [--]  Casper needs Xcode command line tools; skipped."
        echo "        Run 'meditate casper' once you have them."
    fi
fi

# ---- 7. Report
echo
echo "  ================================"
if [ "$TEST_PASS" = true ]; then
    echo "  meditate v${VERSION} installed. All tests green."
    echo
    echo "  Your rules: ~/.claude/meditation/rules.md — write that file and it"
    echo "  replaces the defaults this tool ships. Nothing personal is baked in."
    echo
    echo "  Casper is the product — talk to him. From here:"
    echo "         meditate            where am I + the one next action"
    echo "         meditate go         move everything forward"
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
