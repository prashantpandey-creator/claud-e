---
name: meditate
description: >-
  Meditate mode — a stilling pass over the workspace-MIND, where the mind is your
  Claude Code SESSIONS (the coding chats), not your git repos. Modeled on the
  yogic science of calming the citta from our own corpus (Yoga Sutras, Gita). It
  (1) refreshes the memory layer (saṃskāras), (2) reads the session transcripts
  and diagnoses them — each session's sprawl, the threads tangled inside, a
  nirodha / stillness reading, (3) SPLITS the tangled multi-thread sessions into
  small per-thread "continuation chats" each with just enough context to resume,
  and (4) flags finished sessions (merged PRs / concluded work) and offers to
  archive them. Use when the user runs /meditate or says "meditate", "still my
  sessions", "split my chats", "reorganize my sessions into smaller chats",
  "calm the mind", "refresh the memories". Bilingual: Sanskrit term + English
  meaning. The unit is the SESSION/THREAD, not the repository.
argument-hint: "[memory|sessions|<session id or title>|archive|repo]   (no arg = full pass)"
---

# /meditate — Meditate Mode (stilling the workspace-mind)

**The mind is your sessions.** A single Claude Code session sprawls across many
unrelated threads until it is a 35 MB tangle — that is the scattered citta
(mind-field). Each thread is a vṛtti (whirl, YS 1.6). The aim is YS 1.2 — *yogaś
citta-vṛtti-nirodhaḥ*, the stilling of the whirls: not deleting the work, but
**separating the tangle into clean, single-pointed continuation chats**, and
setting down what is already finished.

> **The object is the SESSION, not the repo.** An earlier version of this skill
> wrongly analyzed git repositories. That is the wrong mind. The data lives in
> `~/.claude/projects/<project-slug>/*.jsonl` (one transcript per session). The
> repo lens still exists as an optional adjunct (`/meditate repo`) but is NOT the
> default.

> **Bilingual rule (the user asked for this):** name the Sanskrit term AND its
> plain-English meaning beside it — "nirodha (stilling)", "sprawl (tangle)".

## The vow

WRITE only to: the active project's **memory directory** (Phase A) and
**`~/.claude/meditation/`** (the readings + continuation chats). NEVER move,
rename, or delete a transcript or any project file.

**Authorized actions (with per-item confirmation):**
- **Archive** finished sessions via `mcp__ccd_session_mgmt__archive_session`
  (reversible — sessions can be unarchived). Confirm each.
- (Optional `/meditate repo` only) the **commit-green** protocol — `git add`+
  `commit` already-green work, local only, never push. Confirm each.

## Arguments

- (none) → full pass: Phase A, then Phase B (sessions).
- `memory` → Phase A only.
- `sessions` → Phase B only (diagnose + split, no archiving).
- `<session id or title>` → split just that one session.
- `archive` → only the archive-finished step (Phase B4).
- `repo` → the optional repo lens (scan_projects + still + commit-green).

## Phase 0 — Self-heal ("the code fixes itself")

From `~/.claude/skills/meditate/`, verify the machinery before trusting it:

```bash
python3 test_sessions.py        # the session extractor — the core
python3 test_launch.py          # thread-row parse + archive-candidate detection (Phase C2)
python3 test_nidra_bridge.py    # the nidra grading pipe
python3 test_scan.py && python3 test_still.py   # only if using /meditate repo
```

Must exit 0. If red, read the failing assertions, repair the tool, re-run, THEN
proceed. A split built on a broken parser is worse than none.

## Phase A — Refresh the saṃskāras (the memory)

### A0 — Feed nidra (the grading pipe)

Import all session maps into nidra's evidence-graded store. Run the sleep
(consolidation) pass so memories are deduped, drift-checked, and scheduled:

```bash
python3 ~/.claude/skills/meditate/nidra_bridge.py --sleep --json
```

Read `data.sleep.after` for the graded census. Report `machine_checked` /
`source_linked` / `unverified` counts in the stillness reading.

### A1 — Refresh the memory files

Operate on the active project's memory dir (the one whose `MEMORY.md` is in your
context; for vedic-puran:
`/Users/badenath/.claude/projects/-Users-badenath-projects-vedic-puran/memory/`).
Reflective consolidation: **read** all; **merge** duplicates (delete the loser,
keep `[[links]]`); **fix stale facts** — verify any file/flag/path a memory names
still exists before trusting its advice; convert relative dates to absolute;
**prune** the wrong; **reconcile** the index (one line per file, every pointer
resolves). May invoke a `consolidate-memory` skill if present.

## Phase B — Still the sessions

### B1 — Read the sessions (the tested tool; consume only `data`)

```bash
python3 ~/.claude/skills/meditate/sessions.py --json
```

Never read a raw transcript into context — they reach 37 MB. The tool streams
each and returns a capped, compact record per session: `title`, `sprawl_score`,
`chapter_marks` (explicit thread boundaries), `user_messages` (the human intents,
noise stripped), `files_touched`, `projects`, `ts_start/end`, `counts`,
`last_assistant_text`. Sessions come ranked most-tangled first.

