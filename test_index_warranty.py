"""Tests for repair.index_warranty — which lines of MEMORY.md are checkable.

WHY (measured 2026-08-25):

An agent's opening context is ~9,488 tokens. MEMORY.md is 5,118 of them —
54%. It is loaded into EVERY session by Claude Code's own harness, and not one
line of it carries anything an agent can re-check. The graded lane, which does
carry receipts, delivers ~90 tokens a day.

An agent cannot verify its own context: no budget, no tools, no reason to
doubt. So it acts on a line written six weeks ago with exactly the confidence
it gives a line verified this morning. That makes the cost of a context item
`tokens x P(false) x cost-of-acting`, not its token count — "capture.js fires
every session" is six tokens and nearly cost a whole session's diagnosis.

stale_index_lines() already answers "which lines are BROKEN" and flags zero
today. This answers the bigger question it cannot: "which lines are
CHECKABLE AT ALL". A line whose backing memories are all quote-scoped is not
broken and never will be — no change in the world can falsify it. That is not
health, it is unfalsifiability, and it is the difference between 56% and 13%
in this project's own history.

Nothing is written to MEMORY.md. Another session is restructuring it, and a
report that edits the thing it measures is how "he is quiet now" became a
false claim in this codebase.

Run: python3 ~/.claude/skills/meditate/test_index_warranty.py
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


def _mem(source, scope="world", status="machine_checked", active=True):
    return {"id": "mem_" + os.path.basename(source) + scope,
            "statement": "a fact in " + os.path.basename(source),
            "active": active, "flags": [],
            "epistemic": {"evidence_status": status, "evidence_scope": scope},
            "evidence": [{"source": source, "excerpt": "x", "locator": "path:/f.py"}]}


def _memdir(tmp, body, files):
    d = os.path.join(tmp, "memory")
    os.makedirs(d, exist_ok=True)
    for n in files:
        open(os.path.join(d, n), "w").write("# " + n + "\n")
    open(os.path.join(d, "MEMORY.md"), "w").write(body)
    return d


BODY = "# Index\n- [A](a.md) — hook\n- [B](b.md) — hook\n- [C](c.md) — hook\n"


# ---------------------------------------------------------------------------

def test_a_world_backed_line_is_warrantied():
    with tempfile.TemporaryDirectory() as tmp:
        md = _memdir(tmp, "- [A](a.md) — hook\n", ["a.md"])
        st = _store(tmp, [_mem(os.path.join(md, "a.md"), scope="world")])
        w = repair.index_warranty(md, st)
        assert w["world"] == 1 and w["lines"] == 1, w
        assert w["unwarrantied"] == 0


def test_a_QUOTE_backed_line_is_NOT_warrantied():
    """The whole point. A quote-scoped memory is machine_checked and will stay
    that way forever: its evidence is that a sentence exists in a file, which
    no change in the world can falsify. Green, permanent, and unfalsifiable."""
    with tempfile.TemporaryDirectory() as tmp:
        md = _memdir(tmp, "- [A](a.md) — hook\n", ["a.md"])
        st = _store(tmp, [_mem(os.path.join(md, "a.md"), scope="quote")])
        w = repair.index_warranty(md, st)
        assert w["world"] == 0, w
        assert w["unwarrantied"] == 1, "a quote-only line was counted as warrantied"


def test_an_INTERNAL_backed_line_is_NOT_warrantied():
    """A wikilink target proves the graph is self-consistent — exactly the
    property that holds while every statement is false."""
    with tempfile.TemporaryDirectory() as tmp:
        md = _memdir(tmp, "- [A](a.md) — hook\n", ["a.md"])
        st = _store(tmp, [_mem(os.path.join(md, "a.md"), scope="internal")])
        assert repair.index_warranty(md, st)["unwarrantied"] == 1


def test_a_line_with_NO_backing_memory_is_ungraded_not_broken():
    """'The grader has never seen this' is a THIRD value, distinct from both
    'checkable' and 'broken'. Collapsing it into either is the defect this
    whole project keeps finding in itself."""
    with tempfile.TemporaryDirectory() as tmp:
        md = _memdir(tmp, "- [A](a.md) — hook\n", ["a.md"])
        w = repair.index_warranty(md, _store(tmp, []))
        assert w["ungraded"] == 1, w
        assert w["world"] == 0 and w["broken"] == 0


def test_a_broken_line_is_counted_separately_from_ungraded():
    with tempfile.TemporaryDirectory() as tmp:
        md = _memdir(tmp, "- [A](a.md) — hook\n", ["a.md"])
        st = _store(tmp, [_mem(os.path.join(md, "a.md"),
                               scope="world", status="unverified")])
        w = repair.index_warranty(md, st)
        assert w["broken"] == 1 and w["ungraded"] == 0, w


def test_the_BEST_backing_memory_wins():
    """One world-checkable memory is enough to warranty the line — the others
    do not drag it down. Warranty asks 'can anything here be re-checked?'"""
    with tempfile.TemporaryDirectory() as tmp:
        md = _memdir(tmp, "- [A](a.md) — hook\n", ["a.md"])
        p = os.path.join(md, "a.md")
        st = _store(tmp, [_mem(p, scope="quote"), _mem(p, scope="world")])
        assert repair.index_warranty(md, st)["world"] == 1


def test_inactive_memories_do_not_warranty_a_line():
    with tempfile.TemporaryDirectory() as tmp:
        md = _memdir(tmp, "- [A](a.md) — hook\n", ["a.md"])
        st = _store(tmp, [_mem(os.path.join(md, "a.md"), scope="world", active=False)])
        assert repair.index_warranty(md, st)["world"] == 0


def test_external_links_and_prose_are_not_counted():
    with tempfile.TemporaryDirectory() as tmp:
        body = ("# Index\n> a note\n**Standing rules**\n"
                "- see [docs](https://example.com/x.md)\n- [A](a.md) — hook\n")
        md = _memdir(tmp, body, ["a.md"])
        st = _store(tmp, [_mem(os.path.join(md, "a.md"), scope="world")])
        assert repair.index_warranty(md, st)["lines"] == 1


def test_it_reports_WHICH_lines_so_the_number_is_actionable():
    with tempfile.TemporaryDirectory() as tmp:
        md = _memdir(tmp, BODY, ["a.md", "b.md", "c.md"])
        st = _store(tmp, [_mem(os.path.join(md, "a.md"), scope="world"),
                          _mem(os.path.join(md, "b.md"), scope="quote")])
        w = repair.index_warranty(md, st)
        by = {d["target"]: d["scope"] for d in w["detail"]}
        assert by["a.md"] == "world" and by["b.md"] == "quote"
        assert by["c.md"] is None, "an ungraded line must report None, not a guess"


def test_it_NEVER_writes_to_MEMORY_md():
    """Another session is restructuring this file. A report that edits what it
    measures is how a false claim gets manufactured."""
    with tempfile.TemporaryDirectory() as tmp:
        md = _memdir(tmp, BODY, ["a.md", "b.md", "c.md"])
        p = os.path.join(md, "MEMORY.md")
        before = open(p).read()
        repair.index_warranty(md, _store(tmp, []))
        assert open(p).read() == before, "index_warranty modified MEMORY.md"


def test_a_missing_index_is_empty_not_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        d = os.path.join(tmp, "nothing"); os.makedirs(d)
        assert repair.index_warranty(d, _store(tmp, []))["lines"] == 0


def test_an_unreadable_store_reports_ungraded_not_broken():
    """Cannot read the grades -> say 'I do not know', never 'it is broken'."""
    with tempfile.TemporaryDirectory() as tmp:
        md = _memdir(tmp, "- [A](a.md) — hook\n", ["a.md"])
        w = repair.index_warranty(md, os.path.join(tmp, "no-store"))
        assert w["ungraded"] == 1 and w["broken"] == 0, w


def test_live_index_reports_without_crashing():
    w = repair.index_warranty()
    print("       live: %d index lines — %d world, %d unwarrantied, %d ungraded, %d broken"
          % (w["lines"], w["world"], w["unwarrantied"], w["ungraded"], w["broken"]))
    assert isinstance(w["lines"], int)


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
