"""Tests for nidra_bridge.py (Rule 0, precondition A).

Run: python3 ~/.claude/skills/meditate/test_nidra_bridge.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nidra_bridge


def test_envelope_shape():
    env = nidra_bridge.run()
    assert isinstance(env, dict)
    assert "success" in env
    assert "data" in env
    assert "metadata" in env
    assert "errors" in env
    assert env["tool_name"] == "nidra_bridge"


def test_data_fields_on_success():
    env = nidra_bridge.run()
    if not env["success"]:
        return  # nidra not importable — skip
    d = env["data"]
    for key in ("scanned", "imported", "already_exists", "no_anchor", "store_total"):
        assert key in d, f"missing data.{key}"
    assert isinstance(d["store_total"], int)
    assert d["store_total"] >= 0


def test_sleep_pass():
    env = nidra_bridge.run(do_sleep=True)
    if not env["success"]:
        return
    d = env["data"]
    if "sleep" in d:
        assert "actions" in d["sleep"]
        assert "after" in d["sleep"]
        assert isinstance(d["sleep"]["after"]["active"], int)


def test_idempotent():
    e1 = nidra_bridge.run()
    if not e1["success"]:
        return
    e2 = nidra_bridge.run()
    assert e2["success"]
    assert e2["data"]["imported"] == 0
    assert e2["data"]["already_exists"] == e1["data"]["scanned"]


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
