# Meditate

**Evidence-graded AI agent memory with drift detection.**

Meditate is a memory system for Claude Code sessions. It mines your transcripts,
grades every memory with SHA-256 evidence receipts, detects when the world
changes and silently stops serving stale knowledge, consolidates what you know,
and injects graded context fresh into every session — not buried 286 rows deep.

When a fact drifts, it stops being a fact. Meditate catches it.

```
90 sessions scanned
82 memories imported
68 machine-checked (evidence verified against live source files)
 3 unverified
11 tombstoned (stale, no evidence, pruned)
 0 stale answers served
```

---

## Install

```bash
bash ~/.claude/skills/meditate/install.sh
```

One command. Checks prerequisites, wires the hooks, runs the tests, reports.
No sudo, no network, no account. Safe to re-run.

---

## What it does

### Mine — one pass, not three

`sessions.py` streams every transcript (some are 35 MB) line-by-line and emits
a compact map per session: title, sprawl score, chapter marks, user intents,
files touched. The raw transcript never enters context.

```bash
python3 ~/.claude/skills/meditate/sessions.py --json
```

### Grade — every memory carries its receipt

When a memory is stored, the text that supported it is hashed (SHA-256) and
saved alongside as an *evidence receipt*. The receipt points at the source file
and stores the exact excerpt.

Three tiers:

| Grade | Meaning | Serves? |
|---|---|---|
| `machine_checked` | evidence re-verified against live source bytes | Yes |
| `source_linked` | evidence recorded, source not checkable right now | Maybe |
| `unverified` | no evidence, or evidence drifted | No |

### Detect drift — at serve time, not on a schedule

Every time a memory is recalled, its evidence is **re-checked against the
source file**. If the source was edited, moved, or deleted since the memory was
stored, the excerpt won't match. The memory silently stops serving. No TTL
guesswork. The world changed — the answer is no longer trustworthy.

```python
# grade.py — the two checks per evidence row
def verify_evidence_row(ev):
    # 1. Integrity: does the excerpt match its own sha256?
    if sha256_text(excerpt) != ev["sha256"]:
        return "corrupt"
    # 2. Reality: does the source still contain the excerpt?
    if excerpt not in source_content:
        return "drifted"
    return "ok"
```

### Consolidate — the sleep pass

Five deterministic stages, run once per `/meditate`:

1. **Dedup** — same normalized statement → merge into the oldest, union evidence
2. **Verify** — re-check every evidence row, re-grade. Drifted = demoted.
3. **Contradict** — same subject, negated or numerically conflicting → both flagged
4. **Schedule** — spaced-repetition: each clean re-check pushes the next review out
5. **Prune** — evidence-free, low-confidence, long-overdue → tombstoned (never deleted)

Running the pass twice in an unchanged world produces zero actions.

```bash
python3 ~/.claude/skills/meditate/nidra_bridge.py --sleep --json
```

### Inject — fresh, not buried

Two hooks fire operating rules and memory state into every session:

- **`meditate-checkpoint.sh`** — fires on SessionStart and git operations.
  Checks when you last ran `/meditate`. When overdue (3 days), nudges.
- **`rules-inject.sh`** — fires on SessionStart, PreToolUse, Stop.
  Injects operating rules + nidra graded memory census directly into context
  via `additionalContext` — the channel that beats burial.

Every new session sees:
```
Nidra store: 71 graded memories; 68 machine_checked; 3 unverified.
```

### Still — split tangles into threads

For tangled, sprawling sessions, meditate splits them into clean single-purpose
continuation chats — each with context, next step, and files in play. Paste one
into a new session and resume exactly where you left off.

```
~/.claude/meditation/sessions/<id>/
├── INDEX.md                    # all threads found
├── thread-1-drift-detection.md # one thread, resumable
├── thread-2-nidra-bridge.md    # another thread
└── thread-3-packaging.md       # another
```

### Diagnose — know what's working

```bash
python3 ~/.claude/skills/meditate/doctor.py
```

