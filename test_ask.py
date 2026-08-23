"""Tests for ask.py — query the graded store (Rule 0, precondition A).

Contract:
  - `meditate ask "query"` retrieves over ACTIVE memories, machine_checked
    ranked before unverified, each hit shown with its grade
  - unverified hits are marked as such, never presented as clean fact
  - repair queue: grade writes repair-queue.md when drift/contested exist,
    REMOVES it when clean; SessionStart nudges only while it exists
  - envelope always; exit 0 always

Run: python3 ~/.claude/skills/meditate/test_ask.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)
import ask as ak


def _store(t, mems):
    sd = os.path.join(t, "store")
    os.makedirs(sd, exist_ok=True)
    with open(os.path.join(sd, "memories.jsonl"), "w") as f:
        for m in mems:
            f.write(json.dumps(m) + "\n")
    return sd


def _mem(mid, statement, status="machine_checked", active=True):
    return {"id": mid, "active": active, "statement": statement,
            "epistemic": {"evidence_status": status}, "evidence": [], "flags": []}


def test_query_finds_and_ranks_verified_first():
    with tempfile.TemporaryDirectory() as t:
        sd = _store(t, [
            _mem("m1", "deploy uses docker compose on the hetzner box", "unverified"),
            _mem("m2", "deploy verified by duration and live output on hetzner", "machine_checked"),
            _mem("m3", "the mascot is an abstract form"),
        ])
        hits = ak.query("deploy hetzner", store_dir=sd, k=5)
        assert len(hits) == 2
        assert hits[0]["epistemic"]["evidence_status"] == "machine_checked", \
            "verified must outrank unverified at equal relevance"


def test_inactive_never_returned():
    with tempfile.TemporaryDirectory() as t:
        sd = _store(t, [_mem("m1", "tombstoned deploy fact", active=False)])
        assert ak.query("deploy", store_dir=sd) == []


def test_repair_queue_written_and_cleared():
    with tempfile.TemporaryDirectory() as t:
        med = os.path.join(t, "med")
        os.makedirs(med)
        drift = {"count": 1, "memories": [
            {"id": "mem_x", "statement": "stale claim", "status": "unverified",
             "flags": ["drifted"], "failing": [{"claim": "path:/gone", "line": "x"}]}]}
        p = ak.write_repair_queue(drift, meditation_dir=med)
        assert p and os.path.exists(p)
        body = open(p).read()
        assert "mem_x" in body and "path:/gone" in body
        # clean world -> queue file removed
        p2 = ak.write_repair_queue({"count": 0, "memories": []}, meditation_dir=med)
        assert p2 is None and not os.path.exists(p)


def test_cli_envelope():
    r = subprocess.run([sys.executable, os.path.join(SKILL, "ask.py"),
                        "deploy", "--json"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0
    env = json.loads(r.stdout)
    for k in ("success", "data", "metadata", "errors"):
        assert k in env


def test_knowledge_query_does_not_answer_with_a_CHAT_STUB():
    """A session record is not knowledge.

    Measured on the live store: 140 of 526 active memories (27%) are session
    stubs — "Session 'x' on y. 1 turns, 2 files, sprawl 0.5". They sat in the
    same index as curated knowledge, so asking Casper about Razorpay payments
    got that sentence read aloud as the answer. Metadata about a chat, spoken
    with the authority of a fact, is worse than no answer: it sounds like one.

    They stay IN the store (they answer "which session did X") — they are just
    not what a "what do we know about ..." question is asking for."""
    import tempfile, json as _j
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "memories.jsonl"), "w") as f:
            f.write(_j.dumps({"id": "s1", "active": True,
                "tags": ["meditate-session"],
                "statement": "Session 'Locate and verify Razorpay key' on x. 1 turns.",
                "epistemic": {"evidence_status": "machine_checked"}}) + "\n")
            f.write(_j.dumps({"id": "k1", "active": True,
                "tags": ["memory-file"],
                "statement": "Razorpay live keys are stored in the frontend secrets env.",
                "epistemic": {"evidence_status": "machine_checked"}}) + "\n")
        hits = ak.query("razorpay", store_dir=d, k=3)
        assert hits, "no hits at all"
        assert hits[0]["id"] == "k1", \
            "a chat stub outranked real knowledge: %s" % [h["id"] for h in hits]
        assert all(h["id"] != "s1" for h in hits), \
            "session stub reached a knowledge answer: %s" % [h["id"] for h in hits]


def test_sessions_are_still_findable_when_asked_for():
    """Filtering them out of knowledge must not delete them from the store."""
    import tempfile, json as _j
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "memories.jsonl"), "w") as f:
            f.write(_j.dumps({"id": "s1", "active": True,
                "tags": ["meditate-session"],
                "statement": "Session 'Razorpay key hunt' on x. 1 turns.",
                "epistemic": {"evidence_status": "machine_checked"}}) + "\n")
        hits = ak.query("razorpay", store_dir=d, k=3, include_sessions=True)
        assert hits and hits[0]["id"] == "s1", hits


def _main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERR  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
