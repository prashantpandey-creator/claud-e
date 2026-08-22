"""Tests for converse.py — the voice TURN over the graded data (Rule 0, A).

A voice assistant = mic (recognizer) -> turn() -> speaker (TTS). The mic and
TTS are the shell (voice-cockpit / web). This is the turn: understand a
spoken sentence, answer from what we actually know, and speak back — with
actions gated (voice never pushes/deploys, never spends agents unless the
shell explicitly opts in).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)
import converse as cv


def _world(tmp, repair=False, goal=True):
    med = os.path.join(tmp, "med"); os.makedirs(med)
    store = os.path.join(med, "nidra_store"); os.makedirs(store)
    gdir = os.path.join(tmp, "goals"); os.makedirs(gdir)
    if repair:
        open(os.path.join(med, "repair-queue.md"), "w").write("# Repair\n## mem_x\n")
    if goal:
        open(os.path.join(gdir, "g.md"), "w").write(
            "---\nname: mila\ntitle: Ship Mila\nproject: mila\ncwd: /r\n"
            "status: active\n---\n## Milestones\n- [x] a\n- [ ] resubmit to Apple\n")
    return dict(meditation_dir=med, store_dir=store, goals_dir=gdir,
                history_path=os.path.join(tmp, "h.jsonl"))


def test_status_question_answers_from_briefing():
    with tempfile.TemporaryDirectory() as t:
        w = _world(t, repair=True)
        r = cv.turn("what's bothering me right now", **w)
        assert r["intent"] == "status"
        assert "true" in r["speech"].lower() or "receipt" in r["speech"].lower()
        assert r["action"] is None


def test_project_question_answers_with_that_project():
    with tempfile.TemporaryDirectory() as t:
        w = _world(t)
        r = cv.turn("how is mila doing", **w)
        assert r["intent"] == "project"
        assert "mila" in r["speech"].lower()
        assert "resubmit" in r["speech"].lower() or "%" in r["speech"]


def test_command_is_gated_by_default():
    with tempfile.TemporaryDirectory() as t:
        w = _world(t, repair=True)
        r = cv.turn("fix the broken knowledge", **w)   # allow_actions default False
        assert r["intent"] == "command"
        assert r["action"] == "fix"
        assert r["executed"] is False, "voice must not spend agents unless opted in"
        assert "say" in r["speech"].lower() or "confirm" in r["speech"].lower() \
            or "meditate fix" in r["speech"].lower()


def test_command_runs_when_actions_allowed():
    with tempfile.TemporaryDirectory() as t:
        w = _world(t, repair=True)
        called = []
        r = cv.turn("run the fleet", allow_actions=True,
                    runner=lambda a: called.append(a) or {"started": True,
                                                          "output": "Launched 1"},
                    **w)
        assert r["intent"] == "command" and r["action"] == "go"
        assert r["executed"] is True and called == ["go"]
        assert "launch" in r["speech"].lower() or "1" in r["speech"]


def test_push_is_never_a_voice_command():
    with tempfile.TemporaryDirectory() as t:
        w = _world(t)
        for utter in ("push it", "deploy to production", "push to main"):
            r = cv.turn(utter, allow_actions=True,
                        runner=lambda a: (_ for _ in ()).throw(AssertionError("ran!")),
                        **w)
            assert r["action"] != "push" and not r["executed"], utter
            assert "terminal" in r["speech"].lower() or "won't" in r["speech"].lower() \
                or "owner" in r["speech"].lower()


def test_knowledge_question_queries_the_store():
    with tempfile.TemporaryDirectory() as t:
        w = _world(t)
        store = w["store_dir"]
        with open(os.path.join(store, "memories.jsonl"), "w") as f:
            f.write(json.dumps({"id": "m1", "active": True,
                "statement": "Deploys run via a systemd timer on the Mumbai box",
                "epistemic": {"evidence_status": "machine_checked"},
                "evidence": [{"source": "/x"}]}) + "\n")
        r = cv.turn("what do we know about deploys", **w)
        assert r["intent"] == "knowledge"
        assert "mumbai" in r["speech"].lower() or "systemd" in r["speech"].lower()


def test_unheard_asks_to_repeat():
    with tempfile.TemporaryDirectory() as t:
        w = _world(t, goal=False)
        r = cv.turn("", **w)
        assert r["intent"] == "unclear"


def test_cli_envelope():
    r = subprocess.run([sys.executable, os.path.join(SKILL, "converse.py"),
                        "what", "is", "bothering", "me", "--json"],
                       capture_output=True, text=True, timeout=90)
    assert r.returncode == 0
    env = json.loads(r.stdout)
    for k in ("success", "data", "metadata", "errors"):
        assert k in env
    assert "speech" in env["data"]


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
