"""Tests for inbox.py — agent-to-agent messaging (Rule 0, A).

Laws pinned here:
  - addressed delivery: session-id prefix, project name, or explicit 'all'
  - delivered ONCE (a message repeated every tool call is pressure)
  - never deliver a sender their own message
  - stale mail (>48h) is history, not a message
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
import inbox as ib


def test_addressed_by_session_id():
    with tempfile.TemporaryDirectory() as t:
        ib.send("aaaa1111", "handle the payments fix", frm="owner", coord_root=t)
        mine = ib.fetch("aaaa1111-bbbb-cccc", coord_root=t)
        assert len(mine) == 1 and "payments" in mine[0]["body"]
        other = ib.fetch("zzzz9999-dddd", coord_root=t)
        assert other == [], "message leaked to the wrong session"


def test_addressed_by_project():
    with tempfile.TemporaryDirectory() as t:
        ib.send("purangpt", "backend branch first", frm="owner", coord_root=t)
        got = ib.fetch("sid-1", cwd="/Users/x/projects/purangpt", coord_root=t)
        assert len(got) == 1, got
        miss = ib.fetch("sid-2", cwd="/Users/x/projects/otherthing", coord_root=t)
        assert miss == []


def test_delivered_once_not_repeated():
    """The pressure law: a message must not re-deliver on every tool call."""
    with tempfile.TemporaryDirectory() as t:
        ib.send("sid-a", "do the thing", frm="owner", coord_root=t)
        first = ib.fetch("sid-a", coord_root=t)
        assert len(first) == 1, "not delivered at all: %s" % first
        second = ib.fetch("sid-a", coord_root=t)
        assert second == [], "message re-delivered (pressure law broken)"


def test_peek_does_not_consume():
    with tempfile.TemporaryDirectory() as t:
        ib.send("sid-a", "peek me", frm="owner", coord_root=t)
        assert len(ib.fetch("sid-a", coord_root=t, mark_read=False)) == 1
        assert len(ib.fetch("sid-a", coord_root=t)) == 1, "peek consumed it"


def test_never_deliver_own_message():
    with tempfile.TemporaryDirectory() as t:
        ib.send("all", "note to everyone", frm="sid-a", coord_root=t)
        assert ib.fetch("sid-a", coord_root=t) == [], "sender got their own message"
        assert len(ib.fetch("sid-b", coord_root=t)) == 1


def test_broadcast_requires_explicit_all():
    with tempfile.TemporaryDirectory() as t:
        ib.send("purangpt", "scoped", frm="owner", coord_root=t)
        assert ib.fetch("sid-x", cwd="/tmp/unrelated", coord_root=t) == []
        ib.send("all", "everyone", frm="owner", coord_root=t)
        assert len(ib.fetch("sid-x", cwd="/tmp/unrelated", coord_root=t)) == 1


def test_stale_mail_not_delivered():
    with tempfile.TemporaryDirectory() as t:
        old = time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                            time.gmtime(time.time() - (ib.KEEP_HOURS + 5) * 3600))
        with open(os.path.join(t, "messages.jsonl"), "w") as f:
            f.write(json.dumps({"id": "old-1", "to": "sid-a", "from": "owner",
                                "body": "ancient", "ts": old}) + "\n")
        assert ib.fetch("sid-a", coord_root=t) == [], "stale mail delivered"


def test_delivery_is_capped():
    with tempfile.TemporaryDirectory() as t:
        for i in range(10):
            ib.send("sid-a", "msg %d" % i, frm="owner", coord_root=t)
        assert len(ib.fetch("sid-a", coord_root=t)) <= ib.MAX_DELIVER


def test_render_is_plain():
    out = ib.render([{"from": "sessionabc", "body": "take the backend first"}])
    assert out.startswith("MESSAGE from") and "backend" in out


def test_cli_envelope():
    with tempfile.TemporaryDirectory() as t:
        env = dict(os.environ, MEDITATE_COORD_ROOT=t)
        subprocess.run([sys.executable, os.path.join(SKILL, "inbox.py"),
                        "send", "all", "hello", "there"], capture_output=True,
                       text=True, timeout=15, env=env)
        r = subprocess.run([sys.executable, os.path.join(SKILL, "inbox.py"),
                            "read", "--sid", "someone", "--json"],
                           capture_output=True, text=True, timeout=15, env=env)
        assert r.returncode == 0
        d = json.loads(r.stdout)
        assert d["success"] and d["data"]["count"] == 1


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
