"""Tests for repair — fixing dead path claims without a model.

Contract:
  - a path with a `.retired` twin is repointed at the twin
  - a file that moved is repointed at where it actually is
  - a path with no findable successor is LEFT ALONE and reported
  - the old path is never left in the file as a path (that is the failing
    claim; writing it back in backticks would recreate it)
  - a live path is never touched
  - nothing is written unless apply=True

Run: python3 ~/.claude/skills/meditate/test_repair.py
"""
from __future__ import annotations

import os
import sys
import tempfile

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)

import repair as rp


def _md(tmp, body):
    p = os.path.join(tmp, "note.md")
    with open(p, "w") as f:
        f.write(body)
    return p


def test_retired_twin_is_the_successor():
    with tempfile.TemporaryDirectory() as t:
        gone = os.path.join(t, "hook.sh")
        open(gone + ".retired", "w").write("x")
        md = _md(t, "The defect lived in `%s` at line 45.\n" % gone)
        res = rp.repair_file(md, [gone], apply=True)
        assert res["fixed"], res
        after = open(md).read()
        assert gone + ".retired" in after, after
        assert res["fixed"][0]["rule"].startswith("retired"), res


def test_moved_file_is_found_where_it_actually_is():
    with tempfile.TemporaryDirectory() as t:
        newdir = os.path.join(t, "hooks"); os.makedirs(newdir)
        real = os.path.join(newdir, "thing.py")
        open(real, "w").write("x")
        gone = os.path.join(t, "thing.py")          # never existed here
        md = _md(t, "See `%s` for the guard.\n" % gone)
        res = rp.repair_file(md, [gone], apply=True, roots=[t])
        assert res["fixed"], res
        assert real in open(md).read()


def test_the_dead_path_is_not_written_back_as_a_path():
    """Leaving the old path in backticks recreates the exact claim that was
    failing — the repair would 'pass' once and fail again on the next grade."""
    with tempfile.TemporaryDirectory() as t:
        gone = os.path.join(t, "hook.sh")
        open(gone + ".retired", "w").write("x")
        md = _md(t, "It lived in `%s`.\n" % gone)
        rp.repair_file(md, [gone], apply=True)
        after = open(md).read()
        assert "`%s`" % gone not in after, after


def test_no_successor_means_hands_off():
    with tempfile.TemporaryDirectory() as t:
        gone = os.path.join(t, "vanished.sh")
        md = _md(t, "It lived in `%s`.\n" % gone)
        before = open(md).read()
        res = rp.repair_file(md, [gone], apply=True, roots=[t])
        assert not res["fixed"] and res["left"], res
        assert open(md).read() == before, "a file with no known successor was edited"


def test_a_live_path_is_never_touched():
    with tempfile.TemporaryDirectory() as t:
        alive = os.path.join(t, "here.sh")
        open(alive, "w").write("x")
        md = _md(t, "It lives in `%s`.\n" % alive)
        before = open(md).read()
        res = rp.repair_file(md, [alive], apply=True)
        assert not res["fixed"], res
        assert open(md).read() == before


def test_dry_run_writes_nothing():
    with tempfile.TemporaryDirectory() as t:
        gone = os.path.join(t, "hook.sh")
        open(gone + ".retired", "w").write("x")
        md = _md(t, "It lived in `%s`.\n" % gone)
        before = open(md).read()
        res = rp.repair_file(md, [gone], apply=False)
        assert res["fixed"], "a dry run must still SAY what it would do"
        assert open(md).read() == before, "dry run edited the file"


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
