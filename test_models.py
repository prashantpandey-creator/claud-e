"""Tests for models — which model did which work.

WHY (measured 2026-08-30): every assistant turn carries `message.model` —
24,173 of 24,173 across 30 transcripts — and every tool call it issues gets a
result that is either fine or `is_error` (8,311 errored across 12 files). So
attribution and outcome are both in the record, unlabelled by anyone.

Two things this must never do. It must not attribute per SESSION: 9 of 40
sessions used more than one model, one of them five, so "this session was
Sonnet" is false on a quarter of the record. And it must not print a quality
score: an error share is confounded by task difficulty, which is recorded
nowhere, so the caveat travels with the number instead of under it.

Run: python3 ~/.claude/skills/meditate/test_models.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import models  # noqa: E402


def _fixture(rows):
    d = tempfile.mkdtemp()
    proj = os.path.join(d, "proj")
    os.makedirs(proj)
    with open(os.path.join(proj, "aaaaaaaa1111.jsonl"), "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return d


def _asst(model, tools=0, out=100):
    return {"type": "assistant", "timestamp": "2026-08-30T10:00:00Z",
            "message": {"model": model, "usage": {"output_tokens": out},
                        "content": [{"type": "tool_use", "name": "Bash"}] * tools}}


def _result(err):
    return {"type": "user", "timestamp": "2026-08-30T10:00:01Z",
            "message": {"content": [{"type": "tool_result", "is_error": err,
                                     "content": "x"}]}}


def test_a_tool_error_is_charged_to_the_model_that_ISSUED_it():
    """The result arrives in a LATER row than the call, so the join is
    'whoever was driving'. Charging it to the wrong model would invert the
    only outcome signal in the file."""
    old = models.PROJECTS
    models.PROJECTS = _fixture([_asst("m-a", tools=1), _result(True),
                                _asst("m-b", tools=1), _result(False)])
    try:
        rows = {r["model"]: r for r in models.scan()["models"]}
    finally:
        models.PROJECTS = old
    assert rows["m-a"]["tool_errors"] == 1, rows["m-a"]
    assert rows["m-b"]["tool_errors"] == 0, rows["m-b"]


def test_attribution_is_per_TURN_not_per_session():
    """9 of 40 real sessions used more than one model. A per-session label is
    false on a quarter of the record."""
    old = models.PROJECTS
    models.PROJECTS = _fixture([_asst("m-a")] * 3 + [_asst("m-b")] * 7)
    try:
        d = models.scan()
    finally:
        models.PROJECTS = old
    rows = {r["model"]: r for r in d["models"]}
    assert rows["m-a"]["turns"] == 3 and rows["m-b"]["turns"] == 7
    assert d["sessions"][0]["mixed"] is True
    assert d["sessions"][0]["primary"] == "m-b"


def test_the_synthetic_placeholder_is_not_counted_as_a_model():
    """`<synthetic>` is Claude Code's own marker on generated rows. Counting
    it invents a model that never ran anything."""
    old = models.PROJECTS
    models.PROJECTS = _fixture([_asst("<synthetic>"), _asst("m-a")])
    try:
        names = [r["model"] for r in models.scan()["models"]]
    finally:
        models.PROJECTS = old
    assert "<synthetic>" not in names, names


def test_a_model_with_NO_tool_calls_reports_no_share_not_zero():
    """Zero calls means the question was never asked. A 0.0% error share
    reads as flawless — an absence rendered as a present value."""
    old = models.PROJECTS
    models.PROJECTS = _fixture([_asst("m-quiet", tools=0)])
    try:
        r = models.scan()["models"][0]
    finally:
        models.PROJECTS = old
    assert r["error_share"] is None, r


def test_the_CAVEAT_travels_with_the_number():
    """An error share is confounded by task difficulty, which is recorded
    nowhere. The caveat is part of the output, not a footnote someone drops
    when they quote the table."""
    old = models.PROJECTS
    models.PROJECTS = _fixture([_asst("m-a", tools=2), _result(True)])
    try:
        text = models.render()
    finally:
        models.PROJECTS = old
    low = text.lower()
    assert "not a quality score" in low
    assert "difficulty" in low
    assert "per turn" in low


def test_the_live_record_attributes_every_turn():
    d = models.scan(limit=8)
    assert d["models"], "no models found in the real transcripts"
    for r in d["models"]:
        assert r["model"] not in models._NOT_A_MODEL
        assert r["turns"] > 0
    print("       live: " + ", ".join("%s %d turns" % (r["model"][:18], r["turns"])
                                      for r in d["models"][:4]))



def test_effort_and_thinking_are_BOTH_captured():
    """They live in different places and neither had reached a report: `effort`
    is a ROW field (22,052 of 22,346 turns), thinking_tokens is nested in
    usage.output_tokens_details (22,091). Reading only the message misses both.
    """
    old = models.PROJECTS
    rows = [{"type": "assistant", "timestamp": "2026-08-30T10:00:00Z",
             "effort": "max",
             "message": {"model": "m-a",
                         "usage": {"output_tokens": 100,
                                   "output_tokens_details": {"thinking_tokens": 40}},
                         "content": []}},
            {"type": "assistant", "timestamp": "2026-08-30T10:01:00Z",
             "effort": "low",
             "message": {"model": "m-a",
                         "usage": {"output_tokens": 100,
                                   "output_tokens_details": {"thinking_tokens": 0}},
                         "content": []}}]
    models.PROJECTS = _fixture(rows)
    try:
        r = models.scan()["models"][0]
    finally:
        models.PROJECTS = old
    assert r["think_tokens"] == 40 and r["think_per_turn"] == 20, r
    assert r["effort"] == {"max": 1, "low": 1}, r["effort"]
    assert "max 50%" in r["effort_mix"] and "low 50%" in r["effort_mix"], r["effort_mix"]


def test_effort_mix_reads_HARDEST_first():
    """'max 97% xhigh 3%' and 'low 90% high 10%' must be distinguishable at a
    glance; alphabetical order would put low before max and invert it."""
    old = models.PROJECTS
    rows = [{"type": "assistant", "timestamp": "2026-08-30T10:00:00Z", "effort": e,
             "message": {"model": "m", "usage": {}, "content": []}}
            for e in ["low", "max", "xhigh", "high", "medium"]]
    models.PROJECTS = _fixture(rows)
    try:
        mix = models.scan()["models"][0]["effort_mix"]
    finally:
        models.PROJECTS = old
    order = [w for w in mix.split() if not w.endswith("%")]
    assert order == ["max", "xhigh", "high", "medium", "low"], mix


def test_a_turn_with_NO_effort_recorded_is_not_called_zero():
    """294 real turns carry no effort field. Reporting them as an effort level
    would invent one; the mix says '—'."""
    old = models.PROJECTS
    models.PROJECTS = _fixture([{"type": "assistant", "timestamp": "2026-08-30T10:00:00Z",
                                 "message": {"model": "m", "usage": {}, "content": []}}])
    try:
        r = models.scan()["models"][0]
    finally:
        models.PROJECTS = old
    assert r["top_effort"] is None and r["effort_mix"] == "—", r


def test_the_twin_carries_the_model_section():
    import twin
    titles = [s["title"] for s in twin.build()]
    assert any(t.startswith("WHO DID THE WORK") for t in titles), titles
    sec = [s for s in twin.build() if s["title"].startswith("WHO DID THE WORK")][0]
    assert sec["lines"], "the section is empty on the live record"
    assert any("thinking tokens" in l for l in sec["lines"])
    assert any("NOT a quality score" in l for l in sec["lines"]),         "the caveat did not travel into the twin"



# ---------------------------------------------------------------------------
# choosing who to dispatch — measured 2026-08-30 before any of it existed:
# 0 of 6 goal files named a model, so every dispatch fell through to a
# hardcoded `--model sonnet`; `effort` appeared 0 times in the whole dispatch
# path. The fleet was not choosing badly, it was not choosing at all.
# ---------------------------------------------------------------------------

def test_a_choice_always_states_its_BASIS():
    """A default that says it is a default can be argued with; a hardcoded one
    cannot. Every pick carries model, effort and why."""
    for kind in ("repair", "goal", "revive", "thread", "something-new"):
        p = models.pick(kind)
        assert p["model"] and p["effort"], p
        assert p["basis"] in ("evidence", "default"), p
        assert len(p["why"]) > 20, p


def test_it_does_NOT_rank_models_by_the_confounded_error_share():
    """The one thing this must never do. Error share is confounded by task
    difficulty, which is recorded nowhere — picking with it would be the exact
    defect the caveat in this module exists to prevent. With no per-kind rows,
    the basis must be `default`, never `evidence`."""
    p = models.pick("goal")
    ev = models.evidence_for("goal")
    if not ev["enough"]:
        assert p["basis"] == "default", \
            "ranked models from the global error share: %r" % p


def test_evidence_only_wins_with_ENOUGH_like_for_like_rows():
    """Per-kind is the only comparison that is not confounded — repair tasks
    resemble each other. But three rows are not a finding."""
    ev = models.evidence_for("repair")
    assert ev["enough"] is (ev["rows"] >= 10), ev


def test_a_model_named_on_the_GOAL_always_wins():
    """That is the owner deciding, and no policy outranks him."""
    import go
    c = go.choose("goal", "opus-4-8")
    assert c["model"] == "opus-4-8" and c["basis"] == "goal file", c


def test_the_dispatch_LEDGER_records_the_choice():
    """Without model, effort and basis on the row, the ledger can never answer
    'did this choice work' — so the default could never become evidence and
    the policy would stay a guess forever."""
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "go.py")).read()
    for field in ('"kind": "goal"', '"model": pickd', '"effort": pickd', '"basis": pickd'):
        assert field in src, "dispatch rows lost %s" % field


def test_effort_actually_reaches_the_command():
    """`effort` appeared 0 times in the dispatch path — every agent ever
    dispatched ran at the CLI default."""
    import inspect, go
    assert "effort" in inspect.signature(go.dispatch_one).parameters
    assert "effort" in inspect.signature(go._headless).parameters
    src = inspect.getsource(go._headless)
    assert '"--effort"' in src, "effort is accepted and then dropped"



def test_a_plain_human_message_CLOSES_the_burst():
    """The bug this pins: the fast-path line filter kept only rows containing
    "model" or "tool_result", and a plain human message carries neither — so
    no burst ever closed and leash read None for all six models while the
    prototype had measured 5 to 17."""
    old = models.PROJECTS
    rows = [_asst("m-a"), _asst("m-a"), _asst("m-a"),
            {"type": "user", "message": {"content": "go"}},
            _asst("m-a"),
            {"type": "user", "message": {"content": "ok"}}]
    models.PROJECTS = _fixture(rows)
    try:
        r = models.scan()["models"][0]
    finally:
        models.PROJECTS = old
    assert r["bursts"] == 2, r["bursts"]
    assert r["leash"] in (1, 3), r["leash"]


def test_leash_is_a_MEDIAN_not_a_mean():
    """One 200-turn night would drag a mean into fiction for every other
    burst."""
    old = models.PROJECTS
    rows = []
    for n in (1, 1, 1, 200):
        rows += [_asst("m-a")] * n + [{"type": "user", "message": {"content": "go"}}]
    models.PROJECTS = _fixture(rows)
    try:
        r = models.scan()["models"][0]
    finally:
        models.PROJECTS = old
    assert r["leash"] == 1, "a single long night set the leash: %r" % r["leash"]


def test_the_KIND_of_work_is_split_by_tool():
    """A model at 19% make is producing code; one at 93% run is executing
    someone else's plan. That difference was invisible in a turn count."""
    old = models.PROJECTS
    def t(name):
        return {"type": "assistant", "timestamp": "2026-08-30T10:00:00Z",
                "message": {"model": "m", "usage": {},
                            "content": [{"type": "tool_use", "name": name}]}}
    models.PROJECTS = _fixture([t("Edit"), t("Read"), t("Bash"), t("Bash")])
    try:
        r = models.scan()["models"][0]
    finally:
        models.PROJECTS = old
    assert r["make_share"] == 0.25 and r["look_share"] == 0.25
    assert r["run_share"] == 0.5, r


