"""Tests for voice.py — Casper: what to say, and whether now is the moment.

Two honest decisions, both deterministic:
  briefing()          — the SINGLE highest-leverage thing to say right now,
                        in plain words, drawn from data that already exists
  interruptibility()  — flow / pause / idle / away, measured from real
                        activity. NOT mood-reading: a proxy, labeled as one.
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
import voice as vc


def _coord(tmp, files_age_s=None):
    """A presence dir with one session whose last edit is files_age_s ago."""
    cd = os.path.join(tmp, "sessions"); os.makedirs(cd)
    if files_age_s is not None:
        now = time.time()
        p = os.path.join(cd, "sess-a.json")
        with open(p, "w") as f:
            json.dump({"sid": "sess-a", "cwd": "/repo",
                       "files": {"/repo/x.py": now - files_age_s}}, f)
        os.utime(p, (now - files_age_s, now - files_age_s))
    return cd


# ---- interruptibility: measured, never guessed --------------------------

def test_flow_when_editing_seconds_ago():
    with tempfile.TemporaryDirectory() as t:
        cd = _coord(t, files_age_s=20)
        r = vc.interruptibility(coord_dir=cd)
        assert r["state"] == "flow", r
        assert r["interrupt_ok"] is False, "never break flow"


def test_pause_when_live_but_idle_a_few_minutes():
    with tempfile.TemporaryDirectory() as t:
        cd = _coord(t, files_age_s=8 * 60)   # 8 min since last edit, still live
        r = vc.interruptibility(coord_dir=cd)
        assert r["state"] == "pause", r
        assert r["interrupt_ok"] is True, "a pause is the moment"


def test_away_when_no_live_session():
    with tempfile.TemporaryDirectory() as t:
        cd = _coord(t, files_age_s=None)     # nobody live
        r = vc.interruptibility(coord_dir=cd)
        assert r["state"] == "away", r
        assert r["interrupt_ok"] is False, "don't talk to an empty room"


def test_proxy_is_labeled_not_mood():
    with tempfile.TemporaryDirectory() as t:
        r = vc.interruptibility(coord_dir=_coord(t, files_age_s=20))
        assert "proxy" in r["basis"].lower() or "activity" in r["basis"].lower()


# ---- briefing: one thing, highest leverage ------------------------------

def _world(tmp, repair=False):
    med = os.path.join(tmp, "med"); os.makedirs(med)
    store = os.path.join(med, "nidra_store"); os.makedirs(store)
    gdir = os.path.join(tmp, "goals"); os.makedirs(gdir)
    if repair:
        with open(os.path.join(med, "repair-queue.md"), "w") as f:
            f.write("# Repair queue\n## mem_x\n")
    return med, store, gdir


def test_briefing_leads_with_repair_when_knowledge_broke():
    with tempfile.TemporaryDirectory() as t:
        med, store, gdir = _world(t, repair=True)
        b = vc.briefing(meditation_dir=med, store_dir=store, goals_dir=gdir,
                        history_path=os.path.join(t, "h.jsonl"))
        assert b["headline"], b
        assert b["kind"] == "repair", "broken knowledge must be the top whisper"
        assert b["action"] == "meditate fix", "and name the fix action"


def test_briefing_is_one_thing_not_a_list():
    with tempfile.TemporaryDirectory() as t:
        med, store, gdir = _world(t)
        with open(os.path.join(gdir, "g.md"), "w") as f:
            f.write("---\nname: g\ntitle: Ship it\nproject: p\ncwd: /r\n"
                    "status: active\n---\n## Milestones\n- [ ] the task\n")
        b = vc.briefing(meditation_dir=med, store_dir=store, goals_dir=gdir,
                        history_path=os.path.join(t, "h.jsonl"))
        assert isinstance(b["headline"], str) and "\n" not in b["headline"]


def test_briefing_empty_world_says_all_clear():
    with tempfile.TemporaryDirectory() as t:
        med, store, gdir = _world(t)
        b = vc.briefing(meditation_dir=med, store_dir=store, goals_dir=gdir,
                        history_path=os.path.join(t, "h.jsonl"))
        assert b["headline"], "even quiet must say something honest"


def test_cli_envelope():
    r = subprocess.run([sys.executable, os.path.join(SKILL, "voice.py"), "--json"],
                       capture_output=True, text=True, timeout=90)
    assert r.returncode == 0
    env = json.loads(r.stdout)
    for k in ("success", "data", "metadata", "errors"):
        assert k in env
    assert "headline" in env["data"]["briefing"]
    assert "state" in env["data"]["timing"]


def _main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"  ok   {fn.__name__}")
        except AssertionError as e:
            failed += 1; print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:
            failed += 1; print(f"  ERR  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
