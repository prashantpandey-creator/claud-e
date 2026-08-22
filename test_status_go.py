"""Tests for status.py + go.py — the two-verb porcelain (Rule 0, A).

Contract:
  status:
  - one screen, ends with exactly ONE `next:` line — a decision, not a menu
  - priority: repair queue > dispatchable goals > overdue stilling > rest
  go:
  - executes the same priority: repair agent first when the queue is open,
    then goal agents up to the cap; prints what it DID
  - launcher injectable; repair launch recorded nowhere twice inside cooldown

Run: python3 ~/.claude/skills/meditate/test_status_go.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)
import status as st
import go as g

GOAL = """---
name: g-a
title: Ship it
project: p
cwd: /repo/p
status: active
---
## Milestones
- [ ] first open thing
"""


def _world(t, with_goal=True, with_repair=False):
    med = os.path.join(t, "med"); os.makedirs(med, exist_ok=True)
    store = os.path.join(med, "nidra_store"); os.makedirs(store, exist_ok=True)
    gdir = os.path.join(t, "goals"); os.makedirs(gdir, exist_ok=True)
    if with_goal:
        with open(os.path.join(gdir, "g.md"), "w") as f:
            f.write(GOAL)
    if with_repair:
        with open(os.path.join(med, "repair-queue.md"), "w") as f:
            f.write("# Repair queue\n\n## mem_x  [unverified]\n- statement: s\n")
    return med, store, gdir


def test_status_single_next_line():
    with tempfile.TemporaryDirectory() as t:
        med, store, gdir = _world(t)
        out = st.status_text(meditation_dir=med, store_dir=store, goals_dir=gdir,
                             history_path=os.path.join(t, "h.jsonl"),
                             ledger_path=os.path.join(t, "d.jsonl"))
        assert out.count("next:") == 1, out


def test_status_priority_repair_beats_goals():
    with tempfile.TemporaryDirectory() as t:
        med, store, gdir = _world(t, with_repair=True)
        out = st.status_text(meditation_dir=med, store_dir=store, goals_dir=gdir,
                             history_path=os.path.join(t, "h.jsonl"),
                             ledger_path=os.path.join(t, "d.jsonl"))
        nxt = [l for l in out.splitlines() if "next:" in l][0]
        assert "repair" in nxt.lower(), nxt


def test_status_goals_when_no_repair():
    with tempfile.TemporaryDirectory() as t:
        med, store, gdir = _world(t)
        out = st.status_text(meditation_dir=med, store_dir=store, goals_dir=gdir,
                             history_path=os.path.join(t, "h.jsonl"),
                             ledger_path=os.path.join(t, "d.jsonl"))
        nxt = [l for l in out.splitlines() if "next:" in l][0]
        assert "meditate go" in nxt, nxt


def test_go_repair_first_then_goals():
    with tempfile.TemporaryDirectory() as t:
        med, store, gdir = _world(t, with_repair=True)
        launched = []
        rep = g.run(n=2, meditation_dir=med, store_dir=store, goals_dir=gdir,
                    history_path=os.path.join(t, "h.jsonl"),
                    ledger_path=os.path.join(t, "d.jsonl"),
                    launcher=lambda cwd, prompt, name: launched.append(name) or True)
        assert rep["repair_launched"] is True
        assert rep["goals_launched"] == 1          # cap 2 = 1 repair + 1 goal
        assert launched[0].startswith("repair"), launched


def test_go_goals_only_when_clean():
    with tempfile.TemporaryDirectory() as t:
        med, store, gdir = _world(t)
        launched = []
        rep = g.run(n=2, meditation_dir=med, store_dir=store, goals_dir=gdir,
                    history_path=os.path.join(t, "h.jsonl"),
                    ledger_path=os.path.join(t, "d.jsonl"),
                    launcher=lambda cwd, prompt, name: launched.append(name) or True)
        assert rep["repair_launched"] is False
        assert rep["goals_launched"] == 1
        assert launched and launched[0].startswith("goal-")


def test_go_zero_is_dry():
    with tempfile.TemporaryDirectory() as t:
        med, store, gdir = _world(t, with_repair=True)
        launched = []
        rep = g.run(n=0, meditation_dir=med, store_dir=store, goals_dir=gdir,
                    history_path=os.path.join(t, "h.jsonl"),
                    ledger_path=os.path.join(t, "d.jsonl"),
                    launcher=lambda *a: launched.append(1) or True)
        assert launched == [] and rep["would"], rep


def test_cli_status_envelope():
    r = subprocess.run([sys.executable, os.path.join(SKILL, "status.py"), "--json"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    env = json.loads(r.stdout)
    for k in ("success", "data", "metadata", "errors"):
        assert k in env
    assert env["data"]["next"], "status must always decide a next action"


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
