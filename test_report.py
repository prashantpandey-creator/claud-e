"""Tests for report.py — the wins/efficacy loop (Rule 0, precondition A).

Contract:
  - drift: caught = downgrade events + drifted flags; repaired = a downgrade
    (or drifted state) later re-verified to machine_checked, with
    time-to-repair when both timestamps exist
  - stilling: archived count/bytes from ARCHIVE-INDEX.jsonl, continuation
    chats from the meditation sessions dir
  - sangama: fact_served / collision_warned counts from events.jsonl
  - honest zeros: an empty world reports zeros, never invented numbers
  - envelope always; exit 0 always

Run: python3 ~/.claude/skills/meditate/test_report.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)
import report as rp


def _world(t):
    store = os.path.join(t, "store"); os.makedirs(store)
    arch = os.path.join(t, "arch"); os.makedirs(arch)
    coord = os.path.join(t, "coord"); os.makedirs(coord)
    med = os.path.join(t, "med", "sessions"); os.makedirs(med)
    return store, arch, coord, os.path.join(t, "med")


def test_empty_world_reports_zeros():
    with tempfile.TemporaryDirectory() as t:
        store, arch, coord, med = _world(t)
        d = rp.compute(store_dir=store, archive_root=arch,
                       coord_root=coord, meditation_dir=med)
        assert d["drift"]["caught"] == 0
        assert d["drift"]["repaired"] == 0
        assert d["stilling"]["sessions_archived"] == 0
        assert d["sangama"]["facts_served"] == 0


def test_repair_pair_counted_with_time():
    with tempfile.TemporaryDirectory() as t:
        store, arch, coord, med = _world(t)
        rows = [
            {"event": "sleep.regraded", "id": "mem_a",
             "detail": "machine_checked -> unverified (excerpt no longer present in source)",
             "ts": "2026-08-19T10:00:00+00:00"},
            {"event": "sleep.regraded", "id": "mem_a",
             "detail": "unverified -> machine_checked (excerpt present in source)",
             "ts": "2026-08-20T10:00:00+00:00"},
        ]
        with open(os.path.join(store, "journal.jsonl"), "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        d = rp.compute(store_dir=store, archive_root=arch,
                       coord_root=coord, meditation_dir=med)
        assert d["drift"]["caught"] == 1
        assert d["drift"]["repaired"] == 1
        assert abs(d["drift"]["median_repair_hours"] - 24.0) < 0.1


def test_open_drift_counts_flagged_and_evidenceless():
    with tempfile.TemporaryDirectory() as t:
        store, arch, coord, med = _world(t)
        mems = [
            {"id": "m1", "active": True, "flags": ["drifted"],
             "epistemic": {"evidence_status": "unverified"},
             "evidence": [{"source": "/x"}]},
            {"id": "m2", "active": True, "flags": [],
             "epistemic": {"evidence_status": "unverified"}, "evidence": []},
            {"id": "m3", "active": True, "flags": [],
             "epistemic": {"evidence_status": "machine_checked"},
             "evidence": [{"source": "/y"}]},
        ]
        with open(os.path.join(store, "memories.jsonl"), "w") as f:
            for m in mems:
                f.write(json.dumps(m) + "\n")
        d = rp.compute(store_dir=store, archive_root=arch,
                       coord_root=coord, meditation_dir=med)
        assert d["drift"]["open_real"] == 1        # has evidence, failed
        assert d["drift"]["open_ungradeable"] == 1  # no evidence at all
        assert d["drift"]["caught"] == 1            # the drifted flag


def test_stilling_stats():
    with tempfile.TemporaryDirectory() as t:
        store, arch, coord, med = _world(t)
        with open(os.path.join(arch, "ARCHIVE-INDEX.jsonl"), "w") as f:
            f.write(json.dumps({"sid": "a", "bytes": 1000, "reason": "empty"}) + "\n")
            f.write(json.dumps({"sid": "b", "bytes": 2500, "reason": "empty"}) + "\n")
        sd = os.path.join(med, "sessions", "some-session")
        os.makedirs(sd)
        for fn in ("INDEX.md", "thread-1-x.md", "thread-2-y.md"):
            open(os.path.join(sd, fn), "w").write("x")
        d = rp.compute(store_dir=store, archive_root=arch,
                       coord_root=coord, meditation_dir=med)
        assert d["stilling"]["sessions_archived"] == 2
        assert d["stilling"]["bytes_archived"] == 3500
        assert d["stilling"]["continuation_chats"] == 2   # INDEX.md excluded


def test_sangama_event_counts():
    with tempfile.TemporaryDirectory() as t:
        store, arch, coord, med = _world(t)
        with open(os.path.join(coord, "events.jsonl"), "w") as f:
            f.write(json.dumps({"type": "fact_served", "ts": "x"}) + "\n")
            f.write(json.dumps({"type": "fact_served", "ts": "x"}) + "\n")
            f.write(json.dumps({"type": "collision_warned", "ts": "x"}) + "\n")
        d = rp.compute(store_dir=store, archive_root=arch,
                       coord_root=coord, meditation_dir=med)
        assert d["sangama"]["facts_served"] == 2
        assert d["sangama"]["collisions_warned"] == 1


def test_drift_demotion_pairs_with_later_repair():
    """sleep.py's actual drift path logs `sleep.demoted`, not a `sleep.regraded
    ... -> unverified` event (that shape only fires for corruption). Before this
    fix, a real drift catch could never pair with its later repair: caught came
    from the store's `drifted` flag (no down_at entry), so `repaired` stayed 0
    forever even after the memory re-verified clean."""
    with tempfile.TemporaryDirectory() as t:
        store, arch, coord, med = _world(t)
        rows = [
            {"event": "sleep.demoted", "id": "mem_d",
             "detail": "evidence drift: excerpt no longer present in source",
             "ts": "2026-08-19T10:00:00+00:00"},
            {"event": "sleep.regraded", "id": "mem_d",
             "detail": "unverified -> machine_checked (excerpt present in source)",
             "ts": "2026-08-20T10:00:00+00:00"},
        ]
        with open(os.path.join(store, "journal.jsonl"), "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        d = rp.compute(store_dir=store, archive_root=arch,
                       coord_root=coord, meditation_dir=med)
        assert d["drift"]["caught"] == 1
        assert d["drift"]["repaired"] == 1, d["drift"]
        assert abs(d["drift"]["median_repair_hours"] - 24.0) < 0.1


def test_open_demotion_not_double_counted_with_store_flag():
    """A still-open drift catch has BOTH a `sleep.demoted` journal event and a
    live `drifted` flag in the store — the same catch, not two."""
    with tempfile.TemporaryDirectory() as t:
        store, arch, coord, med = _world(t)
        with open(os.path.join(store, "journal.jsonl"), "w") as f:
            f.write(json.dumps({"event": "sleep.demoted", "id": "mem_o",
                "detail": "evidence drift: gone",
                "ts": "2026-08-19T10:00:00+00:00"}) + "\n")
        with open(os.path.join(store, "memories.jsonl"), "w") as f:
            f.write(json.dumps({"id": "mem_o", "active": True,
                "flags": ["drifted"],
                "epistemic": {"evidence_status": "unverified"},
                "evidence": [{"source": "/x"}]}) + "\n")
        d = rp.compute(store_dir=store, archive_root=arch,
                       coord_root=coord, meditation_dir=med)
        assert d["drift"]["caught"] == 1, d["drift"]
        assert d["drift"]["repaired"] == 0


def test_repair_pairs_across_rotated_journals():
    """A downgrade in a rotated journal + repair in the current one must pair."""
    with tempfile.TemporaryDirectory() as t:
        store, arch, coord, med = _world(t)
        with open(os.path.join(store, "journal-20260801-000000.jsonl"), "w") as f:
            f.write(json.dumps({"event": "sleep.regraded", "id": "mem_r",
                "detail": "machine_checked -> unverified (x)",
                "ts": "2026-08-01T00:00:00+00:00"}) + "\n")
        with open(os.path.join(store, "journal.jsonl"), "w") as f:
            f.write(json.dumps({"event": "sleep.regraded", "id": "mem_r",
                "detail": "unverified -> machine_checked (y)",
                "ts": "2026-08-02T00:00:00+00:00"}) + "\n")
        d = rp.compute(store_dir=store, archive_root=arch,
                       coord_root=coord, meditation_dir=med)
        assert d["drift"]["repaired"] == 1, d["drift"]


def test_cli_envelope():
    r = subprocess.run([sys.executable, os.path.join(SKILL, "report.py"), "--json"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0
    env = json.loads(r.stdout)
    for k in ("success", "data", "metadata", "errors"):
        assert k in env
    assert env["data"]["drift"]["repaired"] >= 0


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
