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


def test_the_alias_FALLBACK_is_most_used_not_most_verbose():
    """The fallback, for an alias no dispatch has used yet.

    The first cut tie-broke on tokens-per-turn, so "opus" resolved to
    whichever opus was most VERBOSE (4-8 at 1,927/turn) instead of the one
    doing the work. Most-used is the representative rate. This rule now sits
    BEHIND the real-run lookup — a dispatch that actually happened beats any
    name match — so it is exercised here with an empty spend ledger.
    """
    import models
    real = models.spend
    models.spend = lambda ledger=None: {"rows": [], "runs": 0,
                                        "total_usd": 0, "per_model": []}
    try:
        assert swarm._match(RATES, "opus") == "claude-opus-5"
        assert swarm._match(RATES, "sonnet") == "claude-sonnet-5"
        assert swarm._match(RATES, "claude-opus-4-8") == "claude-opus-4-8"
    finally:
        models.spend = real


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



def test_a_goal_row_carries_its_NAME_not_just_prose():
    """The console's run button takes the name as its argument. The first cut
    kept only the sentence, so the button dispatched with an EMPTY arg —
    which means the WHOLE FLEET rather than the one goal it pointed at. A
    button that does something broader than its label is the same defect
    Casper's verb fall-through was."""
    real = swarm.open_work
    import go
    real_run = go.run
    go.run = lambda n: {"data": {"would": ["goal: my-goal -> ship the thing"],
                                 "cooling": 0}}
    try:
        rows = [r for r in swarm.open_work() if r.get("kind") == "goal"]
    finally:
        go.run = real_run
        swarm.open_work = real
    assert rows and rows[0]["name"] == "my-goal", rows
    # what is the THING, why is the reason — never both in both
    assert rows[0]["what"] == "my-goal", rows[0]
    assert "ship the thing" in rows[0]["why"] and "->" not in rows[0]["what"]


def test_the_console_labels_a_button_with_what_it_will_DO():
    """'run it' on a goal with no name would start the whole fleet. The label
    has to name the target or the click is a surprise."""
    html = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "twin_console.html"), errors="ignore").read()
    assert "dispatch " in html and "start the fleet" in html, \
        "the plan's buttons no longer say what they target"
    assert 'data-act="${esc(v)}" data-arg="${esc(arg)}"' in html



def _console():
    return open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "twin_console.html"), errors="ignore").read()


def test_a_click_shows_its_state_where_the_finger_IS():
    """A dispatch takes seconds. The old click fired and said nothing until
    the answer landed, so the page looked broken for the whole wait. The
    button holds its own state: label -> working -> the first line of what
    happened -> label. Proven live: 'open flight-postman' became 'opening a
    window on flight-p' and restored."""
    html = _console()
    assert 'btn.textContent = "working…"' in html
    assert "btn.disabled = true" in html
    assert 'act(b.dataset.act, b.dataset.arg, b)' in html, \
        "the click no longer hands the button to act()"


def test_a_REFUSAL_is_shown_as_a_refusal():
    """/api/act answers started:false when it declines — a nonexistent
    project, for one. Printing its message alone reads like success."""
    assert 'd.started === false' in _console()


def test_completion_offers_only_targets_that_EXIST():
    """A palette listing a verb with no target is how `go ` with an empty
    argument launched the whole fleet. The list is built from the goals the
    dispatcher named and the repos it offered, never hand-typed."""
    html = _console()
    assert 'list="targets"' in html and '<datalist id="targets">' in html
    assert 'if(a.kind === "goal" && a.name) opts.add("go " + a.name)' in html
    assert 'opts.add("revive " + a.what)' in html



def test_an_alias_resolves_to_what_it_REALLY_RAN():
    """Substring matching picked opus-5 because it had the most turns, while
    `--model opus` actually runs claude-opus-4-8 — so every projection used
    the wrong model's rates. A dispatch that actually happened outranks a
    name match."""
    import models
    real = models.spend
    models.spend = lambda ledger=None: {"rows": [
        {"alias": "opus", "model": "claude-opus-4-8", "cost_usd": 1.0}] * 3,
        "runs": 3, "total_usd": 3.0, "per_model": []}
    try:
        got = swarm._match(RATES, "opus")
    finally:
        models.spend = real
    assert got == "claude-opus-4-8", got


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
