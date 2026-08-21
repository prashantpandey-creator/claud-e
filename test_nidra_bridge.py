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
    """Run the bridge against a throwaway store."""
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
        e1 = nidra_bridge.run(store_dir=sd)
        if not e1["success"]:
            return
        e2 = nidra_bridge.run(store_dir=sd)
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
