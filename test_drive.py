"""Tests for drive.py — the goal-fleet dispatcher (Rule 0, precondition A).

Contract:
  - dispatchable = active/evolving goal, open milestones, NOT dispatched
    inside the cooldown window (an agent is presumed working it)
  - dry-run by default: lists, launches nothing, writes nothing
  - --go N launches at most N (launcher injectable), records each dispatch
    to the ledger with goal + milestone + ts
  - a second drive inside the cooldown dispatches nothing (no double-send)
  - envelope always

Run: python3 ~/.claude/skills/meditate/test_drive.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)
import drive as dv

GOAL = """---
name: g-one
title: Ship widget one
project: w1
cwd: /repo/w1
status: active
---
## Milestones
- [x] a done thing
- [ ] the next thing
"""


def _world(t, n_goals=1):
    gdir = os.path.join(t, "goals"); os.makedirs(gdir)
    for i in range(n_goals):
        with open(os.path.join(gdir, "g%d.md" % i), "w") as f:
            f.write(GOAL.replace("g-one", "g-%d" % i)
                        .replace("widget one", "widget %d" % i)
                        .replace("/repo/w1", "/repo/w%d" % i))
    return gdir, os.path.join(t, "dispatch.jsonl"), os.path.join(t, "hist.jsonl")


def test_dispatchable_lists_open_goals():
    with tempfile.TemporaryDirectory() as t:
        gdir, ledger, hist = _world(t, n_goals=2)
        ds = dv.dispatchable(goals_dir=gdir, ledger_path=ledger, history_path=hist)
        assert len(ds) == 2
        assert ds[0]["next"] == "the next thing"


def test_dry_run_launches_and_writes_nothing():
    with tempfile.TemporaryDirectory() as t:
        gdir, ledger, hist = _world(t)
        launched = []
        rep = dv.run(go=0, goals_dir=gdir, ledger_path=ledger, history_path=hist,
                     launcher=lambda cwd, prompt, name, model='': launched.append(name) or True)
        assert rep["launched"] == 0 and launched == []
        assert not os.path.exists(ledger)


def test_go_launches_records_and_caps():
    with tempfile.TemporaryDirectory() as t:
        gdir, ledger, hist = _world(t, n_goals=3)
        launched = []
        rep = dv.run(go=2, goals_dir=gdir, ledger_path=ledger, history_path=hist,
                     launcher=lambda cwd, prompt, name, model='': launched.append((cwd, name)) or True)
        assert rep["launched"] == 2 and len(launched) == 2
        rows = [json.loads(l) for l in open(ledger)]
        assert len(rows) == 2
        assert rows[0]["goal"].startswith("g-") and rows[0]["milestone"]


def test_cooldown_prevents_double_dispatch():
    with tempfile.TemporaryDirectory() as t:
        gdir, ledger, hist = _world(t)
        dv.run(go=1, goals_dir=gdir, ledger_path=ledger, history_path=hist,
               launcher=lambda *a, **k: True)
        rep2 = dv.run(go=1, goals_dir=gdir, ledger_path=ledger, history_path=hist,
                      launcher=lambda *a, **k: True)
        assert rep2["launched"] == 0, "same goal dispatched twice inside cooldown"
        assert rep2["cooling"] == 1


def test_expired_cooldown_allows_redispatch():
    with tempfile.TemporaryDirectory() as t:
        gdir, ledger, hist = _world(t)
        old = time.time() - dv.COOLDOWN_S - 60
        with open(ledger, "w") as f:
            f.write(json.dumps({"goal": "g-0", "milestone": "the next thing",
                                "ts_epoch": old}) + "\n")
        rep = dv.run(go=1, goals_dir=gdir, ledger_path=ledger, history_path=hist,
                     launcher=lambda *a, **k: True)
        assert rep["launched"] == 1


def test_failed_launch_not_recorded():
    with tempfile.TemporaryDirectory() as t:
        gdir, ledger, hist = _world(t)
        rep = dv.run(go=1, goals_dir=gdir, ledger_path=ledger, history_path=hist,
                     launcher=lambda *a, **k: False)
        assert rep["launched"] == 0
        assert not os.path.exists(ledger) or not open(ledger).read().strip()


def test_cli_envelope():
    r = subprocess.run([sys.executable, os.path.join(SKILL, "drive.py"), "--json"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0
    env = json.loads(r.stdout)
    for k in ("success", "data", "metadata", "errors"):
        assert k in env
    assert env["metadata"]["dry_run"] is True


def _main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERR  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
