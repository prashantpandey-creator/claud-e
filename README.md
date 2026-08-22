# Meditate

**Your Claude, remembered — and measured.**

Meditate turns everything Claude Code does on your machine into knowledge you
can trust: facts with receipts, projects with honest progress, and agents you
dispatch and watch. It runs entirely on your own machine — no account, no
server of ours, no data leaves your laptop.

```
444 facts known · 94% still true when re-checked · 75 learned by itself
28 failed verification → repair queue
6 goals across 4 projects · fleet dispatch + live progress
```

---

## Install

```bash
git clone https://github.com/prashantpandey-creator/meditate ~/.claude/skills/meditate
bash ~/.claude/skills/meditate/install.sh
```

One command. It fetches its own grading engine, wires the hooks, grades your
memory, installs a background self-check tuned to how fast your work changes,
runs the tests, and opens the dashboard. No sudo, no account, nothing leaves
your machine. Safe to re-run.

*(The engine is a small internal library — installed automatically, you never
touch it. It lives in its own repo only so it can be tested in isolation.)*

## The whole product is two verbs

```bash
meditate           # where am I + the ONE next action
meditate go        # move everything forward: repair agent if knowledge
                   # broke, plus one agent per open goal
```

Everything else is there when you want a specific organ:

```bash
meditate pulse       # LIVE dashboard in your browser (localhost only)
meditate projects    # where your attention actually went, per project + tasks
meditate ask "..."   # question the graded memory — verified facts first
meditate fix         # repair broken knowledge (--list, or fix <n> for one)
meditate fleet       # live progress of dispatched agents
meditate report      # wins: drift caught/repaired, stilling, efficacy
meditate distill     # sessions awaiting distillation into memory
meditate archive     # tidy finished sessions away (reversible)
meditate doctor      # health: prereqs, 21 test suites, hooks, heartbeat
meditate help        # everything
```

Intent aliases work too — `what`/`search`/`find`/`recall` = ask,
`run`/`work` = go, `repair` = fix, `where` = status, `live`/`brain` = pulse.

## What "a memory" means here

One fact about your work, saved with a **receipt** — the exact file and line
it came from. Every 6 hours each receipt is re-checked against reality. A
fact whose file moved, whose claim went stale, or whose path was deleted
stops being trusted and goes to the repair queue instead. Nothing unverified
is ever served.

That is the whole thesis: **form freely, grade ruthlessly, serve only what
verifies.**

---

## How it works

### Two sources, one graded store

Meditate grades two kinds of knowledge into a single nidra store:

**1. Your `.md` memory files** (the real knowledge — 272 files across all 5
memory stores). Hand-curated, linked with `[[wikilinks]]`, structured with
`Why:` and `How to apply:`. These are the refined metal. The adapter
(`nidra/adapters/memory_files.py`) reads each file, extracts verifiable
claims, and builds evidence receipts:

- **File paths** — `/Users/.../file.py` or `~/path` → does it exist on disk?
- **Wikilinks** — `[[target-name]]` → does `target-name.md` exist in the memory dir?
- **Content anchors** — the richest line from the file body → SHA-256'd, checked
  against the file itself (if the memory is edited to remove that claim, the
  evidence drifts — correct)

Up to `MAX_CLAIMS` (40) paths and 40 wikilinks per file. The real corpus peaks
at 8 and 26, so nothing is dropped — and a test fails loudly if a future file
would exceed it, rather than quietly under-grading.

**2. Session transcripts** (the raw ore — 91 sessions, 754 MB, largest 112 MB).
The adapter (`nidra/adapters/meditate.py`) mines each transcript into a compact
map and anchors it to the first user message.

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

One hook (`meditate-hook.sh`) fires into every Claude Code session via
`additionalContext` — three registrations, one script. An irrelevant tool call
is rejected by a pure-bash prefilter and spawns **no** subprocess at all
(0.012 s); only a call that will actually produce a rule pays for parsing:

- **SessionStart** — operating rules + nidra census + stillness check
- **PreToolUse (Bash)** — git/deploy discipline
- **PreToolUse (Write|Edit|MultiEdit)** — hot-file guard for pipeline files

Every new session sees:
```
Nidra store: 358 graded memories; 353 machine_checked; 5 unverified.
Sangama: 2 other live sessions in this repo (recent: LifeReadings.tsx). ...
```

The hook must always exit 0 with valid JSON. A crash or empty stdout makes
Claude Code drop every rule *silently* — worse than no hook, because you lose
the rules and never find out. `test_hook.py` pins that contract against
malformed stdin, a missing `~/.claude/projects`, unicode, and embedded newlines.

### Sangama — many sessions, one repo, no collisions

Multiple Claude Code sessions often work the same checkout. The sangama
(confluence) layer coordinates them deterministically — no LLM, no extra
registrations, ~0.05 s per edit:

- **Presence** — every Write/Edit records (session, file, time). `meditate who`
  shows who is live and what they touched.
- **Collision** — editing a file another live session touched inside 2h gets
  ONE calm warning naming the session and the age — then silence. A warning
  repeated on every edit is pressure; pressure is what this layer removes.
- **Facts at the moment of need** — the grade pass builds `path_index.json`
  (path → machine-checked statements). Editing an indexed file serves up to 2
  graded facts about it, once per session. An agent about to act on a wrong
  belief gets the corrected fact exactly then — not buried in a system prompt.
- **Drift alert** — SessionStart names memories downgraded in the last 48h;
  `meditate drift` prints the exact failing claim and the line to fix.

Correction stays honest: detection is deterministic, the .md fix is judgment
work for the agent (or `/meditate`), and the next grade pass re-verifies it.

### Metrics — how well is it running

```bash
meditate metrics
```

```
  Memory Health
    active:      344  (of 355 total, 11 tombstoned)
      machine_checked: 341
      unverified: 3
    verified:   ███████████████████░ 99.1%
    confidence: █████████████████░░░ 89.7%

  Drift Detection
    upgrades:     654  (unverified -> machine_checked)
    downgrades:     0  (machine_checked -> unverified)
    drift rate:  0.00%

  Coverage
    Sessions:      72 / 91  ███████████████░░░░░ 79.1%
    .md files:    272 / 272  ████████████████████ 100.0%
    total active: 344
```

Coverage counts **every** memory store. It used to count one, which reported a
flattering 244/244 while 28 files in other stores were never graded at all —
a metric hiding the gap it exists to expose.

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
Memory files (.md)          Session transcripts (754 MB)
   272 files, 5 stores            91 sessions
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
          meditate-hook.sh    ← inject graded state into sessions
                 │
                 ├── SessionStart   operating rules + nidra census
                 └── PreToolUse     git/deploy discipline + hot-file guard
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
meditate archive      # archive finished/empty sessions (dry-run; --apply)
meditate drive        # dispatch goal agents (dry-run; --go N launches)
meditate dashboard    # the whole organism, one self-contained HTML page
meditate ask          # question the graded store — verified facts first
meditate distill      # formation queue: sessions awaiting distillation
meditate goals        # long-term goals: %, scope drift, agent kickoffs
meditate report       # wins + efficacy: drift caught/repaired, stilling
meditate drift        # memories whose evidence failed — exact claims
meditate who          # live sessions in this workspace, their files
meditate sessions     # show sessions ranked by sprawl
meditate launch       # see live threads
meditate launch --open  # open Terminal windows per thread
meditate doctor --json  # full diagnostic as JSON envelope
```

---

## Numbers (measured, not claimed)

Every row below was produced by running the command, not by recall. Re-measure
with `meditate metrics --json` and `meditate doctor --json`.

| Metric | Value |
|---|---|
| Memory files graded | 272 / 272 (100%, all 5 stores) |
| Sessions scanned | 72 / 91 (79.1%) |
| Total active memories | 344 (of 355; 11 tombstoned) |
| Machine-checked | 341 (99.1%) |
| Unverified | 3 (0.9%) |
| Evidence receipts | 1,356 |
| — wikilinks | 860 |
| — content anchors | 272 |
| — file paths | 150 |
| — session transcripts | 74 |
| Journal entries | 4,985 |
| Sleep runs | 94 |
| Duplicates merged | 22 |
| Grade downgrades (drift) | 0 |
| Test files / functions | 17 / 120 |
| All tests | green |
| Largest transcript | 112 MB |
| Total transcript size | 754 MB |
| Hook, SessionStart | 0.14 s |
| Hook, irrelevant call (prefiltered) | 0.012 s |
| Grade + sleep pass (full) | 3.8 s |
| Session scan (all 91) | 1.9 s |

---

## Files

```
~/.claude/skills/meditate/
├── README.md              this file
├── VERSION                0.7.0
├── CHANGELOG.md           history
├── SKILL.md               /meditate slash command definition
├── INTERNALS.md           developer docs (vritti/antaraya/nirodha formulas)
├── meditate               CLI wrapper (symlinked to ~/.local/bin/ by install.sh)
├── install.sh             one-command setup
├── hooks/
│   └── meditate-hook.sh   the hook — repo is the source of truth, install copies it
├── doctor.py              self-diagnostic
├── metrics.py             health, drift, coverage dashboard
├── coordination.py        sangama: presence, collisions, fact serving, drift CLI
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
├── test_hook.py           }
├── test_coordination.py   }
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
└── meditate-hook.sh           operating rules + nidra + discipline (single merged hook)

~/.claude/meditation/
├── STILLNESS.md               last reading
├── nidra_store/               graded memory store
│   ├── memories.jsonl         355 memories, 344 active
│   └── journal.jsonl          4,985 events
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