```
meditate doctor v0.2.0
========================================

Prerequisites:
  [     ok]  python3         Python 3.14.6
  [     ok]  claude_code     found on PATH

Tests: all green
  [     ok]  test_sessions.py          green
  [     ok]  test_launch.py            green
  [     ok]  test_scan.py              green
  [     ok]  test_still.py             green
  [     ok]  test_nidra_bridge.py      green

Hook:
  [     ok]  hook file exists
  [     ok]  hook executable
  [     ok]  SessionStart registered
  [     ok]  PreToolUse registered

Stillness:
  [     ok]  STILLNESS.md — 1.2 days old (threshold: 3d)

Nidra store:
  total: 82  active: 71
    machine_checked: 68
    unverified: 3

========================================
Healthy. All systems nominal.
```

---

## Use

Inside any Claude Code session:

```
/meditate              full pass: grade memories, read sessions, split, archive
/meditate memory       grade and refresh the memory layer only
/meditate sessions     read and split sessions only
/meditate archive      archive finished sessions only
```

From a terminal:

```bash
# Health check
python3 ~/.claude/skills/meditate/doctor.py

# Raw session data (JSON envelope)
python3 ~/.claude/skills/meditate/sessions.py --json

# Grade all sessions into nidra (with consolidation)
python3 ~/.claude/skills/meditate/nidra_bridge.py --sleep

# See live threads
python3 ~/.claude/skills/meditate/launch.py

# Open them in Terminal windows
python3 ~/.claude/skills/meditate/launch.py --open
```

---

## Architecture

```
Session transcripts (734 MB, 90 sessions)
        │
        ▼
   sessions.py          ← mine once, emit compact maps
        │
        ▼
   nidra_bridge.py      ← import into evidence-graded store
        │
        ├── store.py         memories.jsonl + journal.jsonl
        ├── grade.py         SHA-256 drift detection
        ├── sleep.py         5-stage consolidation
        └── recall.py        answer cache with receipts
        │
        ▼
   rules-inject.sh      ← structured injection (rules + memory state)
        │
        ├── SessionStart     operating rules + nidra census
        ├── PreToolUse       git/deploy/pipeline discipline
        └── Stop             (disabled — was causing loops)
        │
        ▼
   doctor.py             ← self-diagnostic (prereqs, tests, hooks, nidra)
```

### Pipeline stages — what covers what

| Stage | Tool | What happens |
|---|---|---|
| **Mine** | `sessions.py` | Stream transcripts → compact maps (title, sprawl, chapters, intents, files) |
| **Grade** | `nidra/grade.py` | SHA-256 evidence receipts, 3-tier trust (machine_checked / source_linked / unverified) |
| **Store** | `nidra/store.py` | JSONL store + append-only journal. Never destroys — supersedes and tombstones. |
| **Consolidate** | `nidra/sleep.py` | Dedup → verify → contradict → schedule → prune. Idempotent. |
| **Retrieve** | `nidra/recall.py` | Exact key + fuzzy match. Re-grades at serve time — drift stops serving. |
| **Inject** | `rules-inject.sh` | `additionalContext` channel — fresh at the event, zero decay. |
| **Diagnose** | `doctor.py` | Tests, hooks, stillness age, nidra census. JSON envelope. |
| **Still** | SKILL.md Phase B | Split tangled sessions → single-thread continuation chats. |
| **Launch** | `launch.py` | Open Terminal windows per live thread. |

### Adapters — what plugs in

| Source | Adapter | Evidence |
|---|---|---|
| Claude Code sessions | `nidra/adapters/meditate.py` | First user message, SHA-256'd, transcript path |
| MemPalace drawers | `nidra/adapters/mempalace.py` | Escape-proof anchor from drawer text, source file path |
| Direct answers | `nidra/recall.py` | Up to 5 passage receipts per cached answer |

---

## The evidence model

Most memory systems store facts. This one stores facts **with the receipts that
prove them.**

