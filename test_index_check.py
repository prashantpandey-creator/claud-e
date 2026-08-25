"""Tests for repair.stale_index_lines — the guard on the lane that matters.

WHY THIS EXISTS (measured 2026-08-25, on the live store):

The graded store gates ONE delivery lane: coordination.facts_for(), which
serves at most 2 machine_checked facts per edit. It served 3 facts in 24
hours.

The OTHER lane is MEMORY.md — 5,042 tokens loaded into EVERY session by
Claude Code's own harness. Nothing in the hook loads it, and nothing anywhere
writes back from the graded store to the .md files it points at. So a memory
nidra demotes keeps being read into every session, verbatim, forever, and the
only thing that ever corrected it was a person remembering to run /meditate.

The funnel that made this obvious: 638 stored -> 563 active -> 561
machine_checked -> 71 reachable by the serving index (12.6%). 492 active
memories could not reach a session through the graded lane at all. ~25,600
lines of machinery were guarding the small door.

This closes it. Today it flags ZERO lines — 2 non-green memories, neither in
MEMORY.md, 0 flagged drifted. That is exactly why it is cheap to add now and
exactly why nobody would notice the day it starts mattering.

Run: python3 ~/.claude/skills/meditate/test_index_check.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import repair  # noqa: E402


def _store(tmp, mems):
    d = os.path.join(tmp, "store")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "memories.jsonl"), "w") as f:
        for m in mems:
            f.write(json.dumps(m) + "\n")
    return d


def _mem(source, status="machine_checked", active=True, flags=None):
    return {
        "id": "mem_" + os.path.basename(source),
        "statement": "a fact recorded in " + os.path.basename(source),
        "active": active,
        "flags": flags or [],
        "epistemic": {"evidence_status": status},
        "evidence": [{"source": source, "excerpt": "x", "locator": "path:" + source}],
    }


def _memdir(tmp, index_body, files):
    d = os.path.join(tmp, "memory")
    os.makedirs(d, exist_ok=True)
    for name in files:
        open(os.path.join(d, name), "w").write("# " + name + "\n")
    open(os.path.join(d, "MEMORY.md"), "w").write(index_body)
    return d


# ---------------------------------------------------------------------------

def test_green_index_flags_nothing():
    """The state of the real store today. Must be silent, or it is noise."""
    with tempfile.TemporaryDirectory() as tmp:
        md = _memdir(tmp, "# Memory Index\n- [A](a.md) — hook\n- [B](b.md) — hook\n",
                     ["a.md", "b.md"])
        st = _store(tmp, [_mem(os.path.join(md, "a.md")),
                          _mem(os.path.join(md, "b.md"))])
        assert repair.stale_index_lines(md, st) == []


def test_demoted_memory_is_flagged():
    """The whole point: MEMORY.md still points at a memory that lost its grade."""
    with tempfile.TemporaryDirectory() as tmp:
        md = _memdir(tmp, "# Memory Index\n- [A](a.md) — hook\n- [B](b.md) — hook\n",
                     ["a.md", "b.md"])
        st = _store(tmp, [_mem(os.path.join(md, "a.md")),
                          _mem(os.path.join(md, "b.md"), status="unverified")])
        out = repair.stale_index_lines(md, st)
        assert len(out) == 1, out
        assert out[0]["target"] == "b.md"
        assert out[0]["reason"] == "unverified"
        assert out[0]["line"] == 3, "must report the line number to fix"


def test_drift_flag_is_flagged_even_when_grade_recovered():
    """A memory can be machine_checked AND carry a live 'drifted' flag."""
    with tempfile.TemporaryDirectory() as tmp:
        md = _memdir(tmp, "- [A](a.md) — hook\n", ["a.md"])
        st = _store(tmp, [_mem(os.path.join(md, "a.md"), flags=["drifted"])])
        out = repair.stale_index_lines(md, st)
        assert len(out) == 1 and out[0]["reason"] == "drifted", out


def test_broken_pointer_is_flagged():
    """An index line pointing at a memory file that is not on disk."""
    with tempfile.TemporaryDirectory() as tmp:
        md = _memdir(tmp, "- [A](a.md) — hook\n- [Gone](gone.md) — hook\n", ["a.md"])
        st = _store(tmp, [_mem(os.path.join(md, "a.md"))])
        out = repair.stale_index_lines(md, st)
        assert len(out) == 1 and out[0]["reason"] == "missing_file", out


def test_ungraded_pointer_is_NOT_flagged():
    """FALSIFIER. A file the store has never graded is not evidence of rot.

    'No memory for this file' means the grader has not seen it — not that the
    fact is wrong. Flagging it would make the check say 'broken' when it means
    'I do not know', which is the single defect this whole project keeps
    finding in itself. If this ever starts flagging, the check is the bug.
    """
    with tempfile.TemporaryDirectory() as tmp:
        md = _memdir(tmp, "- [A](a.md) — hook\n- [New](new.md) — hook\n",
                     ["a.md", "new.md"])
        st = _store(tmp, [_mem(os.path.join(md, "a.md"))])
        assert repair.stale_index_lines(md, st) == []


def test_inactive_memory_does_not_flag():
    """Superseded memories are history, not rot."""
    with tempfile.TemporaryDirectory() as tmp:
        md = _memdir(tmp, "- [A](a.md) — hook\n", ["a.md"])
        st = _store(tmp, [_mem(os.path.join(md, "a.md"), status="unverified", active=False)])
        assert repair.stale_index_lines(md, st) == []


def test_worst_status_wins_when_several_memories_share_a_file():
    """One green and one demoted memory in the same file -> flag it."""
    with tempfile.TemporaryDirectory() as tmp:
        md = _memdir(tmp, "- [A](a.md) — hook\n", ["a.md"])
        good = _mem(os.path.join(md, "a.md"))
        bad = _mem(os.path.join(md, "a.md"), status="unverified")
        bad["id"] = "mem_second"
        assert len(repair.stale_index_lines(md, _store(tmp, [good, bad]))) == 1


def test_non_link_lines_and_external_links_are_ignored():
    """Prose, headings and http links are not memory pointers."""
    with tempfile.TemporaryDirectory() as tmp:
        body = ("# Memory Index\n> a note\n**Standing rules**\n"
                "- see [docs](https://example.com/x.md) — external\n"
                "- [A](a.md) — hook\n")
        md = _memdir(tmp, body, ["a.md"])
        st = _store(tmp, [_mem(os.path.join(md, "a.md"))])
        assert repair.stale_index_lines(md, st) == []


def test_missing_index_returns_empty_not_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        d = os.path.join(tmp, "nothing-here")
        os.makedirs(d)
        assert repair.stale_index_lines(d, _store(tmp, [])) == []


def test_unreadable_store_returns_empty_not_a_false_alarm():
    """Cannot read the grades -> say nothing. Never 'everything is stale'."""
    with tempfile.TemporaryDirectory() as tmp:
        md = _memdir(tmp, "- [A](a.md) — hook\n", ["a.md"])
        assert repair.stale_index_lines(md, os.path.join(tmp, "no-store")) == []


def test_default_reaches_a_REAL_index_not_an_empty_parent():
    """The default must check the per-project dirs, not their parent.

    First cut defaulted to paths.memory_root() = ~/claude-sync/memory, which
    is the PARENT of the per-cwd memory dirs and holds no MEMORY.md itself.
    The live smoke test SKIPPED, which is how the wrong default got caught:
    a check that silently examines nothing reports clean forever.
    """
    from nidra_bridge import _memory_dirs
    dirs = _memory_dirs()
    if not dirs:
        print("       (skipped: no memory dirs on this machine)")
        return
    with_index = [d for d in dirs if os.path.exists(os.path.join(d, "MEMORY.md"))]
    assert with_index, "the default resolver found %d dirs but no MEMORY.md in any" % len(dirs)
    out = repair.stale_index_lines()
    assert isinstance(out, list)
    print("       live: %d dir(s), %d with an index, %d stale line(s)"
          % (len(dirs), len(with_index), len(out)))


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
