# Changelog

## 0.13.0 — 2026-08-22

Remote view (Rung 2) — see your brain from your phone, safely.

The owner has hosting (SSDNodes) and chose the remote-VIEW rung after
reviewing the fork: local island stays the foundation, full remote CONTROL
is refused, and this is the safe middle — data flows ONE WAY only.

- `remote.py`: the machine PUSHES a bounded summary (project shares, goal %,
  counts, open-task titles) to a receiver that STORES the latest and SERVES
  it read-only behind a view secret. push() reads only the HTTP status and
  DISCARDS the body — the server has no path to command the machine. That is
  the one-way guarantee, in code and pinned by test.
- Snapshot carries names and numbers ONLY: never a memory statement,
  transcript line, or evidence excerpt. Proven — a secret planted in a memory
  never appears in the push. Opt-in: nothing is sent without a configured
  endpoint.
- 4 tests (secret-never-leaves, response-never-actioned, receiver
  read-only + secret-gated, no-command-route). Verified live end to end on
  loopback: 449-fact summary pushed, viewed, read-only confirmed.

Deploy to the SSDNode is a separate step (box access + secrets = a HANDOFF);
built and proven locally, not yet live on the server.

Record correction (sangama): `remote.py` and `test_remote.py` were authored
in THIS session but a concurrent session's `git add -A` swept them into its
commit 0a0b128 ("0.12.1 — Casper delivers") mid-flight — the exact
cross-session collision the tool's own presence layer warns about. The code
is correct and committed; the authorship label is wrong. Not force-pushed to
relabel: that would stomp the other session's work, which ship discipline
forbids. This note is the honest record.


## 0.12.0 — 2026-08-22

Casper — the voice/form: composes the ONE thing worth saying and judges
whether now is the moment (flow vs pause, measured from edit activity, never
mood-claimed). briefing() + interruptibility(), 8 tests.

Record correction: voice.py, test_voice.py and the `meditate voice` verb
actually LANDED in commit e65eafd, which a concurrent session mislabeled
"docs: private island" — it ran `git add -A` and swept my uncommitted voice
files into its own push while my commit was mid-flight. The code is correct
and shipped; the commit message was not. Left unamended on purpose: force-
pushing to relabel it would stomp the other session's work, which the tool's
own sangama rule forbids. This note is the honest record instead.

### Added (0.12.1)
- Delivery: `meditate voice --notify` posts a native macOS notification, and
  `--speak` says it aloud — BOTH gated by interruptibility, so Casper reaches
  you only at a pause, never mid-flow. Wired into the heartbeat so it can
  reach you on its own at the right moment.


## 0.11.0 — 2026-08-22

Sārathi (the charioteer) — the brain becomes a conductor with a soul.

### Added
- **Control surface**: POST /api/act runs the same code the CLI runs —
  launch fleet / repair all / fix item N / dispatch one goal / grade — and
  RETURNS THE REAL OUTPUT onto the page (a click that hides what it did is
  the opposite of intuitive). Every click lands in events.jsonl and the
  page's ACTIVITY trail. CSRF-guarded: actions require the X-Meditate
  header (browser preflight blocks cross-origin); 403 without it, tested.
  Push/deploy stay in the terminal with the owner — no button for them.
- **Prāṇa orbs**: every live session is a breathing gold orb whose beat IS
  its recency — ~1s pulse when it edited seconds ago, slowing with age, a
  still dim ember past 30 minutes. Read live: 8 orbs, the Razorpay session
  beating fastest (messaging.js, 1m).
- Verbs: `meditate sarathi|brain|serve|watch|soul`. Sanskrit glossed on the
  page itself: the sessions are the horses, this is the reins.
- Hosted multi-user brain (behind real auth) added to meditate-market-ready
  as an explicit milestone — until then loopback-only stays test-pinned.


## 0.10.0 — 2026-08-22

`meditate brain` — the whole organism, live, in the browser.

