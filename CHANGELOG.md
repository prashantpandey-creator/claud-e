# Changelog

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
