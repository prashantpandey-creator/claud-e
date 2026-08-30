"""Tests for swarm — a dispatch plan re-derived from the record each run.

WHY: `meditate go` dispatched whatever it found, in whatever order, at a
hardcoded model, with no projection of what it would cost. The plan makes
three things explicit — who takes each piece, how long a block it gets, and
the token spend — and every one of them has to be honest or the plan is worse
than none.

Run: python3 ~/.claude/skills/meditate/test_swarm.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import swarm  # noqa: E402


RATES = {"claude-opus-5": {"out": 900, "think": 300, "turns": 20000, "leash": 17},
         "claude-opus-4-8": {"out": 1900, "think": 1200, "turns": 2200, "leash": 5},
         "claude-sonnet-5": {"out": 980, "think": 230, "turns": 2800, "leash": 9}}


def test_an_alias_resolves_to_the_MOST_USED_model_not_the_most_verbose():
    """The first cut tie-broke on tokens-per-turn, so "opus" resolved to
    whichever opus was most VERBOSE (4-8 at 1,927/turn) instead of the one
    doing the work (opus-5, 15,424 turns against 2,222). Most-used is the
    representative rate; most-verbose is an accident of what it was asked."""
    assert swarm._match(RATES, "opus") == "claude-opus-5"
    assert swarm._match(RATES, "sonnet") == "claude-sonnet-5"
    assert swarm._match(RATES, "claude-opus-4-8") == "claude-opus-4-8"


def test_an_unknown_model_matches_NOTHING_rather_than_guessing():
    assert swarm._match(RATES, "gpt-9") is None
    assert swarm._match(RATES, "") is None


def test_with_no_price_table_the_plan_is_priced_in_TOKENS_and_says_so():
    """Prices are an external fact this tool must not guess. Tokens are
    measured on this machine and certain."""
    old = swarm.PRICING
    swarm.PRICING = os.path.join(tempfile.mkdtemp(), "absent.json")
    try:
        d = swarm.plan()
        assert d["priced"] is False and d["projected_usd"] is None, d
        text = swarm.render(d)
        assert "will not guess" in text and "TOKENS" in text
    finally:
        swarm.PRICING = old


def test_a_price_table_the_owner_wrote_IS_used():
    """FALSIFIER for the refusal: it must not be a blanket ban on money, only
    on inventing the number."""
    old = swarm.PRICING
    p = os.path.join(tempfile.mkdtemp(), "pricing.json")
    with open(p, "w") as f:
        json.dump({"claude-opus-5": 75.0, "claude-sonnet-5": 15.0}, f)
    swarm.PRICING = p
    try:
        d = swarm.plan()
        assert d["priced"] is True
        if d["agents"]:
            assert d["projected_usd"] is not None and d["projected_usd"] > 0, d
    finally:
        swarm.PRICING = old


def test_blocks_are_SHORT_because_the_curve_was_measured():
    """Per-turn cost climbs with session length — 57K cache-read tokens a turn
    at 28 turns, 376K at 5,836; the same work split short cost 334M against
    2,196M. So no block may be open-ended."""
    d = swarm.plan()
    for a in d["agents"]:
        assert 0 < a["turns"] <= 25, a
    text = swarm.render(d)
    assert "6.6x cheaper" in text or "6.6" in text


def test_broken_knowledge_is_planned_FIRST():
    """Same leverage order as the tree and the briefing — one order for the
    whole tool. Everything read downstream of a failed fact is suspect."""
    kinds = ["revive", "goal", "repair", "thread"]
    items = [{"kind": k, "what": k, "why": ""} for k in kinds]
    real = swarm.open_work
    swarm.open_work = lambda: items
    try:
        got = [a["kind"] for a in swarm.plan()["agents"]]
    finally:
        swarm.open_work = real
    assert got[0] == "repair", got
    assert got.index("goal") < got.index("revive"), got


def test_an_unmeasured_model_is_FLAGGED_not_projected_as_zero():
    """A model with no rate on this machine would silently project 0 tokens,
    and a zero reads as free."""
    items = [{"kind": "goal", "what": "g", "why": ""}]
    real_w, real_r = swarm.open_work, swarm._rates
    swarm.open_work = lambda: items
    swarm._rates = lambda: {}
    try:
        d = swarm.plan()
        assert d["unmeasured_models"] == 1, d
        assert "not a projection" in swarm.render(d)
    finally:
        swarm.open_work, swarm._rates = real_w, real_r


def test_the_plan_never_ranks_models_by_the_confounded_error_share():
    """Who takes a piece comes from models.pick, which states evidence vs
    default. The plan must not invent a second, quieter ranking."""
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "swarm.py")).read()
    assert "error_share" not in src, "the plan is ranking on the confounded number"
    d = swarm.plan()
    for a in d["agents"]:
        assert a["basis"] in ("evidence", "default", "fallback", "goal file"), a


def test_the_live_plan_renders():
    d = swarm.plan()
    text = swarm.render(d)
    assert "SWARM PLAN" in text
    print("       live: %d queued, %d planned, ~%s out-tokens"
          % (d["queued"], len(d["agents"]), format(d["projected_out_tokens"], ",")))


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
