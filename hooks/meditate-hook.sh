#!/bin/bash
# meditate-hook.sh — single hook for meditate + the owner's operating rules.
#
# CANONICAL SOURCE: this file, in the meditate repo. install.sh copies it to
# ~/.claude/hooks/meditate-hook.sh. Never edit the installed copy — it gets
# overwritten on the next install.
#
# Replaces the two former hooks (meditate-checkpoint.sh + rules-inject.sh):
# one script, one parse, one decision.
#
# Registered events:
#   SessionStart                                 -> operating rules + nidra census + stillness
#   PreToolUse (Bash)                            -> git/deploy discipline
#   PreToolUse (Write|Edit|MultiEdit)            -> hot-file guard
#
# Stop is deliberately NOT registered — it looped and duplicated the
# SessionStart proof-before-done rule.
#
# CONTRACT: this hook must ALWAYS exit 0 with valid JSON on stdout. A non-zero
# exit or empty stdout makes Claude Code drop every rule silently, which is
# worse than no hook at all — you lose the rules and never find out.

set -uo pipefail   # NOT -e: a failed find/du must not swallow the rules

emit_nothing() { trap - EXIT; printf '{}\n'; exit 0; }
trap 'printf "{}\n"' EXIT   # any unexpected death still emits valid JSON

IN=$(cat 2>/dev/null || echo '{}')