def test_ZERO_interruptions_is_reported_as_none_not_as_perfect():
    """No interruption recorded is not the same as flawless — the signal is
    real but rare (18 across 12 transcripts), and a blank must not read as a
    score."""
    old = models.PROJECTS
    models.PROJECTS = _fixture([_asst("m-a")])
    try:
        text = models.render()
    finally:
        models.PROJECTS = old
    assert "none recorded is not the same as flawless" in text
    assert "leash is NOT" in text or "confounded by" in text


def test_the_leash_caveat_reaches_the_TWIN():
    import twin
    sec = [s for s in twin.build() if s["title"].startswith("WHO DID THE WORK")][0]
    joined = " ".join(sec["lines"])
    assert "leash is NOT a rating" in joined, joined[-160:]



# ---------------------------------------------------------------------------
# what a dispatch REALLY costs — measured 2026-08-30 by running three
# ---------------------------------------------------------------------------

def test_a_running_agent_is_PENDING_not_free():
    """A log with no result line has not finished. Reading it as zero spend
    would make every in-flight agent look free — 51 old logs predate JSON
    output and must count as pending, not as $0."""
    import tempfile, json as _j
    d = tempfile.mkdtemp()
    open(os.path.join(d, "a.log"), "w").write("# running\nno result yet\n")
    r = models.reconcile(log_dir=d, ledger=os.path.join(d, "spend.jsonl"))
    assert r["added"] == 0 and r["pending"] == 1, r


