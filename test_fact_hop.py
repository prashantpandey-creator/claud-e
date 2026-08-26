"""Tests for facts_for's one-hop wikilink expansion.

WHY (measured on the live store 2026-08-25):

The serving path was exact-path-match only, so of 576 active memories just 71
(12%) could ever reach a session through it. Expanding one wikilink hop from
those 71 reaches 192 more — 46%, a 2.7x. A second hop adds 24 and then dies,
which is why this stops at one.

The graph was already there and already walked: projects.attribute_all() does
exactly this hop for project ATTRIBUTION and places 63 facts that no other
signal could place. Its own docstring says why it was written — "923 wikilinks
sit in this store and nobody followed one." This reuses that motion in the one
place that never adopted it. Nothing new is stored and nothing is scheduled.

The design rule is your own, from PuranGPT: **graph SURFACES, evidence
GROUNDS.** The link is a routing hint and never a warrant — every memory
served must be independently machine_checked, exactly as it is today. Measured
on the live store: 192 of 192 one-hop neighbours already are, so this costs no
trust. Counting a wikilink as evidence is what once reported 56% of the store
as world-decidable when the honest figure was 13%; that mistake is not being
repeated, only routed around.

Cost: the hop loads memories.jsonl (7ms) ONLY when there is a seed to hop from
AND room under FACT_CAP. An edit to an unindexed path — the common case —
pays nothing.

Run: python3 ~/.claude/skills/meditate/test_fact_hop.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coordination  # noqa: E402


def _store(tmp, mems, index):
    d = os.path.join(tmp, "store")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "memories.jsonl"), "w") as f:
        for m in mems:
            f.write(json.dumps(m) + "\n")
    with open(os.path.join(d, "path_index.json"), "w") as f:
        json.dump(index, f)
    return d


def _mem(mid, statement, source_md, status="machine_checked", links=()):
    ev = [{"source": source_md, "excerpt": "x", "locator": "path:/some/file.py"}]
    for t in links:
        ev.append({"source": source_md, "excerpt": "y",
                   "locator": "wikilink:[[%s]]" % t})
    return {"id": mid, "statement": statement, "active": True,
            "epistemic": {"evidence_status": status}, "evidence": ev}


MD = "/mem/alpha.md"
MD2 = "/mem/beta.md"
MD3 = "/mem/gamma.md"


def _two_linked(tmp, neighbour_status="machine_checked"):
    """alpha (indexed under /work/a.py) --wikilink--> beta (not indexed)."""
    a = _mem("m_a", "alpha fact about a.py", MD, links=["beta"])
    b = _mem("m_b", "beta fact reached by link", MD2, status=neighbour_status)
    idx = {"/work/a.py": [{"id": "m_a", "statement": a["statement"],
                           "status": "machine_checked"}]}
    return _store(tmp, [a, b], idx)


# ---------------------------------------------------------------------------

def test_the_hop_reaches_a_linked_fact():
    """The whole point: a fact not indexed under this path, reached by link."""
    with tempfile.TemporaryDirectory() as tmp:
        got = coordination.facts_for("/work/a.py", [], _two_linked(tmp))
        stmts = [s for _, s, _, _ in got]
        assert "alpha fact about a.py" in stmts, stmts
        assert "beta fact reached by link" in stmts, "the hop did not fire"


def test_an_UNGRADED_neighbour_is_NEVER_served():
    """THE falsifier. The link routes; it never vouches.

    Serving an unverified memory because a verified one pointed at it is
    exactly the 56%-vs-13% mistake in a new costume.
    """
    for bad in ("unverified", "source_linked"):
        with tempfile.TemporaryDirectory() as tmp:
            got = coordination.facts_for("/work/a.py", [], _two_linked(tmp, bad))
            stmts = [s for _, s, _, _ in got]
            assert "beta fact reached by link" not in stmts, \
                "a %s neighbour was served" % bad
            assert "alpha fact about a.py" in stmts, "the direct fact was lost too"


def test_exact_path_facts_come_FIRST():
    """A hopped fact must never displace or outrank a direct one."""
    with tempfile.TemporaryDirectory() as tmp:
        got = coordination.facts_for("/work/a.py", [], _two_linked(tmp))
        assert got[0][1] == "alpha fact about a.py", got


def test_the_cap_still_holds():
    with tempfile.TemporaryDirectory() as tmp:
        a = _mem("m_a", "direct one", MD, links=["beta", "gamma"])
        b = _mem("m_b", "hop one", MD2)
        c = _mem("m_c", "hop two", MD3)
        idx = {"/work/a.py": [{"id": "m_a", "statement": a["statement"],
                               "status": "machine_checked"}]}
        got = coordination.facts_for("/work/a.py", [], _store(tmp, [a, b, c], idx))
        assert len(got) <= coordination.FACT_CAP, got


def test_no_hop_when_the_cap_is_already_full():
    """Cost discipline: two direct hits means the store is never loaded."""
    with tempfile.TemporaryDirectory() as tmp:
        a = _mem("m_a", "direct one", MD, links=["beta"])
        a2 = _mem("m_a2", "direct two", MD, links=["beta"])
        b = _mem("m_b", "hop one", MD2)
        idx = {"/work/a.py": [
            {"id": "m_a", "statement": a["statement"], "status": "machine_checked"},
            {"id": "m_a2", "statement": a2["statement"], "status": "machine_checked"}]}
        got = coordination.facts_for("/work/a.py", [], _store(tmp, [a, a2, b], idx))
        assert [s for _, s, _, _ in got] == ["direct one", "direct two"], got


def test_no_seed_means_no_hop():
    """An unindexed path has nothing to hop FROM. Must stay empty and cheap —
    this is the common case on every edit."""
    with tempfile.TemporaryDirectory() as tmp:
        assert coordination.facts_for("/work/never-indexed.py", [], _two_linked(tmp)) == []


def test_already_served_facts_are_not_repeated_via_the_hop():
    with tempfile.TemporaryDirectory() as tmp:
        d = _two_linked(tmp)
        first = coordination.facts_for("/work/a.py", [], d)
        served = [k for k, _, _, _ in first]
        again = coordination.facts_for("/work/a.py", served, d)
        assert again == [], again


def test_a_memory_does_not_hop_to_itself():
    with tempfile.TemporaryDirectory() as tmp:
        a = _mem("m_a", "alpha fact", MD, links=["alpha"])   # links to own file
        idx = {"/work/a.py": [{"id": "m_a", "statement": a["statement"],
                               "status": "machine_checked"}]}
        got = coordination.facts_for("/work/a.py", [], _store(tmp, [a], idx))
        assert len(got) == 1, got


def test_an_unresolvable_link_is_skipped_quietly():
    """A wikilink whose target no memory owns. Must not raise, must not serve."""
    with tempfile.TemporaryDirectory() as tmp:
        a = _mem("m_a", "alpha fact", MD, links=["nobody-owns-this"])
        idx = {"/work/a.py": [{"id": "m_a", "statement": a["statement"],
                               "status": "machine_checked"}]}
        got = coordination.facts_for("/work/a.py", [], _store(tmp, [a], idx))
        assert [s for _, s, _, _ in got] == ["alpha fact"], got


def test_an_inactive_neighbour_is_not_served():
    with tempfile.TemporaryDirectory() as tmp:
        a = _mem("m_a", "alpha fact", MD, links=["beta"])
        b = _mem("m_b", "beta fact", MD2)
        b["active"] = False
        idx = {"/work/a.py": [{"id": "m_a", "statement": a["statement"],
                               "status": "machine_checked"}]}
        got = coordination.facts_for("/work/a.py", [], _store(tmp, [a, b], idx))
        assert [s for _, s, _, _ in got] == ["alpha fact"], got


def test_direct_and_hopped_facts_are_DISTINGUISHABLE():
    """The caller must be able to label provenance honestly.

    A hopped fact is machine-checked and true, but it is NOT "about this
    file" — it is one link away. Announcing it as a fact about the file
    overclaims, and overclaiming is the failure mode this whole store exists
    to prevent. No relevance threshold gates the hop instead: lexical overlap
    was measured as a candidate gate and FAILED — median jaccard between a
    hopped statement and its seed is 0.019, yet hand-checking called 3 of 4
    of those genuinely relevant (android-signing -> android monetization
    scores near zero and is the right fact). A gate built on a proxy that
    disagrees with judgement would suppress the good hops.
    """
    with tempfile.TemporaryDirectory() as tmp:
        got = coordination.facts_for("/work/a.py", [], _two_linked(tmp))
        flags = {s: d for _, s, _, d in got}
        assert flags["alpha fact about a.py"] is True, "direct fact not marked direct"
        assert flags["beta fact reached by link"] is False, "hopped fact claimed as direct"


def test_a_hopped_fact_is_served_ONCE_PER_SESSION_not_per_file():
    """The hub-bias fix, and the reason there is no relevance threshold.

    Measured before this: 80 hopped facts came from 34 memories, and one —
    "User strongly prefers fully automated paths" — landed on 13 of 109 paths
    because the dedup key was path-scoped, so the same fact was "new" on every
    file. That is the hub pull the graph-memory literature warns about in PPR
    systems, arriving through a much dumber door.

    Repetition is a defect on its OWN terms, so it needs no relevance
    calibration to fix. Both ranking gates I measured were rejected: lexical
    overlap (median jaccard 0.019 while 3 of 4 hand-checked hops were
    relevant) and in-degree (good hop 48, bad hop 37 — no separation).
    """
    with tempfile.TemporaryDirectory() as tmp:
        a = _mem("m_a", "fact about a.py", MD, links=["beta"])
        c = _mem("m_c", "fact about c.py", MD3, links=["beta"])
        b = _mem("m_b", "the hub fact", MD2)
        idx = {"/work/a.py": [{"id": "m_a", "statement": a["statement"],
                               "status": "machine_checked"}],
               "/work/c.py": [{"id": "m_c", "statement": c["statement"],
                               "status": "machine_checked"}]}
        d = _store(tmp, [a, b, c], idx)
        session = []
        first = coordination.facts_for("/work/a.py", session, d)
        session += [k for k, _, _, _ in first]
        assert "the hub fact" in [s for _, s, _, _ in first], first

        second = coordination.facts_for("/work/c.py", session, d)
        assert "the hub fact" not in [s for _, s, _, _ in second], \
            "the same hopped fact was served again on a different file"
        assert "fact about c.py" in [s for _, s, _, _ in second], \
            "the direct fact for the second file was lost"


def test_a_DIRECT_fact_keeps_its_per_file_key():
    """The direct lane must NOT inherit session-scoping: a fact about file A
    and a fact about file B are different serves even with the same text."""
    with tempfile.TemporaryDirectory() as tmp:
        a = _mem("m_a", "same statement", MD)
        idx = {"/work/a.py": [{"id": "m_a", "statement": "same statement",
                               "status": "machine_checked"}],
               "/work/b.py": [{"id": "m_a", "statement": "same statement",
                               "status": "machine_checked"}]}
        d = _store(tmp, [a], idx)
        s1 = coordination.facts_for("/work/a.py", [], d)
        keys = [k for k, _, _, _ in s1]
        s2 = coordination.facts_for("/work/b.py", keys, d)
        assert [x[1] for x in s2] == ["same statement"], \
            "a direct fact about a different file was wrongly suppressed"


def test_a_missing_store_does_not_raise():
    assert coordination.facts_for("/work/a.py", [], "/nonexistent/store") == []


def test_hop_cost_is_paid_only_when_it_can_help():
    """Instrument the load: an unindexed path must not read memories.jsonl."""
    with tempfile.TemporaryDirectory() as tmp:
        d = _two_linked(tmp)
        calls = []
        real = coordination._load_memories
        coordination._load_memories = lambda sd: (calls.append(sd), real(sd))[1]
        try:
            coordination.facts_for("/work/never-indexed.py", [], d)
            assert calls == [], "the store was loaded with no seed to hop from"
            coordination.facts_for("/work/a.py", [], d)
            assert len(calls) == 1, "the hop did not load when it should have"
        finally:
            coordination._load_memories = real


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
