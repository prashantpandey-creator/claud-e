"""Tests for archive.py — deterministic session archiver (Rule 0, precondition A).

Contract:
  - dry-run is the DEFAULT and moves nothing
  - --apply moves <sid>.jsonl AND its sidecar dir into the archive, and
    appends a restore record to ARCHIVE-INDEX.jsonl
  - restore moves both back exactly where they were
  - a session modified in the last 24h is NEVER archived (it may be live)
  - empty = counts.user <= EMPTY_MAX_USER_MSGS and size < EMPTY_MAX_BYTES
  - envelope always; exit 0 always

Run: python3 ~/.claude/skills/meditate/test_archive.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)
import archive as ar


def _mk_session(proj, sid, user_msgs=0, size=1000, age_days=40, sidecar=True):
    os.makedirs(proj, exist_ok=True)
    p = os.path.join(proj, sid + ".jsonl")
    rows = [{"type": "user", "message": {"role": "user", "content": "hi"}}] * user_msgs
    body = "\n".join(json.dumps(r) for r in rows)
    body += "x" * max(0, size - len(body))
    with open(p, "w") as f:
        f.write(body)
    if sidecar:
        os.makedirs(os.path.join(proj, sid, "tool-results"), exist_ok=True)
        with open(os.path.join(proj, sid, "tool-results", "r.txt"), "w") as f:
            f.write("residue")
    old = time.time() - age_days * 86400
    os.utime(p, (old, old))
    return p


def test_scan_finds_empty():
    with tempfile.TemporaryDirectory() as t:
        proj = os.path.join(t, "projects", "-x")
        _mk_session(proj, "empty-1", user_msgs=0, size=500)
        _mk_session(proj, "busy-1", user_msgs=40, size=900000)
        cands = ar.candidates(projects_root=os.path.join(t, "projects"), empty_only=True)
        ids = [c["sid"] for c in cands]
        assert "empty-1" in ids and "busy-1" not in ids


def test_fresh_session_never_archived():
    with tempfile.TemporaryDirectory() as t:
        proj = os.path.join(t, "projects", "-x")
        _mk_session(proj, "fresh", user_msgs=0, size=500, age_days=0)
        cands = ar.candidates(projects_root=os.path.join(t, "projects"), empty_only=True)
        assert cands == [], "a <24h-old session must never be a candidate"


def test_dry_run_moves_nothing():
    with tempfile.TemporaryDirectory() as t:
        proj = os.path.join(t, "projects", "-x")
        p = _mk_session(proj, "empty-1")
        rep = ar.run(projects_root=os.path.join(t, "projects"),
                     archive_root=os.path.join(t, "arch"), empty_only=True, apply=False)
        assert os.path.exists(p), "dry-run must not move files"
        assert rep["would_archive"] == 1 and rep["archived"] == 0


def test_apply_moves_jsonl_and_sidecar():
    with tempfile.TemporaryDirectory() as t:
        proj = os.path.join(t, "projects", "-x")
        p = _mk_session(proj, "empty-1")
        arch = os.path.join(t, "arch")
        rep = ar.run(projects_root=os.path.join(t, "projects"),
                     archive_root=arch, empty_only=True, apply=True)
        assert rep["archived"] == 1
        assert not os.path.exists(p)
        assert not os.path.exists(os.path.join(proj, "empty-1"))
        assert os.path.exists(os.path.join(arch, "-x", "empty-1.jsonl"))
        assert os.path.exists(os.path.join(arch, "-x", "empty-1", "tool-results", "r.txt"))
        idx = os.path.join(arch, "ARCHIVE-INDEX.jsonl")
        assert os.path.exists(idx)
        rec = json.loads(open(idx).read().strip().splitlines()[-1])
        assert rec["sid"] == "empty-1" and rec["from"].endswith("-x")


def test_restore_round_trip():
    with tempfile.TemporaryDirectory() as t:
        proj = os.path.join(t, "projects", "-x")
        p = _mk_session(proj, "empty-1")
        arch = os.path.join(t, "arch")
        ar.run(projects_root=os.path.join(t, "projects"), archive_root=arch,
               empty_only=True, apply=True)
        assert not os.path.exists(p)
        r = ar.restore("empty-1", archive_root=arch)
        assert r["restored"] is True
        assert os.path.exists(p), "jsonl must be back in the project dir"
        assert os.path.exists(os.path.join(proj, "empty-1", "tool-results", "r.txt"))


def test_restore_unknown_sid():
    with tempfile.TemporaryDirectory() as t:
        r = ar.restore("nope", archive_root=os.path.join(t, "arch"))
        assert r["restored"] is False


def test_older_than_filter():
    with tempfile.TemporaryDirectory() as t:
        proj = os.path.join(t, "projects", "-x")
        _mk_session(proj, "old-big", user_msgs=30, size=50000, age_days=90)
        _mk_session(proj, "new-big", user_msgs=30, size=50000, age_days=3)
        cands = ar.candidates(projects_root=os.path.join(t, "projects"),
                              empty_only=False, older_than_days=30)
        ids = [c["sid"] for c in cands]
        assert "old-big" in ids and "new-big" not in ids


def test_archive_retargets_graded_evidence():
    """The seam test: archiving a session whose memory cites its transcript
    must NOT break the evidence — the store follows the file, both ways."""
    with tempfile.TemporaryDirectory() as t:
        proj = os.path.join(t, "projects", "-x")
        p = _mk_session(proj, "cited-1")
        store = os.path.join(t, "store")
        os.makedirs(store)
        mem = {"id": "mem_1", "active": True, "statement": "s",
               "epistemic": {"evidence_status": "machine_checked"},
               "evidence": [{"source": p, "excerpt": "hi", "sha256": "x"}]}
        with open(os.path.join(store, "memories.jsonl"), "w") as f:
            f.write(json.dumps(mem) + "\n")
        old = ar.STORE_DIR
        ar.STORE_DIR = store
        try:
            arch = os.path.join(t, "arch")
            ar.run(projects_root=os.path.join(t, "projects"), archive_root=arch,
                   empty_only=True, apply=True)
            m = json.loads(open(os.path.join(store, "memories.jsonl")).read())
            assert m["evidence"][0]["source"].startswith(arch), \
                "evidence still points at the old path"
            assert os.path.exists(m["evidence"][0]["source"]), \
                "retargeted evidence source does not exist"
            ar.restore("cited-1", archive_root=arch)
            m = json.loads(open(os.path.join(store, "memories.jsonl")).read())
            assert m["evidence"][0]["source"] == p, "restore did not retarget back"
            assert os.path.exists(p)
        finally:
            ar.STORE_DIR = old


def test_cli_envelope_and_exit_zero():
    r = subprocess.run([sys.executable, os.path.join(SKILL, "archive.py"), "--json"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0
    env = json.loads(r.stdout)
    for k in ("success", "data", "metadata", "errors"):
        assert k in env
    assert env["data"]["archived"] == 0, "bare CLI run must be a dry-run"


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