### Added
- brain.py: stdlib-only HTTP server, one auto-refreshing page (4 s): live
  sessions (sid, last file, age — watched a session's file change between
  refreshes), goal fleet with bars and scope drift, repair queue items,
  memory census, drift wins, sangama counts, the done-digest, and the one
  `next:` decision. GET /api/state serves the same as JSON.
- Binds 127.0.0.1 ONLY and the test pins it — this page IS the owner's
  memory and sessions; a brain never faces a network by default. Public
  hosting stays refused until auth exists (market-ready goal territory).
- Verbs: `meditate brain|serve|watch` (+ 3 tests; server thread-tested on an
  ephemeral port, 404 behavior, self-contained page).


## 0.9.2 — 2026-08-22

The lifecycle closes: install -> silent passes -> the call -> the face ->
action. Plus the other half of communication.

### Added
- **Install finale = the first meditation call**: after the initial pass and
  tests, install generates the dashboard and opens it — the user's first
  sight of the product is their own state with action chips (`meditate`,
  `meditate go`, `meditate fix`, `/meditate`), not a README.
- **Done-digest at SessionStart**: the hooks now tell the user what the
  silent machinery DID, not only what is owed — one line, last 24h, from
  durable logs only: "Done silently (24h): graded 6x, formed 74
  commit-facts, archived 7 sessions, served 2 fact/warn events." Empty day =
  no line. Observed live through the installed hook.
- status points at the face when something is owed; dashboard carries ACT
  chips with the exact commands.

### Fixed
- done_digest read the archive index from a hardcoded live path — isolated
  tests saw the real machine's history (third occurrence of this disease;
  the archive root now follows the store like the repair queue does).
- dashboard chips: two-pass %-format collided with CSS percent signs;
  placeholder replace instead.


## 0.9.1 — 2026-08-22

Install = consent. The owner: maximum things should run silently — whoever
installs a memory manager wants memory managed.

### Changed
- **The heartbeat now does everything safe-and-reversible, silently**: grade
  + sleep + index + queues (as before) + archive empty sessions (--apply is
  reversible by design) + regenerate the dashboard HTML. Observed live: one
  beat ran all three stages. An integration test fails if a stage ever falls
  off the plist.
- **Intuitive verbs** — the typed surface answers intent, not module names:
  `meditate what|search|find|recall ...` = ask; `meditate fix|repair` =
  launch the repair agent (new --repair-only path); `go|run|work|act` = go;
  `status|state|where` = status. An unknown word prints the four-line
  product, not an error.

The remaining human verbs are exactly the intention gates: `go`/`fix`
(spending agents), distill (judgment), push (production). Everything else
is the tool doing its job unasked.


## 0.9.0 — 2026-08-22

Two verbs. The owner was right to be unsatisfied: 15 subcommands is a menu,
and menus are the disease this whole system exists to cure.

### Changed
- **`meditate`** (bare) is now STATUS: one screen — store health, goals,
  queues, fleet, heartbeat — ending in exactly ONE decided `next:` line.
  Priority: repair queue > dispatchable goals > overdue stilling > rest.
  (It used to run doctor — a diagnostic is not a place to live.)