def test_the_result_line_is_found_from_the_END():
    """It is not always last: a chatty or crashed run appends after it."""
    import tempfile, json as _j
    d = tempfile.mkdtemp()
    res = _j.dumps({"type": "result", "total_cost_usd": 0.5, "num_turns": 3,
                    "is_error": False, "usage": {"output_tokens": 100}})
    open(os.path.join(d, "b.log"), "w").write(
        "# revive-x\n# model: sonnet effort: high\n\n" + res + "\ntrailing noise\n")
    r = models.reconcile(log_dir=d, ledger=os.path.join(d, "spend.jsonl"))
    assert r["added"] == 1 and r["rows"][0]["cost_usd"] == 0.5, r
    assert r["rows"][0]["model"] == "sonnet", r["rows"][0]


def test_reconcile_cannot_DOUBLE_COUNT():
    """Keyed by the log's own name, so a second pass adds nothing."""
    import tempfile, json as _j
    d = tempfile.mkdtemp()
    led = os.path.join(d, "spend.jsonl")
    res = _j.dumps({"type": "result", "total_cost_usd": 1.0, "num_turns": 1,
                    "is_error": False, "usage": {}})
    open(os.path.join(d, "c.log"), "w").write("# n\n\n" + res + "\n")
    assert models.reconcile(log_dir=d, ledger=led)["added"] == 1
    assert models.reconcile(log_dir=d, ledger=led)["added"] == 0


