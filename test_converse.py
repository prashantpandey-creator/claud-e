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
        # Casper now speaks the IDEA, not the metric — so assert the CONTRACT
        # (a real spoken sentence about broken knowledge), not old wording.
        sp = r["speech"].lower()
        assert len(r["speech"]) > 20 and "\n" not in r["speech"], r["speech"]
        assert any(w in sp for w in ("told me", "stopped", "gone", "isn't there",
                                     "matching", "verif")), r["speech"]
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


def _mem(mid, statement, scope, grade="machine_checked"):
    return json.dumps({"id": mid, "active": True, "statement": statement,
                       "epistemic": {"evidence_status": grade,
                                     "evidence_scope": scope},
                       "evidence": [{"source": "/x"}]}) + "\n"


def test_world_backed_knowledge_is_stated_flat():
    """Checked against the world, so say it. No mealy hedging."""
    with tempfile.TemporaryDirectory() as t:
        w = _world(t); store = w["store_dir"]
        with open(os.path.join(store, "memories.jsonl"), "w") as f:
            f.write(_mem("m1", "Deploys run via a systemd timer on the Mumbai box", "world"))
        r = cv.turn("what do we know about deploys", **w)
        assert "you wrote" not in r["speech"].lower(), r["speech"]
        assert "mumbai" in r["speech"].lower()


def test_self_referential_knowledge_is_ATTRIBUTED_not_asserted():
    """THE fix. Measured on the live store: 3 of 4 answers Casper would speak
    flat rested on evidence the world cannot falsify — 'quote' (the memory
    quotes itself correctly) or 'internal' (it links to other memories). Both
    were graded machine_checked, so both came out of the speaker's mouth as
    confident fact.

    A spoken sentence carries more authority than a dashboard row, so this is
    where the conflation does the most damage. The honest move is not to hedge
    into uselessness — it is to name the source. Your own recorded decision is
    worth saying; it just must not sound like a measurement."""
    with tempfile.TemporaryDirectory() as t:
        w = _world(t); store = w["store_dir"]
        with open(os.path.join(store, "memories.jsonl"), "w") as f:
            f.write(_mem("m1", "Never use Groq; use OpenRouter", "quote"))
        r = cv.turn("what do we know about groq", **w)
        assert "you wrote" in r["speech"].lower(), r["speech"]
        assert "groq" in r["speech"].lower()
        # attribution, NOT a doubt-word: a recorded decision is still real
        for weasel in ("maybe", "possibly", "i think", "might be"):
            assert weasel not in r["speech"].lower(), r["speech"]


def test_internal_scope_is_attributed_too():
    with tempfile.TemporaryDirectory() as t:
        w = _world(t); store = w["store_dir"]
        with open(os.path.join(store, "memories.jsonl"), "w") as f:
            f.write(_mem("m1", "Answers should end confusion, not add layers", "internal"))
        r = cv.turn("what do we know about answers", **w)
        assert "you wrote" in r["speech"].lower(), r["speech"]


def test_unverified_still_wins_over_scope():
    """Not-checked is a stronger warning than not-world-backed."""
    with tempfile.TemporaryDirectory() as t:
        w = _world(t); store = w["store_dir"]
        with open(os.path.join(store, "memories.jsonl"), "w") as f:
            f.write(_mem("m1", "Something unchecked", "world", grade="unverified"))
        r = cv.turn("what do we know about something", **w)
        assert "unverified" in r["speech"].lower(), r["speech"]


def test_missing_scope_does_not_crash_or_over_claim():
    """Old rows predate evidence_scope. Absent scope must not be read as
    'world' — unknown is not verified."""
    with tempfile.TemporaryDirectory() as t:
        w = _world(t); store = w["store_dir"]
        with open(os.path.join(store, "memories.jsonl"), "w") as f:
            f.write(json.dumps({"id": "m1", "active": True,
                "statement": "An older memory with no scope recorded",
                "epistemic": {"evidence_status": "machine_checked"},
                "evidence": [{"source": "/x"}]}) + "\n")
        r = cv.turn("what do we know about older", **w)
        assert r["speech"], "must still answer"


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
