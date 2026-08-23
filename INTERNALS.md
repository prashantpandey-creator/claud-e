# meditate — skill internals

`/meditate` is a stilling pass over the workspace-mind, modeled on the yogic
science of calming the citta (mind-field) drawn from our own corpus. The skill
instructions live in `SKILL.md`; this README documents the two deterministic
tools it leans on (Rule 0: JSON-contract scripts, not raw `git`/`find` output
dumped into context).

- `scan_projects.py` — discovers every project + the raw facts (git state,
  languages, docs). Tested by `test_scan.py`.
- `still.py` — the **yogic diagnosis** over those facts: per-project vṛtti class,
  the antarāyas, and the nirodha / stillness index. Tested by `test_still.py`.
  **NOT WIRED (audited 2026-08-23).** Nothing imports it, no CLI verb runs it,
  it is not in the launchd heartbeat — only its own test executes it. It is
  also the only thing here that knows the workspace holds **15,027 uncommitted
  files across 88 repos** and **102 repos off main**, so it is worth reviving
  rather than deleting; it costs 14.5s per run and would need caching. Until
  then this file was claiming a live engine, which is the same defect the tool
  exists to catch.

The skill consumes only each tool's `data` field and runs both test suites first
(Phase 0 self-heal); a reading built on a broken scanner is worse than none.

## scan_projects.py

```bash
python3 scan_projects.py                 # human summary, default roots
python3 scan_projects.py --json          # envelope (what the skill calls)
python3 scan_projects.py --json --root "/path/one" --root /path/two --max-depth 5
```

`kind` is `"repo"` (a git repo — scan stops descending into it) or `"workspace"`
(a marker-only folder; descent continues to find nested repos). Heavily prunes
`node_modules`, `venv`, `.next`, `ComfyUI`, etc.

## still.py

```bash
python3 still.py                         # human reading (bilingual)
python3 still.py --json                  # diagnosis envelope
python3 still.py --json --today 2026-06-28   # pin "today" (staleness is reproducible)
python3 still.py --json --root "/path/to/one/project"
```

### What it computes

**Vṛtti class** (YS 1.6 — the five kinds of mental modification), per project:

| class | English | rule (deterministic) |
|---|---|---|
| `pramana` | valid / grounded — live | recent (≤14d) or has uncommitted work, with real code |
| `vikalpa` | concept / not-yet-real — an idea | recent but <5 real code files |
| `nidra` | sleep — dormant | no commit in >30 days (or no commits at all) |
| `smriti` | memory — settled record | clean, 14–30 days old, has code |
| `container` | not a vṛtti | a non-git workspace folder |

(`viparyaya` — error / wrong-track — is intentionally left to the skill's
judgment, e.g. work that fails its green-check in B4.)

**Antarāyas** (YS 1.30 — the scatterers), workspace-wide:

- `alasya` (sloth) — repos with uncommitted files + the total.
- `anavasthitatva` (instability) — repos off `main`/`master`.
- `samshaya` (doubt) — exact same-name repos + fork-family stem clusters.
- `nidra` (dormancy) — the long-dormant repos.

**Nirodha / stillness index** — a transparent score, `stillness = 100 - scatter`,
clamped to `0..100` (higher = calmer). Scatter is the sum of:

```
2 × (#repos with uncommitted work)        # open whirls
+ (Σ min(dirty_files, 50)) / 20           # mass of held work (capped per repo)
+ 3 × (#repos off main)                    # instability
+ 4 × Σ (fork_family_size - 1)             # doubt / duplication
+ 1 × (#dormant repos)                     # dormancy
```

Weights are arbitrary but **monotonic and reproducible** — more open threads,
branches, forks, or dormancy always lowers stillness. `test_still.py` asserts a
clean/on-main/recent workspace scores ≥90 and beats a scattered one. The exact
weights are tunable here; tests pin only the ordering, not a magic number.

### Failure modes

| Symptom | `errors[].code` | Meaning / fix |
|---|---|---|
| Upstream scan failed | `scan_failed` | `still.compute` got `success:false` from the scan; `data` is empty — do not trust it. Read the scan's own errors. |
| A root doesn't exist | `root_missing` (from scan) | Typo/moved dir; correct `--root`. Other roots still resolve. |
| A repo's `last_commit` is null | (none) | No commits / detached HEAD / git timed out → classified `nidra`. Not an error. |
| Staleness looks off in a test | — | Pass `--today YYYY-MM-DD` so `_age_days` is deterministic. |

Both tools return the envelope with `success:false` + populated `errors` on
genuine failure; they never raise across the tool boundary. Always check
`success` before trusting `data`.

## Tests

```bash
python3 test_scan.py     # exit 0 = green
python3 test_still.py    # exit 0 = green
```

`test_scan.py` builds a throwaway workspace (real git repo + workspace folder +
node_modules trap). `test_still.py` tests the pure `compute()` on hand-built
project dicts with a fixed `today` — real-fixture-in, envelope-out, no scan
re-run. `/meditate` runs both first and refuses to proceed if either is red.
