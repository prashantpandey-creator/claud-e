"""Tests for beacon.py — fleet agents report progress back (Rule 0, A)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)
import beacon as bc


def test_report_then_latest_per_goal():
    with tempfile.TemporaryDirectory() as t:
        p = os.path.join(t, "b.jsonl")
        bc.report("mila-live", "reading rejection", beacon_path=p)
        bc.report("mila-live", "drafting reply", beacon_path=p)
        bc.report("astro", "pushing backend", beacon_path=p)
        lat = bc.latest(beacon_path=p)
        assert lat["mila-live"]["message"] == "drafting reply", "latest per goal"
        assert lat["astro"]["message"] == "pushing backend"
        assert lat["mila-live"]["done"] is False


def test_done_flag():
    with tempfile.TemporaryDirectory() as t:
        p = os.path.join(t, "b.jsonl")
        bc.report("g", "working", beacon_path=p)
        bc.report("g", "milestone ticked", done=True, beacon_path=p)
        assert bc.latest(beacon_path=p)["g"]["done"] is True


def test_empty_and_missing():
    with tempfile.TemporaryDirectory() as t:
        assert bc.latest(beacon_path=os.path.join(t, "nope.jsonl")) == {}


def test_malformed_line_ignored():
    with tempfile.TemporaryDirectory() as t:
        p = os.path.join(t, "b.jsonl")
        with open(p, "w") as f:
            f.write("not json\n")
            f.write(json.dumps({"goal": "g", "message": "ok"}) + "\n")
        assert bc.latest(beacon_path=p)["g"]["message"] == "ok"


def test_cli_envelope():
    with tempfile.TemporaryDirectory() as t:
        env = dict(os.environ, MEDITATE_BEACON=os.path.join(t, "b.jsonl"))
        r = subprocess.run([sys.executable, os.path.join(SKILL, "beacon.py"),
                            "mygoal", "doing", "the", "thing", "--json"],
                           capture_output=True, text=True, timeout=15, env=env)
        assert r.returncode == 0
        d = json.loads(r.stdout)
        assert d["success"] and d["data"]["message"] == "doing the thing"


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
