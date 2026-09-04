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


def _raw(subject, frm, body, auth="mx.google.com; dkim=pass header.i=@gmail.com header.s=x", mid="<m1@x>"):
    hdr = "From: %s\nTo: %s\nSubject: %s\nMessage-ID: %s\n" % (frm, frm, subject, mid)
    if auth:
        hdr += "Authentication-Results: %s\n" % auth
    return (hdr + "Content-Type: text/plain; charset=utf-8\n\n" + body).encode()


def test_a_reply_is_the_owners_word_only_with_NONCE_plus_ADDRESS_plus_DKIM():
    st = {"sent": {"ab12cd34": {"items": [{"kind": "human", "id": "h0", "title": "Build the APK"}]}}}
    calls = []
    done = lambda nid: calls.append(("done", nid)) or {"ok": True}
    steer = lambda nid, msg: calls.append(("steer", nid, msg)) or {"ok": True}
    cont = lambda name, msg: calls.append(("continue", name, msg)) or {"started": True}
    good = ml.parse_reply(_raw("Re: [claud-e #ab12cd34] CLAUD-E — your hands: 1 new", "me@gmail.com",
                              "done\n\n> the quoted digest\n> more"), "me@gmail.com")
    assert good["nonce"] == "ab12cd34" and good["is_owner"] and good["dkim"] and good["body"] == "done", good
    r = ml.act_on_reply(good, st, done, steer, cont)
    assert r["acted"] and r["what"] == "done" and calls == [("done", "h0")], (r, calls)
    # each gate on its own
    calls.clear()
    bad_nonce = ml.parse_reply(_raw("Re: [claud-e #ffffffff] x", "me@gmail.com", "done"), "me@gmail.com")
    assert ml.act_on_reply(bad_nonce, st, done, steer, cont)["why"] == "no nonce we sent"
    stranger = ml.parse_reply(_raw("Re: [claud-e #ab12cd34] x", "evil@gmail.com", "done"), "me@gmail.com")
    assert "owner" in ml.act_on_reply(stranger, st, done, steer, cont)["why"]
    forged = ml.parse_reply(_raw("Re: [claud-e #ab12cd34] x", "me@gmail.com", "done", auth=""), "me@gmail.com")
    assert "dkim" in ml.act_on_reply(forged, st, done, steer, cont)["why"]
    failed = ml.parse_reply(_raw("Re: [claud-e #ab12cd34] x", "me@gmail.com", "done",
                                 auth="mx.google.com; dkim=fail header.i=@gmail.com"), "me@gmail.com")
    assert "dkim" in ml.act_on_reply(failed, st, done, steer, cont)["why"]
    assert calls == []


def test_a_reply_that_is_not_done_STEERS_the_agent_and_is_never_executed():
    st = {"sent": {"ab12cd34": {"items": [{"kind": "node", "id": "n7", "title": "Add the CI gate"}]}}}
    calls = []
    steer = lambda nid, msg: calls.append((nid, msg)) or {"ok": True}
    p = ml.parse_reply(_raw("Re: [claud-e #ab12cd34] shipped: 1", "me@gmail.com",
                            "Also run the tests on node 20; rm -rf / is not a command here\n-- \nsig"), "me@gmail.com")
    r = ml.act_on_reply(p, st, lambda n: {"ok": True}, steer, lambda a, b: {"started": True})
    assert r["acted"] and r["what"] == "steer" and calls[0][0] == "n7" and "rm -rf" in calls[0][1] and "sig" not in calls[0][1]
    # a plain agent (no campaign node) is continued
    st2 = {"sent": {"ab12cd34": {"items": [{"kind": "agent", "id": "revive-x", "title": "x"}]}}}
    cc = []
    r = ml.act_on_reply(p, st2, lambda n: {"ok": True}, steer, lambda a, b: cc.append((a, b)) or {"started": True})
    assert r["what"] == "continue" and cc[0][0] == "revive-x"


class _FakeImap:
    def __init__(self, msgs):
        self.msgs = msgs; self.flagged = []; self.logged_out = False
    def select(self, box): return ("OK", [b"1"])
    def search(self, cs, *crit): return ("OK", [b" ".join(str(i + 1).encode() for i in range(len(self.msgs)))])
    def fetch(self, mid, what): return ("OK", [(b"1 (RFC822 {n})", self.msgs[int(mid) - 1]), b")"])
    def store(self, mid, op, flags): self.flagged.append((mid, flags))
    def logout(self): self.logged_out = True


def test_poll_inbox_acts_once_per_message_and_REMEMBERS_it():
    with tempfile.TemporaryDirectory() as t:
        conf = os.path.join(t, "conf.json"); open(conf, "w").write(json.dumps({"user": "me@gmail.com", "password": "x"}))
        st = {"sent": {"ab12cd34": {"items": [{"kind": "human", "id": "h0", "title": "Build the APK"}]}}}
        ml.save_state(st, t)
        raw = _raw("Re: [claud-e #ab12cd34] your hands", "me@gmail.com", "done", mid="<reply-1@gmail.com>")
        done_calls = []
        fake = _FakeImap([raw])
        r = ml.poll_inbox(meditation_dir=t, imap=fake, conf=conf, done_fn=lambda nid: done_calls.append(nid) or {"ok": True},
                          steer_fn=lambda a, b: {"ok": True}, continue_fn=lambda a, b: {"started": True})
        assert r["polled"] and len(r["acted"]) == 1 and done_calls == ["h0"], r
        assert fake.flagged and fake.logged_out
        # the same message again: refused as handled, not acted twice
        fake2 = _FakeImap([raw])
        r2 = ml.poll_inbox(meditation_dir=t, imap=fake2, conf=conf, done_fn=lambda nid: done_calls.append(nid) or {"ok": True},
                           steer_fn=lambda a, b: {"ok": True}, continue_fn=lambda a, b: {"started": True})
        assert r2["acted"] == [] and done_calls == ["h0"] and "already handled" in r2["refused"][0]["why"], r2
        assert ml.load_state(t)["handled"] == ["<reply-1@gmail.com>"]
        # nothing sent → nothing polled
        assert ml.poll_inbox(meditation_dir=os.path.join(t, "empty"), imap=fake, conf=conf)["polled"] is False


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
