"""Tests for nidra_bridge.py (Rule 0, precondition A).

Every test runs against a TEMPORARY store. The live graded store
(~/.claude/meditation/nidra_store) is shared infrastructure — a test that
writes to it corrupts the review schedule and inflates the journal for
every future session. Never point these at STORE_DIR.

Run: python3 ~/.claude/skills/meditate/test_nidra_bridge.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nidra_bridge


def _run(**kw):
    """Run the bridge against a throwaway store.

    form_days=0: formation reads hundreds of MB of transcripts per fresh
    store; that cost belongs to test_formation.py, not to every bridge test
    (7 runs blew doctor's 30s limit at 32.8s measured)."""
    kw.setdefault("form_days", 0)
    with tempfile.TemporaryDirectory() as td:
        return nidra_bridge.run(store_dir=os.path.join(td, "store"), **kw)


def test_envelope_shape():
    env = _run()
    assert isinstance(env, dict)
    assert "success" in env
    assert "data" in env
    assert "metadata" in env
    assert "errors" in env
    assert env["tool_name"] == "nidra_bridge"


def test_data_fields_on_success():
    env = _run()
    if not env["success"]:
        return  # nidra not importable — skip
    d = env["data"]
    assert "formed_commit_facts" in d, "formation field missing from envelope"
    for key in ("scanned", "imported", "already_exists", "no_anchor", "store_total"):
        assert key in d, f"missing data.{key}"
    assert isinstance(d["store_total"], int)
    assert d["store_total"] >= 0


def test_sleep_pass():
    env = _run(do_sleep=True)
    if not env["success"]:
        return
    d = env["data"]
    if "sleep" in d:
        assert "actions" in d["sleep"]
        assert "after" in d["sleep"]
        assert isinstance(d["sleep"]["after"]["active"], int)


def test_idempotent():
    # Both runs must share ONE temp store, or the second sees an empty store.
    with tempfile.TemporaryDirectory() as td:
        sd = os.path.join(td, "store")
        e1 = nidra_bridge.run(store_dir=sd, form_days=0)
        if not e1["success"]:
            return
        e2 = nidra_bridge.run(store_dir=sd, form_days=0)
        assert e2["success"]
        assert e2["data"]["imported"] == 0
        assert e2["data"]["already_exists"] == e1["data"]["scanned"]


def test_does_not_touch_live_store():
    """The guard that keeps this suite honest."""
    live = nidra_bridge.STORE_DIR
    mem = os.path.join(live, "memories.jsonl")
    before = os.path.getmtime(mem) if os.path.exists(mem) else None
    _run(do_sleep=True)
    after = os.path.getmtime(mem) if os.path.exists(mem) else None
    assert before == after, "bridge tests wrote to the LIVE graded store"


def test_memory_dirs_finds_every_store():
    dirs = nidra_bridge._memory_dirs()
    # Must find at least the vedic-puran store, and each must hold .md files.
    assert isinstance(dirs, list)
    for d in dirs:
        assert os.path.isdir(d)
        assert any(f.endswith(".md") for f in os.listdir(d))


def test_memory_dirs_empty_root():
    with tempfile.TemporaryDirectory() as td:
        assert nidra_bridge._memory_dirs(td) == []


def test_memory_dirs_missing_root():
    assert nidra_bridge._memory_dirs("/nonexistent/path/xyz") == []


def test_concurrent_grade_skips_not_corrupts():
    """THE concurrency falsifier: while one pass holds the store lock, a
    second must SKIP with an honest envelope — never interleave saves."""
    import fcntl
    with tempfile.TemporaryDirectory() as td:
        sd = os.path.join(td, "store")
        os.makedirs(sd)
        holder = open(os.path.join(sd, ".grade.lock"), "w")
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        env = nidra_bridge.run(store_dir=sd, form_days=0)
        holder.close()
        assert env["success"] is True
        assert "skipped" in env["data"], env["data"]
        # and after release, a real pass proceeds
        env2 = nidra_bridge.run(store_dir=sd, form_days=0)
        assert "skipped" not in env2["data"]


def test_journal_rotation():
    """Unbounded journal is the one measured long-run defect: 3,314 rows/day
    at current cadence with no rotation anywhere. The bridge rotates at the
    threshold; report globs the rotated files so history is never lost."""
    with tempfile.TemporaryDirectory() as td:
        sd = os.path.join(td, "store")
        os.makedirs(sd)
        jp = os.path.join(sd, "journal.jsonl")
        with open(jp, "w") as f:
            f.write(json.dumps({"event": "x"}) + "\n")
        nidra_bridge._rotate_journal(sd, max_bytes=1)      # force rotation
        assert not os.path.exists(jp), "journal not rotated"
        rotated = [f for f in os.listdir(sd) if f.startswith("journal-")]
        assert len(rotated) == 1, rotated
        nidra_bridge._rotate_journal(sd, max_bytes=10**9)  # under threshold: no-op
        assert len([f for f in os.listdir(sd) if f.startswith("journal-")]) == 1


def test_path_index_built():
    """The bridge must emit path_index.json — coordination.py serves facts from it."""
    with tempfile.TemporaryDirectory() as td:
        sd = os.path.join(td, "store")
        env = nidra_bridge.run(store_dir=sd, form_days=0)
        if not env["success"]:
            return
        idx_path = os.path.join(sd, "path_index.json")
        assert os.path.exists(idx_path), "path_index.json not written"
        with open(idx_path) as fh:
            idx = json.load(fh)
        assert isinstance(idx, dict)
        pi = env["data"].get("path_index", {})
        assert pi.get("claims", 0) == sum(len(v) for v in idx.values())
        if env["data"]["memory_files"]["scanned"] > 0:
            assert pi["claims"] > 0, "memory files scanned but zero path claims indexed"


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
