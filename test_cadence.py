"""Tests for cadence.py — the interval must be derived, not guessed."""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)
import cadence as cd


def test_busy_world_checks_often():
    """High churn -> short interval, floored so it can never thrash."""
    r = cd.recommend({"window_h": 72, "memory_edits": 700,
                      "transcript_edits": 0, "total_edits": 700,
                      "per_hour": 700 / 72})
    assert r["hours"] == cd.MIN_H, r
    assert "floored" in r["why"]


def test_quiet_world_checks_rarely():
    r = cd.recommend({"window_h": 72, "memory_edits": 1, "transcript_edits": 0,
                      "total_edits": 1, "per_hour": 1 / 72})
    assert r["hours"] == cd.MAX_H, r


def test_dead_world_capped_not_infinite():
    r = cd.recommend({"window_h": 72, "memory_edits": 0, "transcript_edits": 0,
                      "total_edits": 0, "per_hour": 0.0})
    assert r["hours"] == cd.MAX_H and r["seconds"] == cd.MAX_H * 3600


def test_interval_tracks_target_changes():
    """The rule is literally TARGET_CHANGES / churn — verify the arithmetic."""
    rate = 1.0                      # 1 edit/hour
    r = cd.recommend({"window_h": 72, "memory_edits": 72, "transcript_edits": 0,
                      "total_edits": 72, "per_hour": rate})
    assert r["hours"] == cd.TARGET_CHANGES, r


def test_staleness_equals_interval():
    """The honest cost must be stated and equal to the interval."""
    r = cd.recommend({"window_h": 72, "memory_edits": 36, "transcript_edits": 0,
                      "total_edits": 36, "per_hour": 0.5})
    assert r["worst_case_staleness_h"] == r["hours"]


def test_churn_measures_real_dirs():
    c = cd.churn(window_h=72)
    assert c["total_edits"] == c["memory_edits"] + c["transcript_edits"]
    assert c["per_hour"] >= 0


def test_cron_interval_parsing():
    assert cd._cron_interval_h("0 */6 * * * true # meditate-heartbeat") == 6
    assert cd._cron_interval_h("0 * * * * true # meditate-heartbeat") == 1
    assert cd._cron_interval_h("0 3 * * * true # meditate-heartbeat") is None


def test_cron_fallback_current_interval_and_apply():
    """No launchd plist (Linux) must not mean cadence goes silently inert —
    current_interval_s and apply have to read and rewrite the crontab line."""
    fake_bin = tempfile.mkdtemp()
    fake_home = tempfile.mkdtemp()  # no plist under here
    store = os.path.join(fake_bin, "cron_store")
    with open(store, "w") as f:
        f.write("0 */6 * * * true # meditate-heartbeat\n")
    crontab_path = os.path.join(fake_bin, "crontab")
    # Supports both invocation forms cadence.py uses: `-l` to read, and a
    # file argument to write (crontab.apply avoids `-`/stdin — see its
    # docstring for why: piping into it was observed live to silently
    # install an empty table on a real Linux CI runner).
    with open(crontab_path, "w") as f:
        f.write("#!/bin/bash\n"
                "STORE=\"%s\"\n"
                "if [ \"$1\" = \"-l\" ]; then\n"
                "  [ -f \"$STORE\" ] && cat \"$STORE\"\n"
                "  exit 0\n"
                "fi\n"
                "cp \"$1\" \"$STORE\"\n" % store)
    os.chmod(crontab_path, 0o755)

    old_home, old_path = os.environ.get("HOME"), os.environ.get("PATH", "")
    try:
        os.environ["HOME"] = fake_home
        os.environ["PATH"] = fake_bin + os.pathsep + old_path
        importlib.reload(cd)  # PLIST is a module constant derived from HOME
        assert cd.current_interval_s() == 6 * 3600
        r = cd.apply(3 * 3600)
        assert r["applied"] is True and r["mechanism"] == "cron", r
        assert cd.current_interval_s() == 3 * 3600
    finally:
        if old_home is not None:
            os.environ["HOME"] = old_home
        os.environ["PATH"] = old_path
        importlib.reload(cd)


def test_cli_envelope_does_not_apply_by_default():
    before = cd.current_interval_s()
    r = subprocess.run([sys.executable, os.path.join(SKILL, "cadence.py"), "--json"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    env = json.loads(r.stdout)
    assert "apply" not in env["data"], "bare run must never change the schedule"
    assert cd.current_interval_s() == before, "plist was modified without --apply"


def _main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
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
