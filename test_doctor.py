"""Tests for doctor.py (Rule 0, precondition A).

Run: python3 ~/.claude/skills/meditate/test_doctor.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import doctor


def test_envelope_shape():
    env = doctor.run()
    assert isinstance(env, dict), "envelope must be dict"
    assert "success" in env, "missing success"
    assert "data" in env, "missing data"
    assert "metadata" in env, "missing metadata"
    assert "errors" in env, "missing errors"
    assert env["success"] is True, "doctor.run should always succeed (it reports issues, not fails)"


def test_data_fields():
    d = doctor.run()["data"]
    for key in ("version", "healthy", "issues", "prereqs", "tests", "hook", "stillness", "output"):
        assert key in d, f"missing data.{key}"
    assert isinstance(d["version"], str), "version must be string"
    assert isinstance(d["healthy"], bool), "healthy must be bool"
    assert isinstance(d["issues"], list), "issues must be list"


def test_prereqs_include_python_and_claude():
    d = doctor.run()["data"]
    names = [p["name"] for p in d["prereqs"]]
    assert "python3" in names, "must check python3"
    assert "claude_code" in names, "must check claude_code"
    python_check = [p for p in d["prereqs"] if p["name"] == "python3"][0]
    assert python_check["ok"] is True, "we are running on python3"


def test_tests_field_covers_all_test_files():
    d = doctor.run()["data"]
    files = [t["file"] for t in d["tests"]["files"]]
    for expected in doctor.TEST_FILES:
        assert expected in files, f"test suite missing {expected}"


def test_stillness_fields():
    d = doctor.run()["data"]
    s = d["stillness"]
    assert "exists" in s, "missing exists"
    assert "overdue" in s, "missing overdue"
    if s["exists"]:
        assert "age_days" in s, "if exists, age_days must be present"
        assert isinstance(s["age_days"], (int, float)), "age_days must be numeric"


def test_json_serializable():
    env = doctor.run()
    serialized = json.dumps(env)
    assert len(serialized) > 100, "envelope too small"


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
