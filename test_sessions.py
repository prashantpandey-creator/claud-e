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

        # --- project attribution must work for ANYONE ---
        # `_project_of` hardcoded the string "/vedic puran/", so for every
        # user who is not this author it returned None for every file: no
        # projects, so derive.py proposes nothing, projects.py is empty, and
        # sprawl loses its multi-project term. A repo root is the general
        # answer -- it is what a "project" means on any machine.
        import subprocess as _sp
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        repo = os.path.join(d, "code", "acme-api")
        os.makedirs(os.path.join(repo, "src"))
        _sp.run(["git", "init", "-q", repo], check=True, env=env)
        check(sessions._project_of(os.path.join(repo, "src", "server.py")) == "acme-api",
              "a plain repo on any machine must attribute: got %r"
              % sessions._project_of(os.path.join(repo, "src", "server.py")))

        # a git WORKTREE has .git as a FILE, not a directory -- this owner
        # uses dozens of them, and treating only dirs as repos loses them all
        wt = os.path.join(d, "code", "wt-feature")
        os.makedirs(wt)
        with open(os.path.join(wt, ".git"), "w") as f:
            f.write("gitdir: /elsewhere/.git/worktrees/wt-feature\n")
        check(sessions._project_of(os.path.join(wt, "app.py")) == "wt-feature",
              "worktree not attributed: %r" % sessions._project_of(os.path.join(wt, "app.py")))

        # outside any repo: fall back to the first segment under cwd, so work
        # in a plain directory still groups instead of vanishing into None
        loose = os.path.join(d, "loose")
        os.makedirs(os.path.join(loose, "notes"))
        check(sessions._project_of(os.path.join(loose, "notes", "a.md"), cwd=loose) == "notes",
              "no-repo fallback failed: %r"
              % sessions._project_of(os.path.join(loose, "notes", "a.md"), cwd=loose))
        check(sessions._project_of("/nowhere/x.py") is None,
              "unattributable path must be None, not a guess")

        # --- the filename IS the session id ---
        # A resumed/compacted transcript carries its ANCESTOR's sessionId in
        # its first rows. Trusting that made 12 of 132 real transcripts
        # collide onto 8 ids in the graded store — including the session
        # doing this pass, which reported itself as a different session.
        resumed = os.path.join(d, "aaaa1111-2222-3333-4444-555555555555.jsonl")
        _write(resumed, [{"type": "system", "sessionId": "OLDPARENT", "cwd": "/x",
                          "timestamp": "2026-06-02T10:00:00.000Z", "content": "boot"},
                         {"type": "user", "timestamp": "2026-06-02T10:01:00.000Z",
                          "message": {"role": "user", "content": "carry on with the work"}}])
        r = sessions.extract_file(resumed)
        check(r["session_id"] == "aaaa1111-2222-3333-4444-555555555555",
              f"resumed session took its ancestor's id: {r['session_id']}")

        # --- titles: custom-title is the real one; ai-title is the fallback ---
        # 123 of 132 real sessions had no ai-title line, so every graded
        # session memory read "Session '(untitled)' on unknown" — content-free.
        # The titles were there the whole time under a type the parser ignored.
        ct = os.path.join(d, "CT.jsonl")
        _write(ct, [{"type": "ai-title", "aiTitle": "auto guess"},
                    {"type": "custom-title", "customTitle": "Fix the retry loop"},
                    {"type": "user", "timestamp": "2026-06-03T10:00:00.000Z",
                     "message": {"role": "user", "content": "fix it"}}])
        check(sessions.extract_file(ct)["title"] == "Fix the retry loop",
              "custom-title must win over ai-title")
        only_ai = os.path.join(d, "AI.jsonl")
        _write(only_ai, [{"type": "ai-title", "aiTitle": "auto guess"},
                         {"type": "user", "timestamp": "2026-06-03T10:00:00.000Z",
                          "message": {"role": "user", "content": "fix it"}}])
        check(sessions.extract_file(only_ai)["title"] == "auto guess",
              "ai-title must still work when there is no custom-title")

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