def test_the_budget_cap_comes_from_LIKE_FOR_LIKE_runs():
    """Difficulty poisons cross-kind comparison; revive runs resemble each
    other. Under three measured runs it must say `default`, never invent an
    evidence-shaped number."""
    b = models.budget_for("kind-that-never-ran")
    assert b["basis"] == "default" and b["usd"] > 0, b
    live = models.budget_for("revive")
    if live["runs"] >= 3:
        assert live["basis"] == "evidence" and live["median"], live


def test_the_cap_is_a_STOP_SIGNAL_not_a_hard_ceiling():
    """Proven live: $0.0242 spent against a $0.001 cap, because one API call
    already costs more than that. The docstring must not promise a ceiling it
    cannot hold."""
    import inspect
    src = inspect.getsource(models.budget_for)
    assert "NOT A HARD CEILING" in src.upper()
    assert "0.0242" in src, "the measured overshoot is not recorded"



def test_a_ZERO_percent_winner_is_never_called_evidence():
    """The lie this pins, shipped and caught live 2026-08-30.

    pick("goal") returned basis=evidence with the reason "on goal tasks opus
    produced work 0 of 18 times (0%)" — recommending a model BECAUSE it had a
    0% success rate. Sorting descending makes the only model in the data
    "best" even when it produced nothing. Zero is an absence, not a finding.
    """
    real = models.spend
    models.spend = lambda ledger=None: {"rows": [
        {"name": "goal-x", "model": "opus", "ok": False, "cost_usd": 0}
        for _ in range(18)], "runs": 18, "total_usd": 0, "per_model": []}
    try:
        ev = models.evidence_for("goal")
        p = models.pick("goal")
    finally:
        models.spend = real
    assert ev["by_model"]["opus"]["produced"] == 0, ev
    assert p["basis"] == "default", \
        "a 0%% producer was recommended as evidence: %r" % p


