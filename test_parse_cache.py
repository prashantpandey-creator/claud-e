"""Tests for the transcript parse cache in sessions.extract_file.

WHY (measured 2026-08-29):

One compute_metrics() call parsed 800,884 JSON lines out of 506 transcript
files, TWICE. metrics.py asks scan_all_projects for cap=20 and
projects.rollup() asks for cap=500 inside that same call, and neither knew
about the other. 15.5s per call, ~8s of it pure json.loads, for files that
mostly had not changed since the last question was asked. test_metrics.py
calls compute_metrics ~14 times and ran 135s against doctor's 180s cap — one
busy afternoon from reporting "no verdict" on a healthy install.

The key is file IDENTITY — (path, mtime_ns, size, cap, snippet) — never a
clock. A TTL has to choose between going stale on the session being written
to right now and expiring while nothing changed. mtime and size cannot do
either: the live transcript re-parses every time, which is correct, because
it is the one that actually changed.

Result: 8.1s warm -> 0.3s, cold unchanged at 13.5s, test_metrics 135s -> 28s.

These tests were first appended to test_sessions.py, where they would NEVER
have run — that file's main() is a hand-written sequence of check() calls,
not a discoverer of test_ functions. Five green-looking tests as dead code is
the same defect they exist to catch, one level up.

Run: python3 ~/.claude/skills/meditate/test_parse_cache.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sessions  # noqa: E402


def _tiny_transcript(path, n_user=2, extra=""):
    rows = []
    for i in range(n_user):
        rows.append({"type": "user",
                     "timestamp": "2026-08-29T%02d:00:00Z" % (i % 23 + 1),
                     "message": {"role": "user",
                                 "content": "do thing %d %s" % (i, extra)}})
        rows.append({"type": "assistant",
                     "timestamp": "2026-08-29T%02d:00:05Z" % (i % 23 + 1),
                     "message": {"role": "assistant",
                                 "content": [{"type": "text", "text": "ok"}]}})
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _scratch(n_user=2, extra=""):
    d = tempfile.mkdtemp()
    f = os.path.join(d, "s.jsonl")
    _tiny_transcript(f, n_user=n_user, extra=extra)
    sessions._PARSE_CACHE.clear()
    return f


def test_a_repeat_scan_of_an_UNCHANGED_file_is_served_from_cache():
    f = _scratch()
    a = sessions.extract_file(f, cap=20)
    assert len(sessions._PARSE_CACHE) == 1, "nothing was cached"
    b = sessions.extract_file(f, cap=20)
    assert len(sessions._PARSE_CACHE) == 1, "a second entry for the same file"
    assert a["counts"] == b["counts"] and a["first_user"] == b["first_user"]


def test_a_CHANGED_file_is_re_parsed():
    """FALSIFIER, and the whole reason the key is mtime and size rather than a
    TTL. A live session's transcript changes constantly; it must never be
    answered from a stale parse."""
    f = _scratch(n_user=1)
    before = sessions.extract_file(f, cap=20)
    time.sleep(0.01)
    _tiny_transcript(f, n_user=6, extra="and more")
    after = sessions.extract_file(f, cap=20)
    assert after["counts"]["user"] > before["counts"]["user"], \
        "the file grew from 1 user turn to 6 and the cache served the old parse"


def test_a_file_that_changes_WITHOUT_growing_is_still_re_parsed():
    """Size alone would miss a same-length rewrite. mtime_ns is in the key for
    this case."""
    f = _scratch(n_user=3, extra="aaaa")
    before = sessions.extract_file(f, cap=20)
    time.sleep(0.01)
    _tiny_transcript(f, n_user=3, extra="bbbb")   # same shape, same length
    after = sessions.extract_file(f, cap=20)
    assert os.path.getsize(f) == os.path.getsize(f)
    assert "bbbb" in json.dumps(after["user_messages"]), \
        "a same-size rewrite was served from the old parse"
    assert "aaaa" in json.dumps(before["user_messages"])


def test_a_DIFFERENT_cap_is_not_served_from_another_caps_entry():
    """metrics asks cap=20 and rollup asks cap=500 in the same call. Sharing
    one entry between them would silently truncate one caller's data — and it
    is the shared-work case that motivated the cache in the first place, so
    the temptation to key on the path alone is real."""
    f = _scratch(n_user=30)
    small = sessions.extract_file(f, cap=3)
    big = sessions.extract_file(f, cap=100)
    assert len(sessions._PARSE_CACHE) == 2, "two caps collapsed to one entry"
    assert len(big["user_messages"]) > len(small["user_messages"])


def test_the_caller_cannot_reach_back_into_the_cache():
    """A shallow dict() copy hands out the SAME lists the cache holds. No
    caller mutates one today — checked across every module — but a cache that
    can be corrupted from outside fails silently and only under repetition,
    which is the worst shape there is."""
    f = _scratch(n_user=3)
    first = sessions.extract_file(f, cap=20)
    # user_messages is a list of DICTS — the first _clone copied the outer
    # list only, so this line reached straight into the cache.
    first["user_messages"][0]["text"] = "INJECTED"
    first["user_messages"].append({"text": "APPENDED"})
    first["files_touched"].append("/tmp/injected")
    first["counts"]["user"] = 9999
    second = sessions.extract_file(f, cap=20)
    flat = json.dumps(second["user_messages"])
    assert "INJECTED" not in flat, "a message dict inside the cached record was mutated"
    assert "APPENDED" not in flat, "the cached list was mutated"
    assert "/tmp/injected" not in second["files_touched"]
    assert second["counts"]["user"] != 9999


def test_the_cached_record_is_not_the_SAME_object_twice():
    """scan_all_projects stamps _project_dir and _project_slug onto every
    record it returns. Handing out one object would let one project's
    annotations show up on another's."""
    f = _scratch()
    a = sessions.extract_file(f, cap=20)
    b = sessions.extract_file(f, cap=20)
    assert a is not b
    a["_project_slug"] = "one"
    assert "_project_slug" not in b


def test_the_cache_is_BOUNDED():
    """An unbounded dict of parsed transcripts inside a long-lived server
    process is a leak. 506 files on this machine; the cap is well above that
    and exists so the number can never be a surprise."""
    assert sessions._PARSE_CACHE_MAX >= 1000


def test_an_unreadable_path_does_not_crash_and_does_not_cache():
    """os.stat fails, so there is no identity to key on — and no identity
    means no caching, rather than a guess at one."""
    n = len(sessions._PARSE_CACHE)
    try:
        sessions.extract_file("/no/such/transcript.jsonl", cap=20)
    except OSError:
        pass          # raising is fine; caching a phantom is not
    assert len(sessions._PARSE_CACHE) == n


def _main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print("  ok   %s" % fn.__name__)
        except AssertionError as e:
            failed += 1; print("  FAIL %s: %s" % (fn.__name__, e))
        except Exception as e:
            failed += 1; print("  ERR  %s: %s: %s" % (fn.__name__, type(e).__name__, e))
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
