"""Tests for milestones — checking the ledger against the world.

The two tests that matter most are regressions of bugs this file's own first
version shipped, both in the dangerous direction: claiming a milestone was
done when it was not.

Run: python3 ~/.claude/skills/meditate/test_milestones.py
"""
from __future__ import annotations

import os
import sys
import tempfile

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)

import milestones as ms


def test_an_arrow_target_is_the_goal_not_the_starting_point():
    """'repaired: 0 -> 1' means get to 1. Reading the first number made the
    milestone its own target and declared untouched work complete."""
    text = "the Mumbai drifted memory repaired and re-verified (repaired: 0 -> 1)"
    v, ev = ms._check_repaired(text, {}, {"repaired": 0})
    assert v is False, ev
    assert "needs 1" in ev, ev
    v2, _ = ms._check_repaired(text, {}, {"repaired": 1})
    assert v2 is True


def test_a_bare_threshold_still_reads():
    v, ev = ms._check_repaired("repair loop closed: repaired >= 2", {},
                               {"repaired": 2})
    assert v is True, ev


def test_one_checker_may_not_pass_a_milestone_with_several_conditions():
    """CLAIM SCOPE = CHECK SCOPE. Finding a LICENSE file was reported as
    'LICENSE + versioned release tags on both repos' being done."""
    text = "LICENSE + versioned release tags on both repos (meditate, nidra)"
    assert ms.has_multiple_conditions(text)
    res = ms.check_milestone(text, {"cwd": SKILL}, {"repaired": 0})
    assert res["verdict"] is not True, res
    assert "more than one thing" in res["evidence"], res


def test_one_unmet_condition_still_fails_the_whole_milestone():
    """A partial checker may not PASS a compound milestone, but it may fail
    one — a single unmet condition is enough to keep it open."""
    with tempfile.TemporaryDirectory() as t:
        res = ms.check_milestone("tagged release and notes published",
                                 {"cwd": t}, {})
        assert res["verdict"] is not True


def test_a_single_condition_can_still_be_passed():
    res = ms.check_milestone("stilling pass run", {}, {"stillness_age_days": 0.1})
    assert res["verdict"] is True, res
    assert "0.1 days" in res["evidence"]


def test_what_it_cannot_know_it_says_it_cannot_know():
    res = ms.check_milestone("App Store privacy labels submitted", {}, {})
    assert res["verdict"] is None and res["checker"] is None, res


def test_status_frozen_into_milestone_text_is_flagged():
    """'(currently WAITING)' is a claim about now, frozen in a file, inside a
    line that is supposed to describe a condition."""
    assert ms.stale_wording("iOS subscriptions approved (currently WAITING)")
    assert ms.stale_wording("Android sign-in repaired (currently DEAD)")
    assert ms.stale_wording("stilling pass run (STILLNESS.md 7+ days overdue)")
    assert not ms.stale_wording("Mila iOS approved and live")
    assert not ms.stale_wording("Razorpay key restored to the right env")


def test_audit_never_reports_a_verdict_without_its_evidence():
    d = ms.audit()
    for r in d["looks_done"] + d["confirmed_open"]:
        assert r["evidence"], r
        assert r["checker"], r


def test_push_as_a_noun_belongs_to_someone_else():
    """'full suite green on push (macOS runner)' is a milestone about CI. It
    matched the push checker and was reported done because the repo happened
    to have nothing unpushed — an unbuilt pipeline, marked complete."""
    v, _ = ms._check_pushed("CI on GitHub: full suite green on push (macOS runner)",
                            {"cwd": SKILL}, {})
    assert v is None, "a CI milestone is not a push milestone"


def test_a_real_push_milestone_still_checks():
    """Three-valued: yes / no / cannot tell — and CANNOT TELL still has to
    say why. Caught in a fresh clone with no upstream, where this returned
    (None, "") and read exactly like a milestone the checker skips."""
    v, ev = ms._check_pushed("backend branch pushed and deployed",
                             {"cwd": SKILL}, {})
    assert v in (True, False, None), v
    assert ev, "a verdict must carry its evidence, including 'cannot tell'"
    if v is None:
        assert "no upstream" in ev or "no working directory" in ev, ev


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
