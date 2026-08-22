"""Tests for brain.py — the live brain server (Rule 0, precondition A).

Contract:
  - state() assembles every organ into one JSON dict: store, goals, fleet,
    live sessions, repair items, queues, wins, digest — no invented numbers
  - the server binds 127.0.0.1 ONLY (the brain never faces a network)
  - GET /api/state -> 200 JSON with all sections; GET / -> 200 self-contained
    HTML (no external http assets) that fetches /api/state
  - a request never crashes the server (bad paths -> 404, state errors -> 500
    JSON, process survives)

Run: python3 ~/.claude/skills/meditate/test_brain.py
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)
import brain as br


def test_state_has_all_sections():
    s = br.state()
    for k in ("store", "goals", "live_sessions", "fleet", "repair",
              "queues", "wins", "digest", "generated"):
        assert k in s, f"state missing {k}"
    assert isinstance(s["goals"], list)
    assert isinstance(s["live_sessions"], list)


def test_server_binds_loopback_and_serves():
    srv = br.make_server(port=0)                      # ephemeral port
    assert srv.server_address[0] == "127.0.0.1", "brain must bind loopback ONLY"
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=10) as r:
            assert r.status == 200
            data = json.loads(r.read())
            assert "store" in data and "goals" in data
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as r:
            assert r.status == 200
            page = r.read().decode()
            assert "/api/state" in page, "page must fetch live state"
            assert "http://" not in page.replace("http://127.0.0.1", "") \
                   and "https://" not in page, "page must be self-contained"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/nope", timeout=10) as r:
            raise AssertionError("404 expected")
    except urllib.error.HTTPError as e:
        assert e.code == 404
    finally:
        srv.shutdown()


def test_act_requires_header_and_runs_known_actions():
    calls = []
    old = br.ACT_RUNNER
    br.ACT_RUNNER = lambda a, g: calls.append((a, g)) or {"started": True, "output": "Launched 2 agent(s)"}
    try:
        srv = br.make_server(port=0)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{port}"
        # no header -> 403 (CSRF guard)
        req = urllib.request.Request(base + "/api/act", data=b'{"action":"go"}',
                                     method="POST")
        try:
            urllib.request.urlopen(req, timeout=10)
            raise AssertionError("403 expected without X-Meditate header")
        except urllib.error.HTTPError as e:
            assert e.code == 403
        # with header -> runs
        req = urllib.request.Request(base + "/api/act",
                                     data=json.dumps({"action": "fix", "arg": "2"}).encode(),
                                     headers={"X-Meditate": "1"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            j = json.loads(r.read())
            assert j["started"] is True
            assert "Launched" in j["output"], "click must return the REAL output"
        assert calls == [("fix", "2")], calls
        # unknown action -> 400
        req = urllib.request.Request(base + "/api/act",
                                     data=b'{"action":"rm-rf"}',
                                     headers={"X-Meditate": "1"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=10)
            raise AssertionError("400 expected for unknown action")
        except urllib.error.HTTPError as e:
            assert e.code == 400
        srv.shutdown()
    finally:
        br.ACT_RUNNER = old


def test_state_is_json_serializable():
    json.dumps(br.state())


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
