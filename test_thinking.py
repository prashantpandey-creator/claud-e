"""Tests for thinking — the live "what am I doing" the face shows while busy.

Contract:
  - note() then read() round-trips the step
  - a step older than STALE_S is NOT reported: a process that died mid-step
    would otherwise leave the face saying "reading what I know" forever
  - clear() empties it, and read() on an empty/missing file returns None
  - nothing here ever raises into the caller — it runs inside slow paths

Run: python3 ~/.claude/skills/meditate/test_thinking.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)

os.environ["MEDITATE_THINKING_FILE"] = os.path.join(
    tempfile.mkdtemp(prefix="thinking-"), "thinking.jsonl")
import thinking as th


def test_a_step_round_trips():
    th.note("reading what I know", "470 facts")
    r = th.read()
    assert r and r["step"] == "reading what I know", r
    assert r["detail"] == "470 facts"
    assert r["age_s"] < 5


def test_a_stale_step_is_not_reported():
    """The falsifying case: a crashed process must not leave the face
    narrating a step that stopped two hours ago."""
    with open(th.PATH, "w") as f:
        f.write(json.dumps({"step": "thinking it through",
                            "ts": time.time() - (th.STALE_S + 60)}) + "\n")
    assert th.read() is None, "a stale step was reported as current"


def test_clear_and_empty_are_silent():
    th.note("something")
    th.clear()
    assert th.read() is None
    os.remove(th.PATH)
    assert th.read() is None, "a missing file must read as 'nothing running'"


def test_never_raises_even_when_the_path_is_impossible():
    """It runs inside the slow paths; it may never be the thing that fails."""
    old = th.PATH
    try:
        th.PATH = "/proc/nonexistent/thinking.jsonl"
        th.note("x")          # must not raise
        assert th.read() is None
        th.clear()            # must not raise
    finally:
        th.PATH = old


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
