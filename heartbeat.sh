#!/bin/bash
# heartbeat.sh — the periodic pass, in ONE place.
#
# WHY THIS FILE EXISTS (measured 2026-08-29):
#
# The step list lived in three copies and all three disagreed:
#
#   install.sh, launchd branch   7 steps
#   install.sh, cron branch      5 steps — no repair --apply, no go --auto
#   the plist actually on disk   6 steps — no census ping
#
# The cron branch is what runs on a machine without launchd, and it was
# missing the self-healing repair pass AND `go --auto`, which is the only
# stage in the chain that ACTS. So on Linux the heartbeat read the world
# every six hours and never moved anything, silently. A list that is copied
# is a list that drifts; there is one copy now and both installers call it.
#
# It also fixes what the inline chain could never do:
#
#   - the log had 115 runs and ONE timestamp in 219 KB, so you could not tell
#     when a pass ran or whether one was skipped
#   - the chain is `{ a; b; c; } >> log 2>&1` with no `set -e` and no exit
#     capture, so a step that started failing would leave no trace at all
#   - the log grew without bound
#
# Run it by hand any time: ~/.claude/skills/meditate/heartbeat.sh
set -uo pipefail          # NOT -e: one failing step must not skip the rest

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="${MEDITATE_HEARTBEAT_LOG:-$HOME/.claude/meditation/heartbeat.log}"
MAX_LINES="${MEDITATE_HEARTBEAT_LOG_LINES:-4000}"
PY="${MEDITATE_PYTHON:-python3}"

mkdir -p "$(dirname "$LOG")"

# The list. Order matters: repair BEFORE grade, so a memory whose file merely
# moved is repointed and then re-graded in the same pass and the queue clears
# itself. go --auto is the only stage that acts; it holds while the owner is
# at the keyboard and says so when it declines. census ping is inert unless an
# endpoint is configured.
STEPS=(
  "repair.py --apply"
  "nidra_bridge.py --sleep"
  "archive.py --apply"
  "dashboard.py"
  "go.py --auto"
  "census.py ping"
  "voice.py --notify --quiet"
)

run_all() {
    local started failed=0 rc t0 t1
    started=$(date "+%Y-%m-%d %H:%M:%S")
    echo "═══ heartbeat $started ═══"
    for step in "${STEPS[@]}"; do
        t0=$(date +%s)
        # shellcheck disable=SC2086
        $PY "$SKILL_DIR"/$step 2>&1
        rc=$?
        t1=$(date +%s)
        if [ "$rc" -ne 0 ]; then
            failed=$((failed + 1))
            # A nonzero exit is the whole reason for this file. Loud, on its
            # own line, greppable: `grep FAILED heartbeat.log`.
            echo "── FAILED  ${step}  exit=${rc}  after $((t1 - t0))s"
        fi
    done
    echo "═══ done $(date "+%H:%M:%S") · ${#STEPS[@]} steps · ${failed} failed ═══"
    return 0
}

run_all >> "$LOG" 2>&1

# Cap the log. It reached 219 KB of untimestamped output before anyone looked;
# a record nobody can read is not a record. Newest lines are the ones worth
# keeping, so trim from the front.
if [ -f "$LOG" ]; then
    lines=$(wc -l < "$LOG" | tr -d ' ')
    if [ "$lines" -gt "$MAX_LINES" ]; then
        tail -n "$MAX_LINES" "$LOG" > "$LOG.trim" && mv "$LOG.trim" "$LOG"
    fi
fi
