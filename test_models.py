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
