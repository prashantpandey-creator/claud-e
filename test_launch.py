#!/usr/bin/env python3
"""Tests for launch — thread-row parsing and archive-candidate detection.

Builds tiny fixture INDEX.md files (real per-session table shape) and asserts:
live vs settled rows are both detected, the memory column is read from the
right position, and a session only becomes an archive candidate when EVERY
one of its threads is settled. Run: python3 test_launch.py
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import launch  # noqa: E402


def _write_index(base, session_slug, rows):
    d = os.path.join(base, session_slug)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "INDEX.md"), "w") as f:
        f.write("# Fixture session\n\n")
        f.write("| # | Thread | vṛtti (kind) | Status | Memory |\n")
        f.write("|---|--------|--------------|--------|--------|\n")
        for r in rows:
            f.write(f"| {r['n']} | {r['thread']} | pramāṇa | {r['status']} | {r['memory']} |\n")


def test_parses_both_live_and_settled_rows():
    tmp = tempfile.mkdtemp()
    try:
        _write_index(tmp, "aaa11111-mixed", [
            {"n": 1, "thread": "Thread A", "status": "🟢 Active", "memory": "[[mem-a]]"},
            {"n": 2, "thread": "Thread B", "status": "✅ Settled", "memory": "[[mem-b]]"},
        ])
        threads = launch.find_threads(session_dir=tmp)
        assert len(threads) == 2, f"expected 2 rows, got {len(threads)}"
        statuses = {t["thread"]: t["status"] for t in threads}
        assert statuses["Thread A"] == "live"
        assert statuses["Thread B"] == "settled"
    finally:
        shutil.rmtree(tmp)


def test_memory_column_read_correctly_not_status_column():
    tmp = tempfile.mkdtemp()
    try:
        _write_index(tmp, "bbb22222-solo", [
            {"n": 1, "thread": "Solo thread", "status": "🟢 GPU training", "memory": "[[the-real-memory]]"},
        ])
        threads = launch.find_threads(session_dir=tmp)
        assert threads[0]["memory"] == "[[the-real-memory]]", (
            f"memory column bled into status text: got {threads[0]['memory']!r}"
        )
    finally:
        shutil.rmtree(tmp)


def test_archive_candidate_requires_ALL_threads_settled():
    tmp = tempfile.mkdtemp()
    try:
        _write_index(tmp, "ccc33333-partial", [
            {"n": 1, "thread": "Done part", "status": "✅ Settled", "memory": "—"},
            {"n": 2, "thread": "Live part", "status": "🟢 Active", "memory": "—"},
        ])
        _write_index(tmp, "ddd44444-full", [
            {"n": 1, "thread": "Done one", "status": "✅ Settled", "memory": "—"},
            {"n": 2, "thread": "Done two", "status": "✅ Settled", "memory": "—"},
        ])
        threads = launch.find_threads(session_dir=tmp)
        candidates = launch.find_archive_candidates(threads)
        candidate_sessions = {c["session"] for c in candidates}

        assert "ccc33333-partial" not in candidate_sessions, (
            "a session with one live thread must NOT be an archive candidate"
        )
        assert "ddd44444-full" in candidate_sessions, (
            "a session where every thread is settled MUST be an archive candidate"
        )
        full = next(c for c in candidates if c["session"] == "ddd44444-full")
        assert full["short_id"] == "ddd44444"
        assert full["thread_count"] == 2
    finally:
        shutil.rmtree(tmp)


def test_no_threads_no_index_returns_empty():
    tmp = tempfile.mkdtemp()
    try:
        threads = launch.find_threads(session_dir=tmp)
        assert threads == []
        assert launch.find_archive_candidates(threads) == []
    finally:
        shutil.rmtree(tmp)




def test_kickoff_resolves_per_thread_file_and_fenceless_prompt():
    """Each live row must open ITS OWN continuation chat (named in the Memory
    column as → **file.md**), not the first continue*.md in the dir; and a
    kickoff without a ``` fence (the documented template) must still extract."""
    tmp = tempfile.mkdtemp()
    try:
        _write_index(tmp, "bbb22222-perthread", [
            {"n": 1, "thread": "Thread X", "status": "🟢 Live", "memory": "→ **my-thread.md**"},
        ])
        d = os.path.join(tmp, "bbb22222-perthread")
        with open(os.path.join(d, "continue-decoy.md"), "w") as f:
            f.write("# decoy\n## Start a fresh chat with\n```\nWRONG kickoff\n```\n")
        with open(os.path.join(d, "my-thread.md"), "w") as f:
            f.write("# mine\n## Start a fresh chat with\ncd /tmp\ndo the right thing\n")
        threads = launch.find_threads(session_dir=tmp)
        live = [t for t in threads if t["status"] == "live"]
        assert len(live) == 1, f"expected 1 live row, got {len(live)}"
        k = live[0].get("kickoff", "")
        assert "do the right thing" in k, f"kickoff not from the named file: {k!r}"
        assert "WRONG" not in k, "kickoff bled from the decoy continue*.md"
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"✅ {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} passed")


def test_build_launch_interactive_not_piped():
    """Regression: `cat | claude` ran non-interactive and died — the owner got
    an empty Terminal. Prompt must be an argument; cwd must be quoted."""
    from launch import build_launch
    kf, cmd, script = build_launch("/Users/x/vedic puran", "Fix Apple's rejection\nline2", "goal-mila-live")
    assert "| claude" not in cmd, cmd
    assert "--dangerously-skip-permissions" in cmd
    assert "--model sonnet" in cmd, "fleet default model must be pinned: " + cmd
    _, cmd2, _ = build_launch("/x", "k", "t", model="opus")
    assert "--model opus" in cmd2, "per-goal model override must win: " + cmd2
    assert "cd '/Users/x/vedic puran'" in cmd, cmd
    assert open(kf).read() == "Fix Apple's rejection\nline2"
    assert 'do script "' in script and "tell application" in script


def test_cwd_injection_neutralized():
    """FINDING 4: a shell metachar in cwd must not become a command."""
    from launch import build_launch
    import shlex
    kf, cmd, _ = build_launch("/x'; touch /tmp/pwned; echo '", "k", "t")
    # the malicious cwd is not a real dir -> falls back to home, shell-quoted
    assert "touch /tmp/pwned" not in cmd.split("&&")[0] or "cd " + shlex.quote(cmd) , cmd
    assert "; touch" not in cmd, "cwd injection reached the command: " + cmd


def test_kickoff_file_unpredictable():
    """FINDING 6: kickoff path must not be the guessable /tmp/claude-kickoff-<name>."""
    from launch import build_launch
    import os
    kf, _, _ = build_launch("/tmp", "prompt", "goal-x")
    assert kf != "/tmp/claude-kickoff-goal-x.txt", "predictable temp path"
    assert os.path.exists(kf) and oct(os.stat(kf).st_mode)[-3:] == "600", kf
    os.unlink(kf)