Also call `mcp__ccd_session_mgmt__list_sessions` to get each session's **PR state**
(merged/open) and running status — this drives B4 (what is finished).

For deep work on ONE session use `sessions.py --session "<id|title>" --json`.

### B2 — Write the reading: `~/.claude/meditation/STILLNESS.md`

Bilingual. Cover: how many sessions; total/peak **sprawl (tangle)**; the most
tangled sessions (the giants that most need splitting); how many are **done**
(merged PR) and can be set down; the count of distinct **threads** you found
across the tangled ones.

### B3 — Split tangled sessions into continuation chats (the heart)

For each sprawled, still-live session, write
`~/.claude/meditation/sessions/<idprefix>-<slug>/`:
- an `INDEX.md` listing every thread found inside, each tagged with its vṛtti
  class and status (🟢 live / ✅ done / 🟡 blocked) and the memory that records it;
- one **continuation chat** file per LIVE thread (skip done/settled threads —
  point them at the memory that holds their outcome instead).

**How to find threads** (judgment over the compact map): the `chapter_marks` are
explicit boundaries; between/around them, group `user_messages` by topic shift,
time gap, and the `files_touched` they correspond to. One coherent intent = one
thread.

**Continuation chat shape** (small, paste-able — see the real samples already in
`~/.claude/meditation/sessions/779cd22a-analyze-refine-guruji/`):

```markdown
# <thread title> — continue here
> Thread <i> of <N>, from session "<title>" (<idprefix>), <date>. vṛtti: <class> (<meaning>).

## What this thread is
<1–2 lines>

## Where it stands (from the session)
- <signals: the user intents, files touched, last state>
- Linked memory: [[name]]

## Next step (abhyāsa)
<the single next action — or, if it's a decision thread, say so>

## Files in play
- <paths>

## Start a fresh chat with
cd "<cwd>"
<a one-paragraph kickoff prompt that loads the right context and states the goal>
```

Write a top-level `~/.claude/meditation/sessions/INDEX.md` — table:
session | sprawl | #threads | #live | status | link.

### B4 — Set down the finished (archive, confirmed)

From `list_sessions`, the sessions whose PR is **MERGED** (or that the transcript
shows clearly concluded) are *smṛti* — finished records. List them, and for each
**ask the user**, then archive via `mcp__ccd_session_mgmt__archive_session`.
Never archive an open-PR or running session. Archiving is reversible; still confirm.

## Final report (terse, bilingual)

- **nirodha (stillness):** sessions read, total sprawl, # most-tangled.
- **split:** N tangled sessions → M continuation chats across K threads; where they live.
- **set down:** sessions archived (with confirmation) / left and why.
- memory: read / merged / pruned; index reconciled.
- the one next breath (abhyāsa): which continuation chat to open first.

The continuation chats are the deliverable — do not dump them inline; report the
stillness reading and the single next breath.

## Phase C — Launch & Resume (end-to-end automation)

### C1 — Final suggestions

After the stillness report, produce a **suggest** block:

```
🧘 SUGGEST:
  1. <one-line action> → <which continuation chat>
  2. <one-line action> → <which continuation chat>
  ...
  Open all: python3 ~/.claude/skills/meditate/launch.py
```

### C2 — Auto-launch terminals

`launch.py` reads the meditation session INDEX files and reports on every
thread — live (🟢) and settled (✅) both. For each live thread it extracts the
kickoff prompt from its continuation chat; the working directory is resolved
per-session from that session's own transcript (`find_session_cwd`), NOT
assumed — a thread opens in the repo it actually belongs to.

```bash
python3 ~/.claude/skills/meditate/launch.py           # dry-run: analyse + report only
python3 ~/.claude/skills/meditate/launch.py --open    # actually open the Terminal windows
```

Without `--open` it launches nothing — it prints the live-thread table and the
archive candidates, so you see the plan before any window opens. With `--open`
it opens a new macOS Terminal per live thread (right `cd` + prompt pre-loaded).

**Archive candidates.** A session where *every* thread is settled (✅) is
reported as ready to archive. `launch.py` does **NOT** archive it — a Python
script cannot call an MCP tool. The actual, confirmed archive is the agent's
job in Phase B4 (`mcp__ccd_session_mgmt__archive_session`), after matching the
reported `short_id` to a real `sessionId` via `list_sessions`. The script
computes the WHAT; the agent performs the ACT. If nothing is live and nothing
is settled: "All settled. ✅ Nothing live, nothing to archive."

### C3 — End-to-end flow

```
/meditate
  ├── Phase 0: self-heal (verify tools)
  ├── Phase A: memory refresh
  ├── Phase B: session reading → stillness → split → archive
  ├── Final report: nirodha (stillness) reading
  ├── Phase C1: final SUGGEST block with next actions
  └── Phase C2: python3 launch.py → new terminals open automatically
```

After `/meditate`, the user should be ONE COMMAND away from resuming any live
thread — either the auto-launched terminal or the continuation chat path.
