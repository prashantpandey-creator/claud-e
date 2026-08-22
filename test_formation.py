"""Tests for formation.py — knowledge FORMING as a natural part (Rule 0, A).

Contract:
  Lane 1 (deterministic, runs on the heartbeat):
  - commits are extracted from transcripts (both `[branch hash] subject` and
    `hash subject` log lines), deduped by hash
  - each becomes a memory with evidence born attached: source = the
    transcript, excerpt = the literal line (so it grades machine_checked at
    the next pass, and archiving the transcript retargets it)
  - forming twice is idempotent
  Lane 2 (judgment, orchestrated):
  - substantive sessions not yet distilled appear in the formation queue
  - `distill <sid>` builds an agent kickoff that names the memory dir and
    requires Why / How-to-apply / originSessionId
  - `--done` marks the ledger; the session leaves the queue

Run: python3 ~/.claude/skills/meditate/test_formation.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)
import formation as fm

TRANSCRIPT = (
    '{"type":"tool_result","text":"[main abc1234] Fix the flux capacitor\\n 3 files changed"}\n'
    '{"type":"tool_result","text":"def5678 0.9.9 — polish the chrome\\nsome other line"}\n'
    '{"type":"tool_result","text":"[main abc1234] Fix the flux capacitor\\n(again, duped)"}\n'
)


def _world(t):
    proj = os.path.join(t, "projects", "-x")
    os.makedirs(proj)
    tp = os.path.join(proj, "sess-1.jsonl")
    with open(tp, "w") as f:
        f.write(TRANSCRIPT)
    store = os.path.join(t, "store")
    os.makedirs(store)
    return tp, store


def test_extract_only_unambiguous_stdout_form():
    """Only `[branch hash] subject` forms; hex-prefixed listings (session ids,
    log lines) are refused — live data proved they form garbage."""
    with tempfile.TemporaryDirectory() as t:
        tp, _ = _world(t)
        commits = fm.extract_commits(tp)
        assert [c["hash"] for c in commits] == ["abc1234"], commits
        assert commits[0]["subject"] == "Fix the flux capacitor"


def test_sessionid_like_lines_refused():
    with tempfile.TemporaryDirectory() as t:
        proj = os.path.join(t, "projects", "-x"); os.makedirs(proj)
        tp = os.path.join(proj, "s.jsonl")
        with open(tp, "w") as f:
            f.write('{"text":"83525ed2 can you check if we can find blue lotus"}\n')
        assert fm.extract_commits(tp) == []


def test_basename_file_resolved_via_project_dir():
    with tempfile.TemporaryDirectory() as t:
        tp, store = _world(t)
        sess = [{"file": os.path.basename(tp),
                 "_project_dir": os.path.dirname(tp), "_project_slug": "-x"}]
        assert fm.form_commit_facts(store, sess) == 1


def test_form_creates_memories_with_birth_evidence():
    with tempfile.TemporaryDirectory() as t:
        tp, store = _world(t)
        n = fm.form_commit_facts(store, [{"file": tp, "_project_slug": "-x"}])
        assert n == 1
        mems = [json.loads(l) for l in open(os.path.join(store, "memories.jsonl"))]
        assert len(mems) == 1
        m = next(m for m in mems if "abc1234" in m["statement"])
        assert "formed" in m["tags"] and "commit-fact" in m["tags"]
        ev = m["evidence"][0]
        assert ev["source"] == tp
        assert "Fix the flux capacitor" in ev["excerpt"]


def test_form_idempotent():
    with tempfile.TemporaryDirectory() as t:
        tp, store = _world(t)
        sessions = [{"file": tp, "_project_slug": "-x"}]
        assert fm.form_commit_facts(store, sessions) == 1
        assert fm.form_commit_facts(store, sessions) == 0
        assert len(open(os.path.join(store, "memories.jsonl")).readlines()) == 1


def test_queue_and_done():
    with tempfile.TemporaryDirectory() as t:
        ledger = os.path.join(t, "distilled.jsonl")
        sessions = [
            {"session_id": "big", "counts": {"user": 40}, "file": "/x/big.jsonl",
             "_project_slug": "-x", "title": "big work"},
            {"session_id": "tiny", "counts": {"user": 2}, "file": "/x/t.jsonl",
             "_project_slug": "-x", "title": ""},
        ]
        q = fm.formation_queue(sessions, ledger_path=ledger)
        assert [s["session_id"] for s in q] == ["big"], "only substantive sessions queue"
        fm.mark_distilled("big", ledger_path=ledger)
        assert fm.formation_queue(sessions, ledger_path=ledger) == []


def test_queue_dedupes_by_sid_keeping_fullest():
    """Live data: 37 entries were 29 sessions (same sid from several project
    dirs). Duplicate queue rows = duplicate distill agents."""
    with tempfile.TemporaryDirectory() as t:
        ledger = os.path.join(t, "distilled.jsonl")
        sessions = [
            {"session_id": "dup", "counts": {"user": 20}, "file": "/a/dup.jsonl",
             "_project_slug": "-a", "title": "early snapshot"},
            {"session_id": "dup", "counts": {"user": 90}, "file": "/b/dup.jsonl",
             "_project_slug": "-b", "title": "full snapshot"},
        ]
        q = fm.formation_queue(sessions, ledger_path=ledger)
        assert len(q) == 1, q
        assert q[0]["counts"]["user"] == 90, "must keep the fullest snapshot"


def test_kickoff_content():
    with tempfile.TemporaryDirectory() as t:
        sessions = [{"session_id": "big", "counts": {"user": 40},
                     "file": "/x/big.jsonl", "_project_slug": "-slug",
                     "title": "the big refactor", "cwd": "/repo"}]
        k = fm.distill_kickoff("big", sessions,
                               memory_root=os.path.join(t, "mem"))
        assert k is not None
        for needle in ("originSessionId", "Why:", "How to apply", "big.jsonl", "-slug"):
            assert needle in k["prompt"], f"kickoff missing {needle}"


def test_cli_envelope():
    r = subprocess.run([sys.executable, os.path.join(SKILL, "formation.py"), "--json"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    env = json.loads(r.stdout)
    for k in ("success", "data", "metadata", "errors"):
        assert k in env


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
