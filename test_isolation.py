"""The test suite never starts a real `claude`, never acts on the real fleet.

Measured 2026-11-13: one hand run of test_campaign.py (`python3
test_campaign.py`, no MEDITATE_TESTING) launched 22 real `claude -p` agents
against the owner's real repair queue and wrote its test goals into the real
goals-history.jsonl. The path: tick(meditation_dir=<temp>) -> the
fallthrough -> go.run, which only knows the REAL dir -> _headless. The only
guard, MEDITATE_TESTING, sat in go.autosplit, off that path, and a hand run
never sets it anyway.

So: every real spawn of the CLI asks go.under_test() first (true under
doctor's MEDITATE_TESTING AND for any process started from a test_*.py), and
a tick falls through only on the dir go.run actually acts on. Injected fakes
are untouched — every existing test that passes its own popen still runs.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)
import go  # noqa: E402


class _NoSpawn:
    """Stands in for subprocess.Popen: records, and refuses to run anything."""
    seen = []

    def __init__(self, *a, **kw):
        _NoSpawn.seen.append(a[0] if a else kw.get("args"))
        raise AssertionError("a real process was started: %r" % (a[0] if a else kw,))


def _plain_python(code, testing):
    env = {k: v for k, v in os.environ.items() if k != "MEDITATE_TESTING"}
    if testing:
        env["MEDITATE_TESTING"] = "1"
    r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r)\n%s" % (SKILL, code)],
                       env=env, capture_output=True, text=True, timeout=60)
    return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:]


def test_a_hand_run_test_file_knows_it_is_a_test():
    """The leak's exact shape: a test file run by hand, no env var."""
    saved = os.environ.pop("MEDITATE_TESTING", None)
    try:
        assert go.under_test(), "a process started from test_*.py was not seen as a test"
    finally:
        if saved is not None:
            os.environ["MEDITATE_TESTING"] = saved


def test_a_plain_process_is_not_a_test_and_doctor_s_flag_still_counts():
    """FALSIFIER: a guard that always says 'test' would switch the fleet off."""
    assert _plain_python("import go; print(go.under_test())", testing=False) == "False"
    assert _plain_python("import go; print(go.under_test())", testing=True) == "True"


def test_headless_never_starts_a_real_claude_in_a_test():
    real_popen, real_dir = subprocess.Popen, go.HEADLESS_LOG_DIR
    _NoSpawn.seen = []
    with tempfile.TemporaryDirectory() as d:
        subprocess.Popen = _NoSpawn
        go.HEADLESS_LOG_DIR = os.path.join(d, "agents")
        try:
            ok = go._headless(d, "prompt", "repair-queue")
        finally:
            subprocess.Popen, go.HEADLESS_LOG_DIR = real_popen, real_dir
        assert ok is False, "a launch was reported from inside a test"
        assert _NoSpawn.seen == [], "spawned: %r" % _NoSpawn.seen
        assert not os.path.exists(os.path.join(d, "agents")), "it still wrote an agent log"
        assert "test" in (go._headless.last or {}).get("why", ""), go._headless.last


def test_continue_agent_never_starts_a_real_claude_in_a_test():
    real_popen = subprocess.Popen
    _NoSpawn.seen = []
    subprocess.Popen = _NoSpawn
    try:
        r = go.continue_agent("repair-queue", "carry on")
    finally:
        subprocess.Popen = real_popen
    assert r.get("started") is False and "test" in r.get("why", ""), r
    assert _NoSpawn.seen == []


def test_a_tick_on_any_other_dir_never_reaches_the_real_fleet():
    import campaign as cp
    calls = []
    real_run, real_tick, real_dir = go.run, cp._tick, go.MEDITATION_DIR
    go.run = lambda **kw: calls.append(kw) or {"sent": ["repair-queue"]}
    cp._tick = lambda **kw: {"armed": True, "free": 2}
    try:
        with tempfile.TemporaryDirectory() as d:
            out = cp.tick(meditation_dir=d)
            assert calls == [] and out["fell_through"] == [], \
                "a tick on a scratch dir filled the REAL fleet's slots: %r" % calls
            # FALSIFIER: the fleet's own dir still falls through
            go.MEDITATION_DIR = d
            out = cp.tick(meditation_dir=d)
            assert [c.get("n") for c in calls] == [2] and out["fell_through"] == ["repair-queue"], \
                (calls, out)
            # and a fallthrough the caller hands in runs on any dir
            go.MEDITATION_DIR = real_dir
            got = []
            out = cp.tick(meditation_dir=d, fallthrough=lambda free: got.append(free) or {"sent": ["x"]})
            assert got == [2] and out["fell_through"] == ["x"], (got, out)
    finally:
        go.run, cp._tick, go.MEDITATION_DIR = real_run, real_tick, real_dir


def test_a_build_on_a_scratch_dir_keeps_its_goal_history_there():
    """goals.scan appends progress to a history file, by default the REAL
    goals-history.jsonl — test_campaign's goals ('g', 'shop-live') landed
    there on every run. build and replan now pass the file beside their dir.
    Two replans: the first builds (build's scan), the second re-reads
    (replan's own scan)."""
    import campaign as cp
    import goals as gl
    got = []
    real = gl.scan
    gl.scan = lambda *a, **kw: got.append(kw.get("history_path")) or []
    try:
        with tempfile.TemporaryDirectory() as d:
            for _ in range(2):
                cp.replan(goals_dir=d, meditation_dir=d, elaborator=lambda *a, **k: [])
            # predict / accept_predicted find a project's goal through this
            cp._goal_for_project("p", d, d, meditation_dir=d)
            want = os.path.join(d, "goals-history.jsonl")
            assert got == [want] * 3, "history went to %r, not beside %s" % (got, d)
    finally:
        gl.scan = real


def test_the_planners_never_start_a_real_claude_in_a_test():
    """ideate / predict / elaborate all take their binary from _claude()."""
    import campaign as cp
    assert os.path.basename(cp._claude()) != "claude", cp._claude()
    assert _plain_python("import campaign; print(campaign._claude())", testing=False) != "/usr/bin/false"


def test_the_advisor_never_starts_a_real_claude_in_a_test():
    import advisor
    assert os.path.basename(advisor._claude_exe()) != "claude", advisor._claude_exe()
    assert _plain_python("import advisor; print(advisor._claude_exe())", testing=False) == "claude"


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
