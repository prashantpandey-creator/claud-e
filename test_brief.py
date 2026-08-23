"""Tests for brief — the console's spoken lead. compose() is pure, so these
feed worlds and read exactly what would be said.

Contract:
  - what is OWED leads; one suggestion closes; never a menu
  - a near-tie is narrated as a split, not as dominance
  - repair arrives as a list (console state) or a dict (CLI) — both count
  - same world, same words: content-keyed, never random
"""
from __future__ import annotations

import os
import sys

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)

from brief import compose


def _world(**over):
    w = {
        "triage": {"action_items": []},
        "projects": [], "projects_window_days": 23,
        "store": {"active": 100, "verified": 100},
        "repair": [], "next": "meditate go",
    }
    w.update(over)
    return w


def test_owed_chats_lead_the_brief():
    w = _world(triage={"action_items": [
        {"action": "reply", "age_h": 31.0, "last_said": "lets get back to tutor"}]})
    lines = compose(w)
    assert "waiting on you" in lines[0]
    assert "lets get back to tutor" in lines[0]


def test_closes_with_one_suggestion_never_a_menu():
    lines = compose(_world())
    assert lines, "must always say something"
    last = lines[-1]
    assert last.startswith(("I'd", "Next", "Nothing")), last
    assert sum(1 for l in lines if l.startswith("I'd")) <= 1, "one suggestion only"


def test_near_tie_is_a_split_not_dominance():
    """purangpt once 'led' meditate 1023 msgs to 1018 — a 5-message margin."""
    w = _world(projects=[
        {"project": "purangpt", "messages": 1023, "commits_recent": 328},
        {"project": "meditate", "messages": 1018, "commits_recent": 79}])
    text = " ".join(compose(w))
    assert "split about evenly" in text
    assert "one place" not in text


def test_clear_leader_is_said_plainly():
    w = _world(projects=[
        {"project": "purangpt", "messages": 1000, "commits_recent": 300},
        {"project": "mila", "messages": 100, "commits_recent": 5}])
    text = " ".join(compose(w))
    assert "one place" in text and "purangpt" in text


def test_repair_as_list_and_as_dict_both_count():
    as_list = " ".join(compose(_world(repair=[{"id": "m1"}])))
    as_dict = " ".join(compose(_world(repair={"open": 1})))
    for text in (as_list, as_dict):
        assert "stopped matching reality" in text, text
        assert "it misleads" in text, "singular grammar: " + text
    plural = " ".join(compose(_world(repair=[{}, {}, {}])))
    assert "they mislead" in plural, plural


def test_healthy_store_and_broken_store_cannot_both_be_claimed():
    """The on-screen contradiction: 'everything checks out' directly above
    '1 fact failed reality-check'. One dict in, one verdict out."""
    text = " ".join(compose(_world(repair=[{"id": "m1"}])))
    assert "still checks out" not in text


def test_same_world_same_words():
    w = _world(triage={"action_items": [
        {"action": "reply", "age_h": 5.0, "last_said": "hello there"}]})
    assert compose(w) == compose(w) == compose(dict(w))


def test_urls_are_not_read_aloud():
    w = _world(triage={"action_items": [
        {"action": "reply", "age_h": 5.0,
         "last_said": "https://job-boards.example.com/x/123 , is this open?"}]})
    text = " ".join(compose(w))
    assert "https://" not in text
    assert "a link" in text


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
