"""Tests for creed — his standing rules, derived once and ordered by recency.

WHY (measured 2026-08-29):

    ~/.claude/meditation/rules.md   7 rules, hand-written, last touched 08-25
    the memory store                43 rules, derived

All 7 hand-written ones were grounded in real memories — nothing invented for
him. But 36 reached only the mascot, which talks, and never the dispatched
agents, which act. The missing ones are precisely the action-governing ones:
work in a worktree, commit local first, clean the disk before a training run,
one test call and real usage numbers before any bulk LLM spend.

And the store CONTRADICTS ITSELF, because he changed his mind and that is his
right: "commit to a LOCAL branch and STOP" (2026-07-10) against "push
automatically when the suite is green" (2026-08-25, explicitly WIDENED).
Handed to a model unordered, that resolves by whichever sentence reads more
forcefully — the same failure advisor.py already recorded for its own
hand-written persona.

Run: python3 ~/.claude/skills/meditate/test_creed.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import creed  # noqa: E402


def _fake(text, date, source="m.md"):
    return {"text": text, "date": date,
            "kind": "action" if creed._ACTION.search(text) else "voice",
            "source": source}


def test_newest_rule_leads():
    """The whole point. A correction from this week must be read before a
    rule from July, because where they conflict the later one is his."""
    rows = creed.rules()
    dated = [r for r in rows if r["date"]]
    assert dated, "no rule carried a date — recency ordering is doing nothing"
    assert dated == sorted(dated, key=lambda r: r["date"], reverse=True)


def test_an_UNDATED_rule_never_outranks_a_dated_one():
    """An undated sentence cannot claim to supersede a correction he stamped.
    Sorting undated as empty-string would put it FIRST under reverse order —
    which would let the oldest thing in the store lead the prompt."""
    rows = [_fake("never push without the suite green", ""),
            _fake("push when the suite is green", "2026-08-25")]
    got = creed.standing(None, 9000, sorted(
        rows, key=lambda r: r["date"] or "0000-00-00", reverse=True))
    assert got[0]["date"] == "2026-08-25", [r["date"] for r in got]


def test_the_supersession_case_that_started_this():
    """Live: both of these are in his store and they disagree. The newer one
    must come first — this test reads the REAL store, so it breaks the day
    the ordering silently regresses."""
    rows = creed.rules()
    push_new = next((i for i, r in enumerate(rows)
                     if "push automatically" in r["text"].lower()
                     or "push everything when" in r["text"].lower()), None)
    push_old = next((i for i, r in enumerate(rows)
                     if "local branch first" in r["text"].lower()
                     or "commit to a local" in r["text"].lower()), None)
    if push_new is not None and push_old is not None:
        assert push_new < push_old, \
            "the superseded rule leads the one that replaced it"


def test_action_and_voice_are_split_by_what_they_GOVERN():
    a = _fake("always work in a worktree off origin/main", "2026-07-18")
    v = _fake("answer in one line then ask only the next step", "2026-07-01")
    assert a["kind"] == "action" and v["kind"] == "voice"


def test_the_budget_TRUNCATES_and_says_so():
    """A compressed rule is a rule someone rewrote, and a rewritten rule is
    not his any more. So the tail is dropped whole — and the block says how
    many went, because a silent cut reads as 'that is all of them'."""
    rows = [_fake("push when green " + "x" * 60, "2026-08-%02d" % (d + 1))
            for d in range(20)]
    out = creed.render("action", budget=300, rows=rows)
    assert "not shown" in out, out[-120:]
    kept = [l for l in out.splitlines() if l.startswith("- [")]
    assert 0 < len(kept) < len(rows)
    for l in kept:
        assert "x" * 60 in l, "a rule was summarised instead of dropped"


def test_every_rule_is_QUOTED_never_reworded():
    """The store's own sentences reach the prompt verbatim."""
    rows = creed.rules()[:6]
    if not rows:
        return
    out = creed.render(None, 9000, creed.rules())
    for r in rows:
        assert r["text"] in out, "reworded on the way to the prompt: %r" % r["text"][:60]


def test_a_machine_with_NO_memories_derives_nothing():
    """FALSIFIER, and the product case: a stranger has no corrections yet, so
    the creed must be empty rather than inheriting the author's. Proven under
    a real fake HOME, not by mocking the reader."""
    import subprocess, tempfile
    fake = tempfile.mkdtemp()
    os.makedirs(os.path.join(fake, ".claude", "meditation"), exist_ok=True)
    r = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r); import creed; "
         "print(len(creed.rules())); print(creed.render('action'))"
         % os.path.dirname(os.path.abspath(__file__))],
        env=dict(os.environ, HOME=fake), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-300:]
    lines = r.stdout.strip().splitlines()
    assert lines[0] == "0", "a stranger inherited %s rules" % lines[0]
    assert len(lines) == 1 or not lines[1].strip(), "a stranger got a rules block"


def test_the_shipped_DEFAULTS_carry_nothing_personal():
    """The hook appends this block only for someone who has their own
    rules.md. Appending it to the shipped defaults broke test_hook, correctly:
    on a shared memory root one person's corrections would reach another
    person's agents."""
    hook = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "hooks", "meditate-hook.sh"), errors="ignore").read()
    assert "HAS_OWN_RULES" in hook, "the derived block is no longer gated"
    i = hook.find("DERIVED=")
    assert 'HAS_OWN_RULES" = yes' in hook[i:i + 200], \
        "the gate and the derivation drifted apart"


def _main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print("  ok   %s" % fn.__name__)
        except AssertionError as e:
            failed += 1; print("  FAIL %s: %s" % (fn.__name__, e))
        except Exception as e:
            failed += 1; print("  ERR  %s: %s: %s" % (fn.__name__, type(e).__name__, e))
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