def test_outcomes_come_from_a_field_something_actually_WRITES():
    """The dispatch ledger's `produced` was designed and never populated, so
    every rate computed over it was an empty column with a percent sign. The
    spend ledger's `ok` is written by reconcile from the agent's own result
    line."""
    import inspect
    src = inspect.getsource(models.evidence_for)
    assert "spend()" in src, "outcomes read from the unwritten field again"
    assert "dispatch.jsonl" not in src


def test_a_real_producer_DOES_win_on_evidence():
    """FALSIFIER for the zero guard: it must not mute a genuine result."""
    real = models.spend
    models.spend = lambda ledger=None: {"rows": [
        {"name": "goal-y", "model": "sonnet", "ok": True, "cost_usd": 0.5}
        for _ in range(6)], "runs": 6, "total_usd": 3.0, "per_model": []}
    try:
        p = models.pick("goal")
    finally:
        models.spend = real
    assert p["basis"] == "evidence" and p["model"] == "sonnet", p
    assert "6 of 6" in p["why"], p["why"]



def test_the_double_count_guard_holds_ACROSS_PROCESSES():
    """Proven live 2026-08-30, by accident: the brain server reconciles on
    every /api/spend read, so the console picked up a finished repair agent
    before the CLI did. The CLI's own reconcile then correctly reported
    `added 0` while the row was already in the ledger — two reconcilers, one
    row, no duplicate. The guard is the log NAME, which both processes see.
    """
    import tempfile, json as _j
    d = tempfile.mkdtemp()
    led = os.path.join(d, "spend.jsonl")
    res = _j.dumps({"type": "result", "total_cost_usd": 0.13, "num_turns": 2,
                    "is_error": False, "usage": {"output_tokens": 40}})
    open(os.path.join(d, "r.log"), "w").write("# repair-x\n\n" + res + "\n")
    first = models.reconcile(log_dir=d, ledger=led)
    second = models.reconcile(log_dir=d, ledger=led)      # the other process
    assert first["added"] == 1 and second["added"] == 0, (first, second)
    rows = [ln for ln in open(led)]
    assert len(rows) == 1, "the same run was billed twice"



def test_whole_session_usage_is_recorded_not_the_LAST_TURN():
    """The impossible number that exposed this: a run showed 700 output
    tokens and $1.41 while another showed 7,520 tokens and $1.30 — less of
    every token, more money. `usage` is the LAST TURN; `modelUsage` is the
    whole session, and total_cost_usd is computed from the latter. The real
    figures were 18,500 output and 771,225 cache-read."""
    import tempfile, json as _j
    d = tempfile.mkdtemp()
    res = _j.dumps({"type": "result", "total_cost_usd": 1.4, "num_turns": 2,
                    "is_error": False,
                    "usage": {"output_tokens": 700,
                              "cache_read_input_tokens": 186065},
                    "modelUsage": {"claude-opus-4-8": {
                        "outputTokens": 18500, "cacheReadInputTokens": 771225,
                        "cacheCreationInputTokens": 50424, "costUSD": 1.4}}})
    open(os.path.join(d, "g.log"), "w").write(
        "# goal-x\n# model: opus effort: max\n\n" + res + "\n")
    r = models.reconcile(log_dir=d, ledger=os.path.join(d, "s.jsonl"))
    row = r["rows"][0]
    assert row["out_tokens"] == 18500, row
    assert row["cache_read"] == 771225, row


def test_the_REAL_model_is_recorded_not_the_alias():
    """`--model opus` ran claude-opus-4-8 on this machine. Filing spend under
    "opus" files it under a name no model has, and made the plan project with
    opus-5's rates."""
    import tempfile, json as _j
    d = tempfile.mkdtemp()
    res = _j.dumps({"type": "result", "total_cost_usd": 1.0, "num_turns": 1,
                    "is_error": False, "usage": {},
                    "modelUsage": {"claude-opus-4-8": {"costUSD": 1.0,
                                                       "outputTokens": 10}}})
    open(os.path.join(d, "h.log"), "w").write(
        "# goal-y\n# model: opus effort: max\n\n" + res + "\n")
    row = models.reconcile(log_dir=d, ledger=os.path.join(d, "s.jsonl"))["rows"][0]
    assert row["model"] == "claude-opus-4-8", row
    assert row["alias"] == "opus", row