```json
{
  "statement": "Session 'Drift detection' on nidra. 15 turns, 2 files.",
  "evidence": [
    {
      "source": "/Users/you/.claude/projects/slug/abc123.jsonl",
      "excerpt": "explain how drift detection works in the recall cache layer",
      "sha256": "a1b2c3d4e5f6...",
      "checked_at": "2026-08-21T04:10:00Z"
    }
  ],
  "epistemic": {
    "evidence_status": "machine_checked",
    "confidence": 0.9
  }
}
```

When `recall()` is called, every evidence row is re-verified:

1. **Integrity** — `sha256(excerpt)` still matches the stored hash?
2. **Reality** — the source file still contains the excerpt?

Both must pass. One drifted row demotes the entire memory to `unverified`.
The cost is a file read per source (LRU-cached by mtime + size — repeated
checks within the same process are free).

---

## Files

```
~/.claude/skills/meditate/
├── README.md              this file
├── VERSION                0.2.0
├── CHANGELOG.md           history
├── SKILL.md               /meditate slash command definition
├── INTERNALS.md           developer docs (vritti/antaraya/nirodha formulas)
├── install.sh             one-command setup
├── doctor.py              self-diagnostic
├── sessions.py            transcript miner
├── nidra_bridge.py        mining → grading pipe
├── scan_projects.py       git repo discovery
├── still.py               yogic diagnosis engine
├── launch.py              Terminal auto-launcher
├── test_sessions.py       }
├── test_launch.py         }
├── test_scan.py           } 25 test functions
├── test_still.py          }
├── test_doctor.py         }
├── test_nidra_bridge.py   }
└── .gitignore

~/projects/nidra/nidra/        (the grading engine)
├── store.py               JSONL store + journal
├── grade.py               SHA-256 drift detection
├── recall.py              answer cache with receipts
├── sleep.py               5-stage consolidation
├── judge.py               LLM contradiction resolver
├── retrieval.py           search (exact + fuzzy)
├── claude_cli.py          Claude Code bridge
├── cli.py                 command-line interface
├── report.py              markdown reports
├── adapters/
│   ├── mempalace.py       MemPalace → nidra import
│   └── meditate.py        session maps → nidra import
└── eval/
    └── longmemeval.py     LongMemEval benchmark harness

~/projects/nidra/tests/        (29 test functions, 107 assertions)

~/.claude/hooks/
├── meditate-checkpoint.sh     stillness nudge (SessionStart + PreToolUse)
└── rules-inject.sh            operating rules + nidra state (SessionStart + PreToolUse)

~/.claude/meditation/
├── STILLNESS.md               last reading
├── nidra_store/               graded memory store
│   ├── memories.jsonl         82 memories, 71 active
│   └── journal.jsonl          every action ever taken
└── sessions/                  continuation chats from splits
```

---

## Numbers (measured, not claimed)

| Metric | Value |
|---|---|
| Sessions scanned | 90 |
| Memories imported | 82 |
| Machine-checked | 68 (83%) |
| Unverified | 3 (4%) |
| Tombstoned by sleep | 11 |
| Total code | 3,918 lines |
| Total tests | 54 functions, 160 assertions |
| Test files | 12 |
| All tests | green |
| Largest transcript | 37 MB |
| Total transcript size | 734 MB |
| Hook latency | sub-second (nidra state read) |
| Sleep pass (full) | < 2 seconds |
| Session scan (all 90) | < 5 seconds |

---

## Requirements

- Python 3.9+
- Claude Code installed
- macOS (for Terminal auto-launch; everything else is cross-platform)
- nidra library at `~/projects/nidra/` (the grading engine)

---

## Philosophy

The model is yogic, drawn from the Yoga Sutras (YS 1.2): *yogas citta-vrtti-nirodhah*
— the stilling of the whirls of the mind-field. The mind is your sessions. Each
tangled thread is a vrtti (whirl). The practice is not deleting work but separating
it into clean, single-pointed threads, and setting down what is finished.

The evidence model is empirical: a memory you cannot verify is a memory you cannot
trust. Drift detection is not a feature — it is the invalidation policy. TTL
guesses when an answer goes stale. SHA-256 **checks**.

Every Sanskrit term appears with its English meaning beside it. The vocabulary is
real, grounded in our own corpus — never decorative.
