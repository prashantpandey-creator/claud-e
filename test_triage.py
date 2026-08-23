"""Tests for triage — which chats are owed something.

Contract:
  - the last HUMAN turn decides the state, not recency and not size
  - a chat whose last word was yours is waiting on a reply
  - a chat where the assistant asked and stopped is waiting on a reply
  - a chat that wrapped up is finished, not resumable
  - the tool's OWN headless calls are not chats and never become action items
  - only waiting/mid-work become action items; everything else is collapsed

Run: python3 ~/.claude/skills/meditate/test_triage.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)

import triage as tr


def _write(dirpath: str, name: str, turns):
    """turns: [(role, text)] — written as a minimal transcript."""
    os.makedirs(dirpath, exist_ok=True)
    p = os.path.join(dirpath, name + ".jsonl")
    now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    with open(p, "w") as f:
        # pad so the file clears the 2000-byte "too small to matter" floor
        f.write(json.dumps({"type": "meta", "pad": "x" * 2100}) + "\n")
        for role, text in turns:
            f.write(json.dumps({"type": role, "timestamp": now,
                                "message": {"role": role, "content": text}}) + "\n")
    return p


def _state(turns, messages=5, age_h=1.0):
    with tempfile.TemporaryDirectory() as t:
        p = _write(t, "s", turns)
        turn = tr.last_turn(p)
        return tr.classify(turn, age_h, messages)


def test_your_word_last_means_it_is_waiting_on_a_reply():
    c = _state([("assistant", "here is the change"), ("user", "lets get back to tutor")])
    assert c["state"] == "waiting" and c["action"] == "reply", c


def test_a_question_left_hanging_is_waiting_on_a_reply():
    c = _state([("user", "which one?"),
                ("assistant", "Two options here. Which would you prefer?")])
    assert c["state"] == "waiting" and c["action"] == "reply", c


def test_stopping_part_way_through_is_resumable():
    c = _state([("user", "do it"),
                ("assistant", "Right — I'll now wire the second half of this.")])
    assert c["state"] == "mid-work" and c["action"] == "resume", c


def test_a_chat_that_wrapped_up_is_finished():
    c = _state([("user", "thanks"),
                ("assistant", "Pushed. doctor: all green.")])
    assert c["state"] == "finished" and c["action"] == "archive", c


def test_a_barely_started_chat_is_not_an_action_item():
    c = _state([("user", "hi")], messages=1)
    assert c["action"] is None, c


def test_the_tools_own_prompts_are_not_chats():
    """The advisor opens a session per question. Nobody ever replies to a
    prompt, so every one of them looked like an unanswered message."""
    sysprompt = ("You are Casper. You have read this person's work.\n"
                 "HARD RULES:\n" + "1. Reason only over the facts. " * 40)
    with tempfile.TemporaryDirectory() as t:
        p = _write(t, "a", [("user", sysprompt), ("assistant", "Fix payments.")])
        assert tr.last_turn(p)["machine"] is True


def test_a_long_human_message_is_still_a_human_message():
    """The filter keys on the SHAPE of a system prompt, not on length —
    a person writing a long brief must not be thrown away."""
    long_human = "ok so " + ("here is what I want you to build next. " * 40)
    with tempfile.TemporaryDirectory() as t:
        p = _write(t, "b", [("assistant", "ready"), ("user", long_human)])
        assert tr.last_turn(p)["machine"] is False


def test_promptSource_is_not_the_signal():
    """Guard against reinstating a filter that deleted every real chat:
    a typed human message carries promptSource 'sdk' through the desktop app."""
    assert not hasattr(tr, "_SDK_FILTER"), "promptSource filtering came back"


def test_waiting_outranks_everything_else():
    w = tr._score("waiting", 100.0, 3)
    m = tr._score("mid-work", 0.0, 40)
    r = tr._score("resumable", 0.0, 40)
    assert w > m > r, (w, m, r)


def test_only_owed_chats_become_action_items():
    d = tr.triage()
    for it in d["action_items"]:
        assert it["action"] in ("reply", "resume"), it
    assert len(d["action_items"]) <= 8, "a list nobody can act on is a wall"


def _main():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
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
