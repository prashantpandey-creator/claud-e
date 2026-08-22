"""Tests for distillation — messages that COMMUNICATE, not read out fields.

The owner's point: "mila: 25% done. Next is X" is a field readout. Effective
communication says the thing that MATTERS about the data — what is stuck,
what changed, what it costs, how it compares. One sentence a person can act
on without reading a table.

Distillation rules under test:
  - a stalled project leads with HOW LONG, not with the percentage
  - a widening goal says scope grew (the % dropping is not failure)
  - knowledge rot is expressed as consequence, not a count
  - the lead is comparative when comparison is the insight
  - never a bare field dump
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)
import distill_speech as ds


def _p(**kw):
    base = {"project": "mila", "sessions": 3, "messages": 96,
            "last_touched_days": 1.0, "facts": 30, "repair_items": 0,
            "goals": 1, "milestones_done": 1, "milestones_total": 4,
            "pct": 25.0, "open_tasks": [{"goal": "mila-live",
                                          "task": "resubmit to Apple",
                                          "pct": 25.0}]}
    base.update(kw)
    return base


def test_stalled_leads_with_duration_not_percent():
    """15 days untouched is the story; 25% is a footnote."""
    s = ds.distill_project(_p(last_touched_days=15.0))
    # duration must LEAD in human units ("2 weeks" beats "15 days" — speech,
    # not a table); the percentage must not be the opening.
    assert "hasn't moved" in s and ("week" in s or "day" in s), s
    assert not s.strip().startswith("mila: 25%") and "25%" not in s, s


def test_active_project_leads_with_the_task_not_the_number():
    s = ds.distill_project(_p(last_touched_days=0.2))
    assert "resubmit to Apple" in s, s
    assert s.count("%") <= 1, "one number at most — this is speech, not a table"


def test_widening_scope_is_explained_not_hidden():
    s = ds.distill_project(_p(scope_delta=3, pct=20.0))
    assert "grew" in s.lower() or "wider" in s.lower() or "added" in s.lower(), s


def test_knowledge_rot_stated_as_consequence():
    s = ds.distill_project(_p(repair_items=12))
    assert "12" in s
    assert ("trust" in s.lower() or "stale" in s.lower()
            or "no longer" in s.lower() or "check" in s.lower()), s


def test_portfolio_lead_is_comparative():
    """Across projects, the insight is the imbalance, not per-project stats."""
    rows = [_p(project="purangpt", messages=2372, last_touched_days=0.1,
               repair_items=23),
            _p(project="mila", messages=96, last_touched_days=15.0)]
    s = ds.distill_portfolio(rows)
    assert "%" in s or "most" in s.lower(), s
    assert "purangpt" in s.lower(), s
    # names the neglected one too — that IS the communication
    assert "mila" in s.lower(), s


def test_all_clear_is_short_and_human():
    rows = [_p(project="x", repair_items=0, last_touched_days=0.5,
               open_tasks=[], milestones_done=4, milestones_total=4, pct=100.0)]
    s = ds.distill_portfolio(rows)
    assert len(s) < 160, s


def test_never_empty():
    assert ds.distill_project(_p()).strip()
    assert ds.distill_portfolio([]).strip()


def _main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
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
