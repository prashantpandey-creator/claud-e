# Meditate

**Evidence-graded AI agent memory with drift detection.**

Meditate grades your knowledge — not your session transcripts, your actual
curated memory files — with SHA-256 evidence receipts. Every file path,
every `[[wikilink]]`, every claim is verified against the live filesystem.
When the world changes, the stale memory silently stops being trusted.

```
316 active memories (244 knowledge files + 72 session maps)
313 machine-checked (evidence verified against live sources)
  3 unverified
 11 tombstoned (stale, pruned by sleep pass)
  0 stale answers served

546 file-path claims verified
829 wikilinks verified
```

---

## Install

```bash
bash ~/.claude/skills/meditate/install.sh
```

One command. Checks prerequisites, wires the hooks, grades your memories,
runs the tests, reports. No sudo, no network, no account. Safe to re-run.

After install, `meditate` is on your PATH:

```bash
meditate              # health check
meditate grade        # scan + grade + consolidate
meditate metrics      # drift rate, coverage, health dashboard
meditate sessions     # show sessions ranked by sprawl
meditate launch       # see live threads (--open to open terminals)
meditate help         # all commands
```

---

## How it works

### Two sources, one graded store

Meditate grades two kinds of knowledge into a single nidra store:

**1. Your `.md` memory files** (the real knowledge — 244 files, 1.2 MB).
Hand-curated, linked with `[[wikilinks]]`, structured with `Why:` and
`How to apply:`. These are the refined metal. The adapter
(`nidra/adapters/memory_files.py`) reads each file, extracts verifiable
claims, and builds evidence receipts:

- **File paths** — `/Users/.../file.py` or `~/path` → does it exist on disk?
- **Wikilinks** — `[[target-name]]` → does `target-name.md` exist in the memory dir?
- **Content anchors** — the richest line from the file body → SHA-256'd, checked
  against the file itself (if the memory is edited to remove that claim, the
  evidence drifts — correct)

**2. Session transcripts** (the raw ore — 91 sessions, 738 MB). The adapter
(`nidra/adapters/meditate.py`) mines each transcript into a compact map and
anchors it to the first user message.

Both sources feed the same store (`~/.claude/meditation/nidra_store/`), graded
by the same engine, consolidated by the same sleep pass.

### The grading engine (nidra)

Every memory carries evidence receipts. At serve time — not on a schedule —
each receipt is re-checked:

```
1. Integrity:  sha256(excerpt) == stored hash?     (is the receipt itself intact?)
2. Reality:    source file still contains excerpt?  (does the world still agree?)
```

Both must pass. One drifted row demotes the entire memory to `unverified`.

Three tiers:

| Grade | Meaning | Serves? |
|---|---|---|
| `machine_checked` | evidence re-verified against live source bytes | Yes |
| `source_linked` | evidence recorded, source not checkable right now | Maybe |
| `unverified` | no evidence, or evidence drifted | No |

### The sleep pass (consolidation)

Five deterministic stages, run by `meditate grade`:

1. **Dedup** — same normalized statement → merge into the oldest, union evidence
2. **Verify** — re-check every evidence row, re-grade. Drifted = demoted.
3. **Contradict** — same subject, negated or numerically conflicting → both flagged
4. **Schedule** — spaced-repetition: each clean re-check pushes the next review out
5. **Prune** — evidence-free, low-confidence, long-overdue → tombstoned (never deleted)

Running the pass twice in an unchanged world produces zero actions.

### Injection — fresh, not buried

Two hooks fire into every Claude Code session automatically:

- **`meditate-checkpoint.sh`** — fires on SessionStart and git operations.
  Checks when you last ran `/meditate`. When overdue (3 days), nudges.
- **`rules-inject.sh`** — fires on SessionStart and PreToolUse.
  Injects operating rules + nidra graded memory census via `additionalContext`.

Every new session sees:
```
Nidra store: 316 graded memories; 313 machine_checked; 3 unverified.
```

### Metrics — how well is it running

```bash
meditate metrics
```

```
  Memory Health
    active:      316  (of 327 total, 11 tombstoned)
      machine_checked: 313
      unverified: 3
    verified:   ███████████████████░ 99.1%
    confidence: █████████████████░░░ 89.6%

  Drift Detection
    upgrades:      313  (unverified -> machine_checked)
    downgrades:      0  (machine_checked -> unverified)
    drift rate:  0.00%

  Coverage
    Sessions:      72 / 91  ███████████████░░░░░ 79.1%
    .md files:    244 / 244  ████████████████████ 100.0%
    total active: 316
```

The numbers that matter: verified rate should stay above 90%. Drift rate should
stay near zero. When something drifts — a file renamed, a function deleted, a
wikilink broken — the downgrade count moves, the verified rate drops. That is the
system working.

### Still — split tangles into threads

For tangled, sprawling sessions, `/meditate` splits them into clean single-purpose
continuation chats — each with context, next step, and files in play.

```
~/.claude/meditation/sessions/<id>/
├── INDEX.md                    # all threads found
├── thread-1-drift-detection.md # one thread, resumable
├── thread-2-nidra-bridge.md    # another thread
└── thread-3-packaging.md       # another
```

