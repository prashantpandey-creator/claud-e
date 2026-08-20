# Meditate

**Still your sessions. Resume any thread.**

Meditate is a stilling pass over your Claude Code workspace. It reads the tangled,
sprawling sessions that accumulate over weeks of work, diagnoses them, splits the
multi-thread tangles into clean single-purpose continuation chats, and sets down
the finished work. When it's done, you are one command away from resuming any
live thread.

Everything runs locally. Nothing is uploaded. Nothing is written outside your
own `~/.claude/` directory.

---

## Install

```
sh ~/.claude/skills/meditate/install.sh
```

That's the whole thing. It checks prerequisites, wires the hook into your Claude
Code settings, runs the test suite, and reports. No sudo, no network, no account.

Already have `/meditate` working? Run `python3 ~/.claude/skills/meditate/doctor.py`
to check health.

---

## Use

Inside any Claude Code session:

```
/meditate              full pass: refresh memory, read sessions, split, archive
/meditate memory       refresh the memory layer only
/meditate sessions     read and split sessions only
/meditate archive      archive finished sessions only
/meditate repo         optional repo lens (git state + yogic diagnosis)
```

Or from a terminal, check status:

```
python3 ~/.claude/skills/meditate/doctor.py          # health check
python3 ~/.claude/skills/meditate/sessions.py --json # raw session data
python3 ~/.claude/skills/meditate/launch.py          # see live threads
python3 ~/.claude/skills/meditate/launch.py --open   # open them in Terminal
```

---

## What it does

### Phase 0 — Self-heal
Runs all 14 tests. If any fail, stops and reports. A split built on a broken
parser is worse than none.

### Phase A — Refresh the memory
Reads every memory file. Merges duplicates, fixes stale facts, prunes the dead,
reconciles the index. The saṃskāras (imprints) — cleaned.

### Phase B — Still the sessions
1. **Read** — `sessions.py` streams every transcript (some are 35 MB) and emits
   a compact map: title, sprawl score, chapters, user intents, files touched.
2. **Diagnose** — writes a stillness reading to `~/.claude/meditation/STILLNESS.md`.
   How many sessions, total sprawl, the worst tangles, what's finished.
3. **Split** — for each tangled session, writes continuation chats to
   `~/.claude/meditation/sessions/<id>/`. Each chat is a paste-able prompt with
   context, next step, and files in play.
4. **Set down** — lists sessions whose PRs are merged. Archives with confirmation.

### Phase C — Launch
Opens a macOS Terminal window per live thread, pre-loaded with the right `cd` and
kickoff prompt. Or prints the plan first (dry-run is the default).

---

## The hook

`meditate-checkpoint.sh` fires on every Claude Code session start and on
git commit/push/deploy commands. It checks the age of your last stillness reading.
When overdue (default: 3 days), it nudges — injected fresh into context, not
buried 286 rows deep.

The hook is what keeps the practice alive. Without it, meditate is a tool you
forget to run. With it, the tool remembers for you.

---

## Health check

```
python3 ~/.claude/skills/meditate/doctor.py
```

Reports:
- Prerequisites (Python 3, Claude Code)
- Test suite status (14 tests across 4 files)
- STILLNESS.md age and whether it's overdue
- Hook registration in settings.json
- Meditation output directory state
- Live thread count and archive candidates

Add `--json` for the envelope.

---

## How it works (for developers)

| File | What it does | Tests |
|---|---|---|
| `sessions.py` | Streams transcripts, emits compact session maps | `test_sessions.py` (5 assertions) |
| `scan_projects.py` | Discovers git repos and workspaces | `test_scan.py` (5 assertions) |
| `still.py` | Yogic diagnosis: vritti/antaraya/nirodha | `test_still.py` (4 assertions) |
| `launch.py` | Opens Terminal windows per live thread | `test_launch.py` (5 assertions) |
| `doctor.py` | Self-diagnostic, JSON envelope | `test_doctor.py` |

Every tool returns a JSON envelope: `{success, data, metadata, errors}`. The
skill consumes only `data` — raw transcript content never enters context.

See `INTERNALS.md` for the vritti classification rules, antaraya definitions,
and nirodha/stillness index formula.

---

## Requirements

- Python 3.9+
- Claude Code installed and working
- macOS (for Terminal auto-launch; everything else is cross-platform)

---

## Philosophy

The model is yogic, drawn from the Yoga Sutras (YS 1.2): *yogaś citta-vṛtti-nirodhaḥ*
— the stilling of the whirls of the mind-field. The mind is your sessions. Each
tangled thread is a vṛtti (whirl). The practice is not deleting work but separating
it into clean, single-pointed threads, and setting down what is already finished.

Every Sanskrit term appears with its English meaning beside it. The vocabulary is
real, grounded in our own corpus — never decorative.
