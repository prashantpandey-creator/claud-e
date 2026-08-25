"""Tests for the voice API the mascot speaks through.

This file used to test a browser page at /casper — a ghost SVG with the
browser's own SpeechRecognition and SpeechSynthesis. That page is discarded;
the real Casper is the native app in mascot/ (Casper.swift), which pops up
and speaks. The page and its three page-shape tests are gone.

What did NOT go: the API contracts underneath, which the native mascot uses
exactly as the page did. They were only living in this file by accident of
who wrote them first.

Contract:
  - POST /api/act action=say routes one turn through converse and returns
    BOTH the spoken line and the parsed turn
  - voice NEVER pushes/deploys, even asked politely
  - /api/state exposes briefing+timing, so the mascot can know WHAT to say
    (briefing.headline) and WHEN it may interrupt (timing.interrupt_ok)

Run: python3 ~/.claude/skills/meditate/test_casper.py
"""
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)
import tempfile
# isolate BEFORE importing brain: these tests POST real actions, and their
# records must never land in the owner's live activity trail.
_ISO = tempfile.mkdtemp(prefix="casper-test-")
os.environ["MEDITATE_COORD_DIR"] = os.path.join(_ISO, "sessions")
os.makedirs(os.environ["MEDITATE_COORD_DIR"], exist_ok=True)

import brain as br


def test_tests_do_not_touch_the_live_activity_log():
    """The guard: this suite fires real POSTs; none may reach the live log."""
    live = os.path.expanduser("~/.claude/coordination/events.jsonl")
    before = os.path.getsize(live) if os.path.exists(live) else 0
    srv, base = _serve()
    try:
        _post(base, {"action": "say", "value": "push it to production"})
    finally:
        srv.shutdown()
    after = os.path.getsize(live) if os.path.exists(live) else 0
    assert after == before, "test POSTs leaked into the owner's live activity log"


def _serve():
    srv = br.make_server(port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


def _post(base, payload):
    req = urllib.request.Request(base + "/api/act",
                                 data=json.dumps(payload).encode(),
                                 headers={"X-Meditate": "1",
                                          "Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read())


def test_say_endpoint_returns_speech_and_turn():
    srv, base = _serve()
    try:
        j = _post(base, {"action": "say", "value": "what should I be looking at"})
        assert j["started"] is True
        assert j["output"], "must return something to speak"
        assert "intent" in j["turn"] and "heard" in j["turn"]
    finally:
        srv.shutdown()


def test_voice_never_ships():
    """The hard line: no push/deploy by voice, even politely.

    This outlived the browser page it was written for. The page is gone; the
    native mascot talks to the same /api/act, so the guarantee still needs a
    test and this is it. Deleting it along with the page's own tests would
    have quietly removed a safety net, not dead code.
    """
    srv, base = _serve()
    try:
        for utter in ("push it to production", "deploy the backend now"):
            j = _post(base, {"action": "say", "value": utter})
            assert j["turn"]["intent"] == "refused", (utter, j["turn"])
            assert j["turn"]["executed"] is False
    finally:
        srv.shutdown()


def test_state_exposes_what_and_when():
    srv, base = _serve()
    try:
        with urllib.request.urlopen(base + "/api/state", timeout=90) as r:
            s = json.loads(r.read())
        assert "headline" in s["briefing"], "mascot cannot know WHAT to say"
        assert "interrupt_ok" in s["timing"], "mascot cannot know WHEN"
    finally:
        srv.shutdown()


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