---

## Architecture

```
Memory files (.md)          Session transcripts (738 MB)
   244 files                     91 sessions
      │                              │
      ▼                              ▼
 memory_files.py              sessions.py → meditate.py
 (paths, wikilinks,          (mine once, emit compact maps,
  content anchors)            anchor to first user message)
      │                              │
      └──────────┬───────────────────┘
                 ▼
          nidra_bridge.py     ← one pass imports both sources
                 │
                 ├── store.py      memories.jsonl + journal.jsonl
                 ├── grade.py      SHA-256 drift detection
                 ├── sleep.py      5-stage consolidation
                 └── recall.py     answer cache with receipts
                 │
                 ▼
          rules-inject.sh     ← inject graded state into sessions
                 │
                 ├── SessionStart   operating rules + nidra census
                 └── PreToolUse     git/deploy discipline
                 │
                 ▼
          metrics.py          ← health, drift, coverage, timeline
          doctor.py           ← prereqs, tests, hooks, nidra state
```

### Adapters — what plugs in

| Source | Adapter | Evidence |
|---|---|---|
| `.md` memory files | `nidra/adapters/memory_files.py` | File paths, `[[wikilinks]]`, content anchors |
| Claude Code sessions | `nidra/adapters/meditate.py` | First user message, SHA-256'd, transcript path |
| MemPalace drawers | `nidra/adapters/mempalace.py` | Escape-proof anchor from drawer text, source file path |
| Direct answers | `nidra/recall.py` | Up to 5 passage receipts per cached answer |

---

## Use

Inside Claude Code:

```
/meditate              full pass: grade, read sessions, split, archive
/meditate memory       grade and refresh the memory layer only
/meditate sessions     read and split sessions only
/meditate archive      archive finished sessions only
```

From a terminal:

```bash
meditate              # health check (doctor)
meditate grade        # scan sessions + .md files, grade, consolidate
meditate metrics      # drift rate, coverage, health dashboard
meditate sessions     # show sessions ranked by sprawl
meditate launch       # see live threads
meditate launch --open  # open Terminal windows per thread
meditate doctor --json  # full diagnostic as JSON envelope
```

---

## Numbers (measured, not claimed)

| Metric | Value |
|---|---|
| Memory files graded | 244 (100%) |
| Sessions scanned | 91 |
| Total active memories | 316 |
| Machine-checked | 313 (99.1%) |
| Unverified | 3 (0.9%) |
| Tombstoned by sleep | 11 |
| File-path claims verified | 546 |
| Wikilinks verified | 829 |
| Journal entries | 1,704 |
| Sleep pass actions | 324 |
| Duplicates merged | 11 |
| Test functions | 61+ |
| All tests | green |
| Largest transcript | 37 MB |
| Total transcript size | 738 MB |
| Hook latency | sub-second |
| Sleep pass (full) | < 2 seconds |
| Session scan (all 91) | < 5 seconds |

---

## Files

```
~/.claude/skills/meditate/
├── README.md              this file
├── VERSION                0.3.0
├── CHANGELOG.md           history
├── SKILL.md               /meditate slash command definition
├── INTERNALS.md           developer docs (vritti/antaraya/nirodha formulas)
├── meditate               CLI wrapper (symlinked to ~/.local/bin/)
├── install.sh             one-command setup
├── doctor.py              self-diagnostic
├── metrics.py             health, drift, coverage dashboard
├── sessions.py            transcript miner
├── nidra_bridge.py        mining + .md grading pipe
├── scan_projects.py       git repo discovery
├── still.py               yogic diagnosis engine
├── launch.py              Terminal auto-launcher
├── test_sessions.py       }
├── test_launch.py         }
├── test_scan.py           } test suite
├── test_still.py          }
├── test_doctor.py         }
├── test_nidra_bridge.py   }
├── test_metrics.py        }
└── .gitignore

~/projects/nidra/nidra/        (the grading engine)
├── store.py               JSONL store + journal
├── grade.py               SHA-256 drift detection
├── recall.py              answer cache with receipts
├── sleep.py               5-stage consolidation
├── judge.py               LLM contradiction resolver
├── retrieval.py           search (exact + fuzzy)
├── adapters/
│   ├── memory_files.py    .md knowledge files → nidra import
│   ├── meditate.py        session maps → nidra import
│   └── mempalace.py       MemPalace → nidra import
└── eval/
    └── longmemeval.py     LongMemEval benchmark harness

~/.claude/hooks/
├── meditate-checkpoint.sh     stillness nudge
└── rules-inject.sh            operating rules + nidra state

~/.claude/meditation/
├── STILLNESS.md               last reading
├── nidra_store/               graded memory store
│   ├── memories.jsonl         327 memories, 316 active
│   └── journal.jsonl          1,704 events
└── sessions/                  continuation chats from splits
```

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
guesses when an answer goes stale. SHA-256 checks.

Every Sanskrit term appears with its English meaning beside it. The vocabulary is
real, grounded in our own corpus — never decorative.