def _kind_ledger():
    """A ledger with two kinds: one with enough runs for evidence, one without."""
    import tempfile, json as _j
    d = tempfile.mkdtemp()
    p = os.path.join(d, "spend.jsonl")
    rows = [{"name": "goal-a", "cost_usd": 1.00, "out_tokens": 1000,
             "cache_read": 40_000, "turns": 2, "model": "claude-opus-4-8"},
            {"name": "goal-b", "cost_usd": 2.00, "out_tokens": 1000,
             "cache_read": 40_000, "turns": 2, "model": "claude-opus-4-8"},
            {"name": "goal-c", "cost_usd": 3.00, "out_tokens": 2000,
             "cache_read": 80_000, "turns": 4, "model": "claude-opus-4-8"},
            {"name": "repair-a", "cost_usd": 0.10, "out_tokens": 100,
             "cache_read": 50_000, "turns": 1, "model": "claude-sonnet-5"}]
    open(p, "w").write("".join(_j.dumps(r) + "\n" for r in rows))
    return p


def test_spend_rolls_up_PER_KIND_not_only_per_model():
    """Per-model spend answers nothing — difficulty, not the model, sets the
    bill. The kind is the grouping the cap is actually derived from, so it has
    to survive into what the dashboard reads."""
    d = models.spend(ledger=_kind_ledger())
    kinds = {k["kind"]: k for k in d["per_kind"]}
    assert set(kinds) == {"goal", "repair"}, kinds
    g = kinds["goal"]
    assert g["runs"] == 3 and g["usd"] == 6.0, g
    assert g["avg"] == 2.0 and g["lo"] == 1.0 and g["hi"] == 3.0, g
    # cache-read against output is THE cost ratio: 160k read / 4k out = 40x
    assert g["read_per_out"] == 40.0, g
    assert g["out_per_turn"] == 500, g


def test_per_kind_carries_the_caps_BASIS_not_just_a_number():
    """A $2 cap from three measured runs and a $2 cap from nothing look
    identical on screen. Every kind row must say which one it is."""
    d = models.spend(ledger=_kind_ledger())
    kinds = {k["kind"]: k for k in d["per_kind"]}
    # 3 goal runs, median $2.00, x2.0 headroom
    assert kinds["goal"]["cap_basis"] == "evidence", kinds["goal"]
    assert kinds["goal"]["cap_usd"] == 4.00, kinds["goal"]
    # 1 repair run is not enough to claim evidence
    assert kinds["repair"]["cap_basis"] == "default", kinds["repair"]


def test_a_kind_with_ZERO_output_does_not_divide_by_zero():
    """A run killed before it emitted anything has out_tokens 0. The ratio is
    then absent, and absent must render as absent — not as 0x, which reads as
    'this run was free'."""
    import tempfile, json as _j
    d = tempfile.mkdtemp()
    p = os.path.join(d, "s.jsonl")
    open(p, "w").write(_j.dumps({"name": "revive-x", "cost_usd": 0.4,
                                 "out_tokens": 0, "cache_read": 9_000,
                                 "turns": 0, "model": "m"}) + "\n")
    row = models.spend(ledger=p)["per_kind"][0]
    assert row["read_per_out"] is None, row
    assert row["out_per_turn"] is None, row


def test_spend_and_budget_for_are_not_each_others_BASE_CASE():
    """budget_for() used to call spend(), and spend() now calls budget_for()
    for every kind — which recursed until the stack blew on the first real
    ledger. spend() passes its own rows down; the guard is that this returns
    at all."""
    import inspect
    src = inspect.getsource(models.budget_for)
    assert "rows" in inspect.signature(models.budget_for).parameters
    assert "spend()" in src, "the fallback path must still exist standalone"
    # the falsifier: a ledger with rows, which is what actually recursed
    d = models.spend(ledger=_kind_ledger())
    assert d["per_kind"], d
    # and standalone (rows=None) still reads the ledger for itself
    assert models.budget_for("anything")["basis"] == "default"


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
