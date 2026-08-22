"""Tests for attention (is the person here?) and vocabulary (their words).

Contract:
  - idle_seconds reads the real HID clock, or returns None; never a guess
  - signals() reports WHICH signals it actually got
  - a meeting app in front is recognised as "do not speak"
  - vocabulary contains this workspace's own names and not ordinary English
  - vocabulary never exceeds the cap that keeps the recogniser useful

Run: python3 ~/.claude/skills/meditate/test_attention.py
"""
from __future__ import annotations

import os
import sys
import tempfile

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)

import attention as at
import vocabulary as vb


def test_idle_is_a_real_number_or_honestly_none():
    v = at.idle_seconds()
    assert v is None or (isinstance(v, float) and v >= 0), v
    # this machine has a HID clock; if that ever stops being true the fallback
    # path in voice.interruptibility is what carries it
    assert v is not None, "no HID idle clock — timing falls back to guessing"


def test_signals_say_which_ones_they_actually_got():
    s = at.signals()
    assert "measured" in s and isinstance(s["measured"], list)
    if s["idle_s"] is not None:
        assert "idle" in s["measured"]
    if s["frontmost"]:
        assert "frontmost" in s["measured"]


def test_meeting_apps_are_recognised():
    assert "zoom.us" in at.MEETING_APPS
    assert "FaceTime" in at.MEETING_APPS
    # and a terminal is not a meeting
    assert "Terminal" not in at.MEETING_APPS
    assert "Terminal" in at.WORK_APPS


def test_vocabulary_holds_this_workspace_and_not_the_dictionary():
    ts = [t.lower() for t in vb.terms()]
    assert "casper" in ts and "nidra" in ts, "the tool's own verbs are missing"
    # ordinary English must not be in here: it crowds out the real names under
    # the cap, and teaching a recogniser the word "waiting" helps nobody
    for junk in ("waiting", "first", "license", "not"):
        assert junk not in ts, f"{junk} is ordinary English, not a name"


def test_vocabulary_respects_the_cap():
    assert len(vb.terms()) <= vb.MAX_TERMS
    assert len(vb.terms(max_terms=7)) == 7


def test_is_name_keeps_products_and_drops_shouting():
    assert vb._is_name("PuranGPT"), "CamelCase product name"
    assert vb._is_name("Razorpay"), "capitalised, not an English word"
    assert not vb._is_name("WAITING"), "capitals are emphasis in this corpus"
    assert not vb._is_name("Waiting"), "an ordinary English word"
    assert not vb._is_name("The"), "a stop word"


def test_vocabulary_survives_an_empty_install():
    """A new user has no memories and no goals; the tool's own verbs remain."""
    with tempfile.TemporaryDirectory() as t:
        empty = os.path.join(t, "store")
        os.makedirs(empty)
        ts = vb.terms(store_dir=empty, goals_dir=os.path.join(t, "nogoals"))
        assert len(ts) >= len(vb.COMMANDS)
        assert "Casper" in ts


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
