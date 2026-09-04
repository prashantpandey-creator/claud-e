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
# Generous: the slowest real step measured is doctor --quick at ~22s,
# and go --auto can legitimately sit while it opens windows. This is a
# stall guard, not a performance budget.
STEP_TIMEOUT="${MEDITATE_STEP_TIMEOUT:-600}"
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
  # The all-goals run advances one step per pass: reads finished results,
  # grows the graph by each RESULT.next, marks blocked / stuck, and sends
  # the next ready wave — only while the owner has said go. Idle otherwise.
  "campaign.py tick"
  "census.py ping"
  # Push the summary-only snapshot to the remote VIEW (a secret gist / your
  # own receiver). Inert unless remote-config.json exists; best-effort by
  # design — `auto` always exits 0 so an offline push never fails the pass.
  "remote.py auto"
  # LAST BUT ONE, and --quick on purpose. It judges the state this pass just
  # left behind, and writes a verdict to doctor.jsonl that the tree reads —
  # without that it is one more thing printed into a log nothing reads, which
  # is how 45 identical osascript failures went unseen. --quick skips the
  # 52-file suite: 11s instead of ~200s, and an hourly suite run would spawn
  # 52 python processes 24 times a day against the owner's own sessions.
  "doctor.py --quick --mend"
  "voice.py --notify --quiet"
  # Reach the owner when he is away: one mail per pass when the run changed
  # (a new item under YOUR HANDS, a step shipped, a stop, a hold). Inert when
  # ~/bin/sendmail or ~/.sendmail.conf is missing; never fails the pass.
  "mail.py --digest --quiet"
  # …and hear him back: replies that carry our nonce, from his address,
  # with dkim=pass. 'done' ticks the item; anything else steers the agent.
  "mail.py --inbox --quiet"
)

# Steps whose nonzero exit is a FINDING, not a breakage.
#
# doctor returns 1 when the install is unhealthy — correct for a person
# running it, wrong for this chain, which is asking "did the step run". Left
# alone it logged `FAILED doctor.py --quick exit=1` every hour for two open
# issues, and an hourly false FAILED is exactly what buries a real one. The
# finding still lands: doctor writes it to doctor.jsonl and the tree reads it.
TOLERANT="doctor.py"

run_all() {
    local started failed=0 rc t0 t1
    started=$(date "+%Y-%m-%d %H:%M:%S")
    echo "═══ heartbeat $started ═══"
    for step in "${STEPS[@]}"; do
        t0=$(date +%s)
        # PER-STEP TIMEOUT. Nothing here was bounded, so one hung step stalled
        # the whole pass forever — and launchd will not start the next pass
        # while this one is still running, so the tool would silently stop
        # grading, repairing, dispatching and self-checking with no failure
        # anywhere to read. That is the dead-lane shape doctor exists to
        # catch, in the thing that RUNS doctor. macOS ships no `timeout(1)`,
        # so this is a background pid and a poll.
        # shellcheck disable=SC2086
        $PY "$SKILL_DIR"/$step 2>&1 &
        step_pid=$!
        waited=0
        while kill -0 "$step_pid" 2>/dev/null; do
            if [ "$waited" -ge "$STEP_TIMEOUT" ]; then
                kill -9 "$step_pid" 2>/dev/null
                echo "── TIMEOUT  ${step}  killed after ${STEP_TIMEOUT}s"
                break
            fi
            sleep 1
            waited=$((waited + 1))
        done
        wait "$step_pid" 2>/dev/null
        rc=$?
        t1=$(date +%s)
        if [ "$rc" -ne 0 ] && [[ " $TOLERANT " == *" ${step%% *} "* ]]; then
            echo "── ${step}  found something (exit=${rc}) — see doctor.jsonl"
        elif [ "$rc" -ne 0 ]; then
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
