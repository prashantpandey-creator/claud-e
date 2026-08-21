# Changelog

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
