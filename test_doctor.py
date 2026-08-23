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
    env = doctor.run(run_tests=False)
    assert isinstance(env, dict), "envelope must be dict"
    assert "success" in env, "missing success"
    assert "data" in env, "missing data"
    assert "metadata" in env, "missing metadata"
    assert "errors" in env, "missing errors"
    assert env["success"] is True, "doctor.run should always succeed (it reports issues, not fails)"


def test_data_fields():
    d = doctor.run(run_tests=False)["data"]
    for key in ("version", "healthy", "issues", "prereqs", "tests", "hook", "stillness", "output"):
        assert key in d, f"missing data.{key}"
    assert isinstance(d["version"], str), "version must be string"
    assert isinstance(d["healthy"], bool), "healthy must be bool"
    assert isinstance(d["issues"], list), "issues must be list"


def test_prereqs_include_python_and_claude():
    d = doctor.run(run_tests=False)["data"]
    names = [p["name"] for p in d["prereqs"]]
    assert "python3" in names, "must check python3"
    assert "claude_code" in names, "must check claude_code"
    python_check = [p for p in d["prereqs"] if p["name"] == "python3"][0]
    assert python_check["ok"] is True, "we are running on python3"


def test_stillness_fields():
    d = doctor.run(run_tests=False)["data"]
    s = d["stillness"]
    assert "exists" in s, "missing exists"
    assert "overdue" in s, "missing overdue"
    if s["exists"]:
        assert "age_days" in s, "if exists, age_days must be present"
        assert isinstance(s["age_days"], (int, float)), "age_days must be numeric"


def test_json_serializable():
    env = doctor.run(run_tests=False)
    serialized = json.dumps(env)
    assert len(serialized) > 100, "envelope too small"


def test_heartbeat_detects_cron_fallback():
    """On a machine with no launchd plist (Linux), a crontab line tagged
    meditate-heartbeat must still count as a loaded heartbeat — else a
    correctly installed Linux box is permanently reported unhealthy."""
    fake_home = tempfile.mkdtemp()
    fake_bin = tempfile.mkdtemp()
    crontab_path = os.path.join(fake_bin, "crontab")
    with open(crontab_path, "w") as f:
        f.write("#!/bin/bash\n"
                "if [ \"$1\" = \"-l\" ]; then\n"
                "  echo '0 */6 * * * true # meditate-heartbeat'\n"
                "  exit 0\n"
                "fi\n"
                "cat > /dev/null\n")
    os.chmod(crontab_path, 0o755)

    old_home, old_path = os.environ.get("HOME"), os.environ.get("PATH", "")
    try:
        os.environ["HOME"] = fake_home
        os.environ["PATH"] = fake_bin + os.pathsep + old_path
        hb = doctor._check_heartbeat()
    finally:
        if old_home is not None:
            os.environ["HOME"] = old_home
        os.environ["PATH"] = old_path

    assert hb["plist_exists"] is False, hb
    assert hb["loaded"] is True, hb
    assert hb["mechanism"] == "cron", hb


def test_heartbeat_absent_when_neither_launchd_nor_cron():
    """Falsifier for the check above: a fresh machine with no scheduler at
    all must report not-loaded, not crash and not false-positive."""
    fake_home = tempfile.mkdtemp()
    fake_bin = tempfile.mkdtemp()  # empty: no crontab binary reachable

    old_home, old_path = os.environ.get("HOME"), os.environ.get("PATH", "")
    try:
        os.environ["HOME"] = fake_home
        os.environ["PATH"] = fake_bin
        hb = doctor._check_heartbeat()
    finally:
        if old_home is not None:
            os.environ["HOME"] = old_home
        os.environ["PATH"] = old_path

    assert hb["loaded"] is False, hb
    assert hb["mechanism"] is None, hb


def test_discovers_every_suite_without_running_them():
    """Cheap proof that discovery is real: every test_*.py on disk is in the
    list (minus self-referential ones). Actually EXECUTING them is what
    `meditate doctor` does — duplicating that here just doubled the cost."""
    import os as _os
    on_disk = {f for f in _os.listdir(doctor.SKILL_DIR)
               if f.startswith("test_") and f.endswith(".py")}
    listed = set(doctor.TEST_FILES)
    missing = on_disk - listed - doctor.SELF_REFERENTIAL
    assert not missing, "suites invisible to doctor: %s" % sorted(missing)
    assert len(listed) >= 20, listed


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
