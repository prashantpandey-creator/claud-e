#!/usr/bin/env python3
"""Tests for sessions — the session-transcript extractor.

Builds a tiny fixture transcript (real Claude Code JSONL shape) and asserts the
compact extract: title, real user intents (noise excluded), chapter marks, files
touched, counts, timestamps, last-state, and the capping behavior that keeps the
output small even for a 35 MB session. Run:  python3 test_sessions.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import sessions  # noqa: E402


def _write(path, rows):
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


FIXTURE = [
    {"type": "system", "sessionId": "S1", "cwd": "/x", "gitBranch": "main",
     "timestamp": "2026-06-01T10:00:00.000Z", "content": "boot"},
    {"type": "ai-title", "sessionId": "S1", "aiTitle": "Build the thing"},
    {"type": "user", "timestamp": "2026-06-01T10:01:00.000Z",
     "message": {"role": "user", "content": "Please build feature X"}},
    {"type": "assistant", "timestamp": "2026-06-01T10:02:00.000Z",
     "message": {"role": "assistant", "content": [
         {"type": "text", "text": "On it."},
         {"type": "tool_use", "name": "Edit", "input": {"file_path": "/x/foo.py"}},
         {"type": "tool_use", "name": "mcp__ccd_session__mark_chapter",
          "input": {"title": "Phase one"}}]}},
    # noise: tool_result, no text -> excluded
    {"type": "user", "timestamp": "2026-06-01T10:03:00.000Z",
     "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}},
    # noise: command wrapper starting with "<" -> excluded
    {"type": "user", "timestamp": "2026-06-01T10:05:00.000Z",
     "message": {"role": "user", "content": "<command-name>/foo</command-name>"}},
    {"type": "user", "timestamp": "2026-06-01T10:06:00.000Z",
     "message": {"role": "user", "content": "Now do part two"}},
    {"type": "assistant", "timestamp": "2026-06-01T10:07:00.000Z",
     "message": {"role": "assistant", "content": [{"type": "text", "text": "Done part two."}]}},
]


def main():
    failures = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)

    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "S1.jsonl")
        _write(p, FIXTURE)
        rec = sessions.extract_file(p)

        check(rec["title"] == "Build the thing", f"title wrong: {rec['title']}")
        check(rec["cwd"] == "/x", "cwd wrong")
        check(rec["git_branch"] == "main", "git_branch wrong")
        texts = [u["text"] for u in rec["user_messages"]]
        check(texts == ["Please build feature X", "Now do part two"],
              f"user intents wrong (noise not excluded?): {texts}")
        check(rec["first_user"] == "Please build feature X", "first_user wrong")
        check(rec["last_user"] == "Now do part two", "last_user wrong")
        check([c["title"] for c in rec["chapter_marks"]] == ["Phase one"],
              "chapter marks wrong")
        check("/x/foo.py" in rec["files_touched"], "files_touched missing edit")
        check(rec["counts"]["user"] >= 2 and rec["counts"]["assistant"] >= 2,
              f"counts wrong: {rec['counts']}")
        check(rec["ts_start"] == "2026-06-01T10:00:00.000Z", "ts_start wrong")
        check(rec["ts_end"] == "2026-06-01T10:07:00.000Z", "ts_end wrong")
        check(rec["last_assistant_text"] == "Done part two.", "last_assistant_text wrong")
        check("Edit" in dict(rec["top_tools"]), "top_tools should include Edit")
        check(rec["sprawl_score"] >= 0, "sprawl_score must be present")

        # --- capping: a long session must NOT explode the output ---
        many = [{"type": "ai-title", "aiTitle": "Big"}]
        for i in range(200):
            many.append({"type": "user", "timestamp": f"2026-06-01T10:{i%60:02d}:00.000Z",
                         "message": {"role": "user", "content": f"intent number {i}"}})
        bp = os.path.join(d, "BIG.jsonl")
        _write(bp, many)
        big = sessions.extract_file(bp, cap=40)
        check(len(big["user_messages"]) <= 40,
              f"user_messages not capped: {len(big['user_messages'])}")
        check(big["first_user"] == "intent number 0", "cap should keep first intent")
        check(big["last_user"] == "intent number 199", "cap should keep last intent")
        check(big["counts"]["user"] == 200, "counts.user should reflect TRUE total, not cap")

        # --- envelope from scan_sessions ---
        env = sessions.scan_sessions(d, cap=40)
        for k in ("success", "data", "metadata", "errors"):
            check(k in env, f"envelope missing {k}")
        check(env["success"] is True, "expected success True")
        check(isinstance(env["data"]["sessions"], list), "data.sessions must be list")
        check(env["data"]["count"] == len(env["data"]["sessions"]), "count mismatch")
        try:
            json.dumps(env)
        except (TypeError, ValueError) as e:
            check(False, f"envelope not JSON-serializable: {e}")

        # --- single-session resolve: by file, and by title substring ---
        by_file = sessions.get_session(d, "S1")
        check(by_file["success"] and by_file["data"]["session"]["title"] == "Build the thing",
              "get_session by filename failed")
        by_title = sessions.get_session(d, "build the thing")
        check(by_title["success"] and by_title["data"]["session"]["session_id"],
              "get_session by title substring failed")
        miss = sessions.get_session(d, "no-such-session-xyz")
        check(miss["success"] is False and miss["errors"],
              "get_session should fail cleanly on no match")

    if failures:
        print("FAIL")
        for f in failures:
            print("  -", f)
        return 1
    print("PASS — all assertions green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
