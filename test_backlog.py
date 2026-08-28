"""Tests for the backlog — the things you looked at and put down.

The failure this guards against is silent and expensive in both directions.
If the backlog stops filtering, the companion nags you about work you already
decided against. If it filters too much, work disappears off your list and
nothing tells you. Both look like the tool working.

Run: python3 ~/.claude/skills/meditate/test_backlog.py
"""
from __future__ import annotations

import os
import sys
import tempfile

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)


def _fresh():
    """A throwaway store. Never the live one — a suite that writes into the
    file it is checking is how "he is quiet now" became a false claim here."""
    os.environ["MEDITATE_BACKLOG_FILE"] = os.path.join(
        tempfile.mkdtemp(prefix="backlog-"), "b.json")
    for m in ("backlog",):
        sys.modules.pop(m, None)
    import backlog
    return backlog


def test_the_key_is_identity_not_wording():
    """Milestones get reworded. A backlog keyed on the sentence forgets."""
    b = _fresh()
    a = {"kind": "goal", "goal": "mila-live", "milestone": "ship it",
         "say": "On Mila, the next step is ship it."}
    reworded = dict(a, say="On Mila unblocked, next up: ship it.")
    assert b.key_for(a) == b.key_for(reworded)
    # ...and two different milestones of one goal are two different things
    other = dict(a, milestone="something else")
    assert b.key_for(a) != b.key_for(other)
    # repair and sessions are singular — the kind IS the identity
    assert b.key_for({"kind": "repair"}) == "repair"


def test_put_down_and_bring_back():
    b = _fresh()
    k = "goal:mila-live:ship it"
    assert b.keys() == set()
    assert b.add(k, "On Mila, ship it.", "not this week")["ok"]
    assert k in b.keys()
    rows = b.items()
    assert len(rows) == 1 and rows[0]["note"] == "not this week"
    assert b.remove(k)["ok"]
    assert b.keys() == set()
    # removing something that was never there is a refusal, not a crash
    assert b.remove(k)["ok"] is False


def test_an_item_with_no_identity_is_refused():
    """A key-less row would be un-removable — parked forever, invisibly."""
    b = _fresh()
    assert b.add("")["ok"] is False
    assert b.keys() == set()


def test_the_agenda_stops_offering_what_you_put_down():
    """The whole point. Without this the backlog is decoration.

    Every item used to be re-offered every time, forever, and the only way to
    make one stop was to finish it — which is what makes a companion feel
    like it is nagging rather than helping.
    """
    b = _fresh()
    sys.modules.pop("voice", None)
    import voice
    before = [i for i in voice.agenda() if i.get("action")]
    if not before:
        raise AssertionError(
            "nothing on the agenda to park — this test needs a live item, and "
            "passing here without one would be testing nothing")
    target = before[0]
    b.add(b.key_for(target), target["say"])
    after = voice.agenda()
    assert all(b.key_for(i) != b.key_for(target) for i in after), \
        "a parked item is still being offered"
    b.remove(b.key_for(target))
    assert any(b.key_for(i) == b.key_for(target) for i in voice.agenda()), \
        "bringing it back did not put it back on the list"


def test_a_broken_store_never_hides_the_list():
    """Failing closed here would make work vanish with no way to notice."""
    b = _fresh()
    with open(os.environ["MEDITATE_BACKLOG_FILE"], "w") as f:
        f.write("{ this is not json")
    assert b.keys() == set(), "unreadable store must park nothing"
    sys.modules.pop("voice", None)
    import voice
    assert voice.agenda(), "a broken backlog emptied the agenda"


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