# ---- Presence heartbeat: every tool call, no subprocess ---------------------
# This runs BEFORE the prefilter on purpose. Presence used to be refreshed
# only by Write/Edit, so a session doing shell work went stale inside the hour
# and the timing layer called it "away" — with the session still running. Pure
# bash regex + touch: one syscall, no python, safe on the hot path. Only an
# EXISTING file is touched; creating it is SessionStart's job.
if [[ "$IN" =~ \"session_id\"[[:space:]]*:[[:space:]]*\"([A-Za-z0-9._-]+)\" ]]; then
    _PF="$HOME/.claude/coordination/sessions/${BASH_REMATCH[1]}.json"
    [ -f "$_PF" ] && touch "$_PF" 2>/dev/null
fi

# ---- File events: one decision point, in python ----------------------------
# Every Write/Edit goes to the sangama layer (coordination.py): presence
# recording, collision warnings, graded-fact serving, and the pipeline/native
# guard rules all live THERE — not split between bash and python. It always
# exits 0 and always prints valid hook JSON.
# Where the skill actually lives. install.sh writes this file with the real
# path, because the skill can be cloned anywhere and the hook is copied to
# ~/.claude/hooks/ where it cannot find its own source. Hardcoding
# ~/.claude/skills/meditate meant that any user who cloned elsewhere got a
# hook that silently returned {} forever: no guard rules, no fact serving,
# no collision warnings — installed, reported healthy, and inert.
SKILL_HOME="$(cat "$HOME/.claude/meditation/skill-path" 2>/dev/null)"
[ -n "$SKILL_HOME" ] || SKILL_HOME="$HOME/.claude/skills/meditate"
COORD="$SKILL_HOME/coordination.py"
# PostToolUse on a file write is the squiggly: the file now EXISTS, so it can
# be checked, and additionalContext for this event is shown in the transcript.
# PreToolUse can only see an intent. Fires for every Write/Edit, must stay
# silent unless something is actually wrong.
if [[ "$IN" == *'"PostToolUse"'* ]]; then
    trap - EXIT
    printf '%s' "$IN" | python3 "$COORD" post-edit 2>/dev/null || printf '{}\n'
    exit 0
fi

if [ -f "$COORD" ] && [[ "$IN" == *'"file_path"'* || "$IN" == *'"notebook_path"'* ]]; then
    trap - EXIT
    printf '%s' "$IN" | python3 "$COORD" hook-edit 2>/dev/null || printf '{}\n'
    exit 0
fi

# ---- Trigger set, defined ONCE ---------------------------------------------
# The prefilter and the branches below MUST derive from the same patterns, or
# the prefilter silently swallows events the branches were written to catch.
# (It did: `docker-compose ... --build` and `Foo.SWIFT` both returned {}.)
# File-path rules are NOT here — they moved to coordination.py above.
GIT_PAT='git commit|git push'
DEPLOY_PAT='deploy|docker[ -]?compose up|--build'
TRIGGER="SessionStart|$GIT_PAT|$DEPLOY_PAT"

# ---- Fast prefilter: no subprocess at all for the common irrelevant call ----
shopt -s nocasematch
if ! [[ "$IN" =~ $TRIGGER ]]; then
    shopt -u nocasematch
    emit_nothing
fi
shopt -u nocasematch

# ---- Single parse for event + command + path -------------------------------
PARSED=$(printf '%s' "$IN" | python3 -c '
import sys, json
try: d = json.load(sys.stdin)
except Exception: d = {}
if not isinstance(d, dict): d = {}
ev = d.get("hook_event_name") or d.get("hookEventName") or ""
ti = d.get("tool_input") or d.get("toolInput") or {}
cmd = ti.get("command", "") if isinstance(ti, dict) else ""
path = ""
for k in ("file_path", "notebook_path", "path"):
    v = ti.get(k) if isinstance(ti, dict) else ""
    if v: path = v; break
print(ev); print(str(cmd).replace(chr(10), " ")); print(path)
' 2>/dev/null) || PARSED=$'\n\n'

EV=$(printf '%s\n' "$PARSED" | sed -n 1p)
# Without python3 the hook could not read its own EVENT NAME, so it fell to
# the catch-all branch and emitted `{}` — the session started (fail-open,
# right) with ZERO rules and no complaint. Measured 2026-08-30 with python3
# stubbed to exit 127. The rules are this hook's entire payload and they come
# from a plain file read; they must not depend on an interpreter to be
# DELIVERED. sed can find one field in one line of JSON.
if [ -z "$EV" ]; then
    EV=$(printf '%s' "$IN" | tr -d '\n' \
         | sed -n 's/.*"hook_event_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
fi
CMD=$(printf '%s\n' "$PARSED" | sed -n 2p)
TARGET=$(printf '%s\n' "$PARSED" | sed -n 3p)

MSG=""
case "$EV" in
  SessionStart)
    # YOUR rules if you have written any, otherwise the ones this tool ships.
    #
    # These used to be seven hardcoded lines, two of which were the author's
    # own: instructions about HIS production boxes and iOS app, and a ban on a
    # named LLM provider. Every stranger who installed inherited them, in every
    # session, with no way to edit or remove them. The defaults below are the
    # ones that hold for anybody; anything specific to your work goes in your
    # own file.
    RULES_FILE="${MEDITATE_RULES_FILE:-$HOME/.claude/meditation/rules.md}"
    HAS_OWN_RULES=no
    if [ -r "$RULES_FILE" ]; then
        RULES=$(cat "$RULES_FILE")
        HAS_OWN_RULES=yes
    else
        RULES="OPERATING RULES (must-fire every turn):
1. Voice — few real lines, not narration; decide by leverage and drive, never hand ranked menus; end with one conclusion or the single next step (go/no).
2. Proof before done — no fix OR DIAGNOSIS claimed without live output; \"built, not wired\" != done; a ledger \"fixed\" is a claim, verify the artifact/process. Claim scope = check scope: test the falsifying case, ship the number in the same sentence, label traced-vs-observed.
3. Subtract, never add — fix by removal, not another layer.
4. Plain words — stats/evals in workshop language, not jargon verdicts.
5. Verify the world — external facts are research questions (curl/read/benchmark), don't guess; take the owner's facts about their own domain as given.
6. Ship discipline — commit to a LOCAL branch and STOP; push only on explicit go. Before any push: the repo's own tests green, and the claim proven live rather than traced.
Add your own in $RULES_FILE — that file replaces this list entirely."
    fi

    # --- his OWN standing rules, derived (never hand-copied) -------------
    #
    # rules.md above is HIS file and is never written by this tool. But it is
    # hand-maintained, so it drifts: measured 2026-08-29 it held 7 rules while
    # the memory store held 41, and the 34 it missed were the action-governing
    # ones — worktree discipline, commit-local-first, clean the disk before a
    # training run, one test call before any bulk LLM spend. Those reached the
    # mascot, which talks, and never the dispatched agents, which act.
    #
    # Appended, not merged: a rule he types by hand cannot be deleted by a
    # derivation, and a rule he records in a memory cannot be missed by a
    # stale hand-copy. Newest first, so a later correction supersedes an
    # earlier one instead of the model picking whichever reads louder.
    # Gated on HAVING a rules file. The SHIPPED DEFAULTS must carry nothing
    # that belongs to one person — test_hook has protected that since the
    # seven hardcoded rules (two of them the author's own boxes and a named
    # provider ban) went out to every stranger who installed. A derived block
    # appended to the defaults broke it, correctly: on a shared memory root
    # one person's corrections would reach another person's agents. Someone
    # with their own rules.md is deriving from their own store, so the block
    # is theirs; someone on the defaults gets the defaults, clean.
    DERIVED=""
    [ "$HAS_OWN_RULES" = yes ] && DERIVED=$(python3 - 2>/dev/null <<'PYCREED'
import os, sys
sys.path.insert(0, os.path.expanduser("~/.claude/skills/meditate"))
try:
    import creed
    print(creed.render("action", budget=2600))
except Exception:
    pass
PYCREED
)
    if [ -n "$DERIVED" ]; then
        RULES="$RULES

$DERIVED"
    fi

    # --- Nidra census (sub-second, fail-open) ---
    NIDRA_STATE=$(python3 - 2>/dev/null <<'PYSLOT'
import json, os, sys
p = os.path.expanduser("~/.claude/meditation/nidra_store/memories.jsonl")
if not os.path.exists(p):
    sys.exit(0)
active = 0
statuses = {}
with open(p) as f:
    for line in f:
        if not line.strip():
            continue
        try:
            m = json.loads(line)          # one bad line must not kill the census
        except Exception:
            continue
        if not m.get("active"):
            continue
        active += 1
        s = m.get("epistemic", {}).get("evidence_status", "unverified")
        statuses[s] = statuses.get(s, 0) + 1
if active:
    parts = ["%d graded memories" % active]
    for s in ("machine_checked", "source_linked", "unverified"):
        if statuses.get(s):
            parts.append("%d %s" % (statuses[s], s))
    print("; ".join(parts))
PYSLOT
    ) || NIDRA_STATE=""

    # --- Stillness check (no subprocess unless overdue) ---
    STILLNESS="$HOME/.claude/meditation/STILLNESS.md"
    PROJECTS_DIR="$HOME/.claude/projects"
    CHECKPOINT=""
    NEED_CENSUS=0
    AGE=0

    if [ -f "$STILLNESS" ]; then
        LAST_MOD=$(stat -f %m "$STILLNESS" 2>/dev/null) || LAST_MOD=0
        [ -z "$LAST_MOD" ] && LAST_MOD=0
        if [ "$LAST_MOD" -gt 0 ] 2>/dev/null; then
            AGE=$(( ( $(date +%s) - LAST_MOD ) / 86400 ))
            [ "$AGE" -gt 3 ] && NEED_CENSUS=1
        fi
    else
        NEED_CENSUS=2
    fi

    if [ "$NEED_CENSUS" -ne 0 ] && [ -d "$PROJECTS_DIR" ]; then
        # -maxdepth 2 counts SESSIONS (~/.claude/projects/<slug>/<uuid>.jsonl).
        # Without it, find recurses into per-session subagents/ and reports 670
        # where metrics.py reports 91 — two surfaces disagreeing about one number.
        SC=$( { find "$PROJECTS_DIR" -maxdepth 2 -name '*.jsonl' -type f 2>/dev/null || true; } | wc -l | tr -d ' ' )
        SZ=$( { du -sm "$PROJECTS_DIR" 2>/dev/null || true; } | cut -f1 | tr -d ' ' )
        [ -z "$SC" ] && SC=0
        [ -z "$SZ" ] && SZ=0
        if [ "$NEED_CENSUS" -eq 1 ]; then
            CHECKPOINT="Meditation checkpoint: STILLNESS.md is ${AGE} days old. ${SC} sessions, ${SZ}MB. Run /meditate."
        else
            CHECKPOINT="Meditation checkpoint: No STILLNESS.md found. ${SC} sessions, ${SZ}MB. Run /meditate."
        fi
    fi

    # --- Sangama: live sessions in this repo + fresh drift (fail-open) ---
    EXTRA=""
    if [ -f "$COORD" ]; then
        EXTRA=$(printf '%s' "$IN" | python3 "$COORD" session-start 2>/dev/null) || EXTRA=""
    fi

    MSG="$RULES"
    [ -n "$NIDRA_STATE" ] && MSG="$MSG
Nidra store: $NIDRA_STATE."
    [ -n "$EXTRA" ] && MSG="$MSG
$EXTRA"
    [ -n "$CHECKPOINT" ] && MSG="$MSG
$CHECKPOINT"
    ;;

  PreToolUse)
    shopt -s nocasematch
    if [[ "$CMD" =~ $GIT_PAT ]]; then
      MSG="RULE (fires at git commit/push): push automatically when the repo's own suite is green — the owner gave a standing go 2026-08-25; do not ask. STOP only for destructive/irreversible acts, another session's uncommitted work, or App Store submission. Concurrent sessions: fetch and REBASE, never merge over their work."
    elif [[ "$CMD" =~ $DEPLOY_PAT ]]; then
      MSG="RULE (fires at deploy): verify the artifact by duration + live output, never trust a GREEN report. Prod corpus/DB is shared infrastructure — a write under a live experiment needs a HANDOFF."
    fi
    shopt -u nocasematch
    [ -z "$MSG" ] && emit_nothing
    ;;

  *) emit_nothing ;;
esac

[ -z "$MSG" ] && emit_nothing

OUT=$(MSG_ENV="$MSG" EV_ENV="$EV" python3 -c '
import json, os
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": os.environ["EV_ENV"],
    "additionalContext": os.environ["MSG_ENV"]}}))
' 2>/dev/null) || OUT=""

# PURE-BASH FALLBACK. The payload was assembled by python3, so a broken or
# missing interpreter produced `{}` — the session still started (fail-open,
# correct) but shipped ZERO rules, silently. Measured 2026-08-30 with python3
# stubbed to exit 127: 2 bytes out, "OPERATING RULES" absent, no complaint
# anywhere. The rules are the entire point of this hook and the one payload
# that must survive its most likely dependency failing; the file read that
# produces them needs no python at all.
if [ -z "$OUT" ] && [ -n "$MSG" ]; then
    ESC=$(printf '%s' "$MSG" \
        | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\t/\\t/g' -e 's/\r//g' \
        | awk 'BEGIN{ORS=""} NR>1{print "\\n"} {print}')
    OUT="{\"hookSpecificOutput\":{\"hookEventName\":\"$EV\",\"additionalContext\":\"$ESC\"}}"
fi

trap - EXIT
[ -z "$OUT" ] && emit_nothing
printf '%s\n' "$OUT"
