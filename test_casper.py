"""Tests for the Casper mascot surface — face, ear, mouth, and the gate.

Contract:
  - GET /casper serves a self-contained page (no external assets)
  - the page carries the three organs: ghost SVG, speech recognition, speech
    synthesis — a companion missing any of them is a poster
  - POST /api/act action=say routes one turn through converse and returns
    BOTH the spoken line and the parsed turn
  - voice NEVER pushes/deploys, even asked politely through the page
  - /api/state exposes briefing+timing so the mascot can decide to speak
  - the proactive path is gated: the page only speaks unprompted when
    interrupt_ok is true

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
import brain as br


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
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def test_casper_page_served_and_self_contained():
    srv, base = _serve()
    try:
        with urllib.request.urlopen(base + "/casper", timeout=10) as r:
            assert r.status == 200
            page = r.read().decode()
        assert "http://" not in page.replace("http://127.0.0.1", ""), "no external assets"
        assert "https://" not in page, "no external assets"
    finally:
        srv.shutdown()


def test_page_has_face_ear_and_mouth():
    """A companion missing any organ is a poster."""
    page = br.CASPER_PAGE
    assert 'id="ghost"' in page and "<svg" in page, "no face"
    assert "SpeechRecognition" in page, "no ear"
    assert "SpeechSynthesisUtterance" in page, "no mouth"
    assert 'id="bubble"' in page, "nothing to read what it said"


def test_say_endpoint_returns_speech_and_turn():
    srv, base = _serve()
    try:
        j = _post(base, {"action": "say", "value": "what should I be looking at"})
        assert j["started"] is True
        assert j["output"], "must return something to speak"
        assert "intent" in j["turn"] and "heard" in j["turn"]
    finally:
        srv.shutdown()


def test_voice_never_ships_through_the_page():
    """The hard line: no push/deploy by voice, even politely."""
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
        with urllib.request.urlopen(base + "/api/state", timeout=25) as r:
            s = json.loads(r.read())
        assert "headline" in s["briefing"], "mascot cannot know WHAT to say"
        assert "interrupt_ok" in s["timing"], "mascot cannot know WHEN"
    finally:
        srv.shutdown()


def test_proactive_speech_is_gated_in_the_page():
    """The page must check interrupt_ok before speaking unprompted."""
    page = br.CASPER_PAGE
    assert "st.interrupt_ok" in page, "unprompted speech is not gated on the pause"
    assert "lastSaid" in page, "would repeat the same line forever"


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