- **`meditate go [N]`** — move everything forward: a repair agent if the
  queue is open, plus one agent per dispatchable goal. The fleet size is not
  a setting — the world sets it (the owner asked "why is 3 fixed?" — it
  isn't, anymore; a number only caps, never pads. 0 = dry-run).
- Everything else is plumbing, still present, grouped under `meditate help`.

### Added
- status.py + go.py + 7 tests (single next line, repair-beats-goals
  priority, world-sized fleet, cap honored, dry-run launches nothing).


## 0.8.0 — 2026-08-22

Drive + dashboard — the fleet layer and the face.

### Added
- **`meditate drive`** — one command dispatches goal agents: dry-run lists
  which goals would get one; `--go N` opens up to N Terminal agents, each on
  its goal's first open milestone with the tick-the-checkbox kickoff. A
  dispatched goal cools down 4h (no double-send while an agent is presumed
  working); every send lands in dispatch.jsonl. Deliberately NOT a cron: the
  owner triggers, the fleet executes, ship discipline rides in via the hook.
  7 tests (dry-run writes nothing, cap, cooldown, expiry, failed-launch not
  recorded).
- **`meditate dashboard`** — the whole organism on one self-contained HTML
  page (4.2 KB, zero external assets, dark + one gold): goals with scope
  drift, drift-correct counters, stilling, sangama, formation queue,
  heartbeat age. `--open` for the browser.


## 0.7.0 — 2026-08-21

Knowledge formation — the owner is right: forming is a natural part. nidra
graded but never formed; the adapters only imported what already existed.
The rule that makes formation safe was already built: form freely, grade
ruthlessly, serve only what verifies.

### Added
- **Lane 1 — commit-facts (deterministic, every heartbeat).** Commits made in
  sessions ARE distilled knowledge (the owner wrote the message). Each becomes
  a memory with evidence born attached (source = transcript, excerpt = the
  literal `[branch hash] subject` stdout line). First live run: 74 formed
  from 105 sessions, 74/74 graded machine_checked the same pass; store
  361 -> 435 active. `meditate ask` surfaces them beside curated laws.
- **Lane 2 — the distillation queue (judgment, orchestrated).**
  `meditate distill` lists substantive sessions (>=15 user msgs) not yet
  distilled; `meditate distill <sid>` emits an agent kickoff that writes real
  memory .md files (Why / How to apply / originSessionId) into the synced
  store, graded by the next heartbeat; `--done` marks the ledger.

### Refused by evidence
- The `git log --oneline` pattern was tried and REMOVED: live transcripts are
  full of hex-prefixed listings and it formed garbage ("83525ed2 can you
  check if we can find blue lotus..."). Only git commit's own stdout shape is
  unambiguous. Two tests pin the refusal.
- Live run also caught `file` being a basename (tests had been too kind —
  absolute paths only); resolution via _project_dir now tested.


## 0.6.0 — 2026-08-21

The missing metabolism. Whole-system analysis found the organs present but no
pulse: nothing ran unbidden, the store could not be questioned, caught drift
evaporated at a terminal. All three closed; knowledge FORMATION stays with
/meditate (judgment), now nagged by exact work items instead of memory.

### Added
- **Heartbeat** — launchd `com.meditate.grade` runs the full grade + sleep +
  index + queue pass every 6h, no human needed. Its FIRST live beat caught a
  real latent bug: system python 3.9 rejects 'Z' timestamps and one crashed
  the whole sleep pass (interactive 3.14 never saw it). Fixed in nidra
  `_parse_ts` — a bad timestamp degrades to None, never kills consolidation.
- **`meditate ask`** — the store can finally be questioned. nidra retrieval
  over active memories, verified facts ranked first, every hit carries its
  grade; an unverified hit is marked, never laundered into clean fact.
- **Repair queue** — every grade pass materializes caught drift as
  `~/.claude/meditation/repair-queue.md` (exact failing claim + line) and
  REMOVES it when the world re-verifies. SessionStart nudges while it exists.
  First live queue caught real work: the liveStream law's worktree path
  (`.scratch-worktrees/chat-switch-perf`) is GONE while the branch is still
  awaiting its push go.
- doctor checks the heartbeat (plist + loaded + last beat); 4+2 new tests.

### Isolation
- The queue lives beside ITS OWN store (parent dir), so temp-store test runs
  can never clear the live queue.


## 0.5.0 — 2026-08-21

Goals — long-term direction across every project, measured not vibed, with
scope-widening as a first-class fact.

### Added
- `goals.py` + 8 tests. A goal = one .md in ~/claude-sync/goals/ (synced,
  human-editable): frontmatter + '## Milestones' checkboxes. Percentage =
  checked/total — deterministic; nothing self-reports progress.
- **Evolving goals**: every scan snapshots (done, total) to
  goals-history.jsonl; when a goal WIDENS the table shows "scope +N" beside
  the honestly lower percentage. Growth of ambition is visible, never silent
  dilution.
- **North-star nudge**: SessionStart injects one line for the goal governing
  the session's cwd (deepest cwd match wins; done/paused goals never nudge).
- **Agent orchestration**: `meditate goals launch <name>` builds a kickoff
  prompt from the goal's open milestones — "take the FIRST open milestone,
  drive it to done, tick the checkbox, stop" — and prints the `claude`
  command or opens a Terminal on it (--open, via launch.py).
- Seeded 3 real goals from the owner's open items: purangpt-mobile-live
  (3/8), astrology-readings-instant (3/6), meditate-self-proving (4/7).


## 0.4.4 — 2026-08-21

Longevity audit: will the pipeline hold for years? Measured, not guessed.

### Measured envelope (holds without changes)
- Full journal read at 50 MB: 35 ms; memories at 50 MB: 56 ms; `du` on a
  10 GB projects dir: ~450 ms — all inside hook timeouts. Presence self-prunes
  (24h). Store rewrite is O(n) and fine at 10k memories. path_index atomic.
- November cliff defused by design: the 313 memories sharing review_due
  2026-11-19 (old test-bug residue) all carry evidence, and sleep's prune
  stage skips anything with evidence; every grade pass re-advances the
  schedule. Only the evidence-free stubs can ever tombstone — correct.

### Fixed (the one real unbounded surface)
- journal.jsonl grew append-only forever, no rotation anywhere (3,314 rows on
  the peak day). The bridge now rotates it at 25 MB to journal-<stamp>.jsonl;
  `report.py` reads ALL journals oldest-first so repair pairs survive
  rotation; the SessionStart drift scan reads only the current file (48h
  window — rotation cannot cut inside it at any plausible rate).
- 2 new tests: rotation trigger + no-op under threshold; repair pair spanning
  a rotated journal and the current one.


## 0.4.3 — 2026-08-21

`meditate report` — the measurement loop for the drift-correct cycle and the
stilling practice. Wins are now counted from durable logs, not remembered.

### Added
- `report.py` + 6 tests: drift caught (downgrade events + `drifted` flags),
  repaired (downgrade later re-verified, with median time-to-repair), open
  (real drift vs ungradeable stubs); stilling (sessions archived + bytes,
  splits -> continuation chats, stillness age); sangama (facts served,
  collisions warned). Honest zeros — an empty log reports 0.
- `coordination.py` now logs every fact-serve and collision-warn to
  `~/.claude/coordination/events.jsonl` (fail-open, one line each). Before
  this, sangama's efficacy was unmeasurable: presence files self-prune in 24h.

### First live run
- caught 1 (the resolved-Mumbai memory: its .md was edited, the content
  anchor stopped matching — flagged `drifted` at first verify), repaired 0,
  open 1 real + 4 ungradeable session stubs; 7 sessions archived (99.2 KB);
  9 splits -> 17 continuation chats; facts_served moved 0 -> 1 from a single
  real hook fire.


## 0.4.1 — 2026-08-21

`meditate archive` — the consolidation step that could never actually remove
a session, now can, from the terminal, reversibly.

### Added
- `archive.py` + 8 tests. Dry-run by default; `--apply` MOVES <sid>.jsonl and
  its sidecar dir to `~/.claude/meditation/archive/<slug>/` and records a
  restore line in ARCHIVE-INDEX.jsonl. `--restore <sid>` brings both back
  exactly where they were. Nothing is ever deleted. Sessions touched in the
  last 24h are never candidates. `--older-than N` extends beyond empty ones.
- File-level = surface-independent: the same store backs the `claude` CLI
  resume picker and the Desktop app, so archiving here removes the session
  from both (Desktop list effect traced via shared store, not UI-observed).

### Proven live
- 111 sessions -> 105 (6 empty archived), restore round-trip observed
  (105 -> 106, file back in the project dir), re-archive -> 105.


## 0.4.0 — 2026-08-21

Sangama (confluence) — multi-session coordination + fact serving. Three new
capabilities, zero new hook registrations, everything through the existing
`additionalContext` channel at the moment it matters, never as prompt bulk.

### Added
- **Presence** — every Write/Edit records (session, cwd, file, time) in
  `~/.claude/coordination/sessions/`. Heartbeat = file mtime; stale entries
  self-prune after 24h. `meditate who` lists live sessions and their files.
  Payload fields (`session_id`, `cwd`) verified against a captured live hook
  payload, not assumed.
- **Collision warnings** — a session editing a file another live session
  touched inside 2h gets ONE calm warning naming the session and the age,
  then never again for that (peer, file). Proven live: warn once, repeat
  edit returns `{}`.
- **Fact serving** — `nidra_bridge` now emits `path_index.json`
  (104 paths, 154 machine-checked claims). Editing an indexed file serves up
  to 2 graded facts, once per file per session, unverified claims never.
  Wrong beliefs get corrected at the exact moment the agent acts on them.
- **Drift alert at SessionStart** — journal downgrades inside 48h surface as
  one line naming the drifted memories.
- **`meditate drift`** — every memory whose evidence currently fails, with
  the exact failing claim (which path is gone, which wikilink broke) and the
  line to fix in the .md. Detection is deterministic; the fix stays judgment.
- `coordination.py` (the whole layer, stdlib-only) + `test_coordination.py`
  (18 tests) + 2 hook-integration tests. 120 test functions total.

### Changed
- File-edit events now route to one decision point (`coordination.py
  hook-edit`); the pipeline/native guard rules moved there from bash. The
  bash fast path for irrelevant commands is untouched: 0.011s. Edit events
  cost 0.048s.
- Hook tests fully isolate presence/store via `MEDITATE_COORD_DIR` /
  `MEDITATE_STORE_DIR`.

## 0.3.1 — 2026-08-21

Audit repairs. An adversarial audit of 0.3.0 confirmed 29 defects; these are the
ones that made the product lie about itself.

### Fixed
- **Tests wrote to the LIVE graded store.** `test_nidra_bridge.py` called
  `run()` five times against `~/.claude/meditation/nidra_store`, and `doctor.py`
  runs that suite — so every health check mutated shared state, inflated the
  journal, and advanced the spaced-repetition ladder for all 316 memories to the
  90-day rung. `run()` now takes `store_dir`; the tests use a `TemporaryDirectory`,
  and `test_does_not_touch_live_store` fails if that ever regresses.
- **`install.sh` shipped a hook that no longer exists.** It copied and registered
  `meditate-checkpoint.sh` — retired in 0.3.0 — so a fresh install wired a
  missing file (exit 127 on every SessionStart) and never installed the merged
  hook. It also skipped copying whenever a hook already existed, so an upgrade
  never shipped a new one.
- **The hook lived in no repository.** `meditate-hook.sh` existed only in
  `~/.claude/hooks/` (untracked). It is now `hooks/meditate-hook.sh` in this repo
  — the source of truth — and `doctor.py` reports when the installed copy drifts.
- **The hook's prefilter was narrower than its own branches**, silently
  swallowing events it was written to catch: `docker-compose ... --build`, bare
  `--build`, and any uppercase path (`Foo.SWIFT`) all returned `{}`. Trigger
  patterns are now defined once and shared by prefilter and branches.
- **`set -e` plus an unguarded `find`/`du` could drop all seven operating
  rules.** With no `~/.claude/projects`, the hook exited 1 with empty stdout and
  Claude Code silently lost every rule. The hook now always exits 0 with valid
  JSON.
- **Two surfaces disagreed on "sessions"**: the hook said 670, `metrics.py` said
  91. `find` was recursing into per-session `subagents/`. Added `-maxdepth 2`.
- **Coverage counted one memory store of five**, reporting a flattering
  244/244 while 28 files went ungraded. Both `nidra_bridge.py` and `metrics.py`
  now walk every store. 272 files graded.
- **`MAX_CLAIMS` was 5**, silently dropping 129 path/wikilink claims across 41
  of 272 files while the README promised "every claim is verified". Raised to 40
  (real corpus peaks at 8 paths / 26 wikilinks); a test fails loudly if a future
  file would exceed it.
- **README numbers were recalled, not measured.** 546 file-path claims (actual:
  150), 829 wikilinks (860), 1,704 journal entries (4,985), largest transcript
  37 MB (112 MB). Every row re-measured and the commands to reproduce named.

### Added
- `test_hook.py` — 11 tests the hook shipped without. Run against the 0.3.0 hook
  they fail 5, including "rules lost on empty HOME".
- `doctor.py` checks each registration **by matcher**, flags registrations still
  pointing at retired hooks, and verifies the installed hook matches the repo.
- `install.sh` puts `meditate` on PATH and retires the two superseded hooks.

## 0.3.0 — 2026-08-21

Grade the knowledge, not just the transcripts.

### Added
- `nidra/adapters/memory_files.py` — grades hand-curated `.md` memory files.
  Extracts file paths, `[[wikilinks]]`, and a content anchor per file, each with
  a SHA-256 evidence receipt checked against the live filesystem. 23 tests.
- `metrics.py` + `test_metrics.py` — health, drift, consolidation, coverage,
  and activity dashboard. `meditate metrics [--json]`.
- `meditate` CLI wrapper: `grade`, `metrics`, `sessions`, `launch`, `install`,
  `doctor`.

### Changed
- `nidra_bridge.py` imports `.md` memory files alongside session transcripts.
- The two hooks (`meditate-checkpoint.sh`, `rules-inject.sh`) merged into one
  `meditate-hook.sh`; 6 registrations became 3; the looping `Stop` branch removed.

## 0.2.0 — 2026-08-21

The nidra pipe: session mining now feeds an evidence-graded memory store.

### Added
- `nidra_bridge.py` — connects meditate's session scanning to nidra's
  evidence-graded store. Scans all sessions, imports with SHA-256 evidence
  receipts, optionally runs the sleep (consolidation) pass. JSON envelope.
- `test_nidra_bridge.py` — 4 tests: envelope shape, data fields, sleep pass,
  idempotency.
- `nidra/adapters/meditate.py` — nidra adapter that converts session maps into
  graded memories. Each memory carries an evidence receipt (first user message
  text + SHA-256) pointing at the source transcript. 8 tests, all green.
- Phase A0 in SKILL.md — `/meditate` now runs the nidra bridge + sleep pass
  during the stilling cycle.
- `doctor.py` checks nidra store state (connected, total, active, by_status).
- `rules-inject.sh` SessionStart now includes nidra graded memory census
  ("71 graded memories; 68 machine_checked") as a structured injection slot.

### First run
- 90 sessions scanned, 82 imported, 68 machine-checked (evidence verified
  against live transcript files), 3 unverified, 11 tombstoned by sleep pass.

## 0.1.0 — 2026-08-21

First versioned release. The engine has been running since June 28; this release
adds the product packaging around it.

### What was already working (since June–July 2026)
- **sessions.py** — streams 35 MB transcripts, emits compact per-session maps
  with sprawl scores, chapter marks, files touched, and user intents.
- **scan_projects.py** — discovers git repos and workspace folders across
  configurable roots.
- **still.py** — the yogic diagnosis engine: vritti classification, antaraya
  scattering, nirodha/stillness index.
- **launch.py** — reads continuation chats and opens macOS Terminal windows
  with the correct working directory and kickoff prompt.
- **meditate-checkpoint.sh** — hook that nudges when STILLNESS.md is overdue.
  Fires on SessionStart and PreToolUse (git commit/push/deploy).
- **SKILL.md** — the `/meditate` slash command definition (5 phases).
- **14 tests** across 4 test files, all green.

### Added in 0.1.0
- `README.md` — product-facing documentation.
- `VERSION` — semver tracking.
- `CHANGELOG.md` — this file.
- `install.sh` — one-command setup: prerequisites, hook wiring, test run.
- `doctor.py` — self-diagnostic: tests, hook registration, STILLNESS age,
  prerequisites. JSON envelope output.
- `test_doctor.py` — tests for doctor.
- `INTERNALS.md` — developer docs (renamed from the old README).
- Git repository initialized.
