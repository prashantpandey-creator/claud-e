"""mail.py — the lane to the owner when he is away.

Contract: a digest goes out only when the run changed in a way worth a mail
(new YOUR HANDS item, a step shipped, a stop, a hold, a close-out); every
mail carries a nonce in its subject and the state file remembers what each
nonce was about; a quiet pass sends nothing; nothing here needs a network
to be tested (the runner is injected).

Run: python3 ~/.claude/skills/meditate/test_mail.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)
import mail as ml


def _g(hands=(), done=(), stopped=0, armed=True, closed=False, hold=0):
    nodes = []
    for i, t in enumerate(hands):
        nodes.append({"id": "h%d" % i, "kind": "human", "status": "waiting", "title": t, "goal_title": "G"})
    for i, t in enumerate(done):
        nodes.append({"id": "d%d" % i, "kind": "goal", "status": "done", "title": t, "goal_title": "G",
                      "result": {"verified_commits": ["abcdef1234"], "pushed": True}})
    return {"nodes": nodes, "events": [{"what": "stopped", "node": "d0", "why": "ran 61 min", "attempt": 1}] * stopped,
            "armed": armed, "summary_at": "x" if closed else "", "hold_until": hold,
            "metrics": {"done": len(done), "nodes": len(nodes), "running": 0, "spent_usd": 1.5}, "until": "21:00"}


def test_a_quiet_pass_sends_NOTHING():
    st = {"fingerprint": ml.fingerprint(_g(hands=("Build the APK",)))}
    assert ml.digest(_g(hands=("Build the APK",)), st) is None
    assert ml.digest(None, {}) is None


def test_a_new_item_under_YOUR_HANDS_is_mailed_with_a_nonce():
    d = ml.digest(_g(hands=("Build the APK and sign in on a device",)), {})
    assert d and d["subject"].startswith("[claud-e #") and "your hands: 1 new" in d["subject"], d
    assert "Build the APK" in d["body"] and "Reply" in d["body"] and "until 21:00" in d["body"]
    assert d["items"] == [{"kind": "human", "id": "h0", "title": "Build the APK and sign in on a device"}]
    assert len(d["nonce"]) == 8


def test_shipped_and_stopped_are_their_own_sections():
    prev = {"fingerprint": ml.fingerprint(_g())}
    d = ml.digest(_g(done=("Android sign-in repaired",), stopped=1), prev)
    assert "SHIPPED" in d["body"] and "abcdef123" in d["body"] and "STOPPED" in d["body"], d["body"]
    assert "shipped: 1" in d["subject"]


def test_send_digest_uses_the_runner_and_REMEMBERS_the_nonce():
    with tempfile.TemporaryDirectory() as t:
        calls = []
        runner = lambda argv, body: calls.append((argv, body)) or (True, "")
        r = ml.send_digest(meditation_dir=t, runner=runner, campaign_state=_g(hands=("Supply the Pixel ID",)))
        assert r["sent"] and calls and calls[0][0][0] == ml.SENDMAIL and calls[0][0][2] == r["subject"], (r, calls)
        assert "Pixel ID" in calls[0][1]
        st = ml.load_state(t)
        assert r["nonce"] in st["sent"] and st["sent"][r["nonce"]]["items"][0]["id"] == "h0"
        # the same state again: nothing
        r2 = ml.send_digest(meditation_dir=t, runner=runner, campaign_state=_g(hands=("Supply the Pixel ID",)))
        assert r2["sent"] is False and len(calls) == 1, r2
        # a failed send is not remembered as sent
        bad = lambda argv, body: (False, "smtp down")
        r3 = ml.send_digest(meditation_dir=t, runner=bad, campaign_state=_g(hands=("Supply the Pixel ID", "Approve iOS")))
        assert r3["sent"] is False and "smtp down" in r3["why"]
        assert ml.load_state(t)["fingerprint"]["hands"] == ["h0"]


def test_no_recipient_is_a_named_refusal_not_a_crash():
    with tempfile.TemporaryDirectory() as t:
        conf = os.path.join(t, "conf.json"); open(conf, "w").write("{}")
        r = ml.send("s", "b", runner=lambda a, b: (True, ""), conf=conf)
        assert r["sent"] is False and "no recipient" in r["why"]
        assert ml.configured(sendmail=os.path.join(t, "nope"), conf=conf) is False


def test_the_summary_mail_carries_a_nonce_too():
    with tempfile.TemporaryDirectory() as t:
        calls = []
        r = ml.send_summary("CLAUD-E run summary", "SHIPPED\n  - x", runner=lambda a, b: calls.append(a) or (True, ""), meditation_dir=t)
        assert r["sent"] and calls[0][2].startswith("[claud-e #") and "summary" in calls[0][2]
        st = ml.load_state(t)
        assert any(v.get("summary") for v in st["sent"].values())


def _main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print("  ok   " + fn.__name__)
        except AssertionError as e:
            failed += 1; print("  FAIL %s: %s" % (fn.__name__, e))
        except Exception as e:
            failed += 1; print("  ERR  %s: %s: %s" % (fn.__name__, type(e).__name__, e))
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
