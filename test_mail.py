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


def _conf(t):
    """A config the test owns. CI proved the need on its first real run:
    three of these tests injected a fake sender but still resolved the
    RECIPIENT from ~/.sendmail.conf, so they passed only on the owner's
    machine and failed on a clean one with 'no recipient'."""
    p = os.path.join(t, "sendmail.conf")
    open(p, "w").write(json.dumps({"user": "me@gmail.com", "password": "x"}))
    return p


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
        r = ml.send_digest(meditation_dir=t, runner=runner, conf=_conf(t), campaign_state=_g(hands=("Supply the Pixel ID",)))
        assert r["sent"] and calls and calls[0][0][0] == ml.SENDMAIL and calls[0][0][2] == r["subject"], (r, calls)
        assert "Pixel ID" in calls[0][1]
        st = ml.load_state(t)
        assert r["nonce"] in st["sent"] and st["sent"][r["nonce"]]["items"][0]["id"] == "h0"
        # the same state again: nothing
        r2 = ml.send_digest(meditation_dir=t, runner=runner, conf=_conf(t), campaign_state=_g(hands=("Supply the Pixel ID",)))
        assert r2["sent"] is False and len(calls) == 1, r2
        # a failed send is not remembered as sent
        bad = lambda argv, body: (False, "smtp down")
        r3 = ml.send_digest(meditation_dir=t, runner=bad, conf=_conf(t), campaign_state=_g(hands=("Supply the Pixel ID", "Approve iOS")))
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
        r = ml.send_summary("CLAUD-E run summary", "SHIPPED\n  - x", runner=lambda a, b: calls.append(a) or (True, ""), meditation_dir=t, conf=_conf(t))
        assert r["sent"] and calls[0][2].startswith("[claud-e #") and "summary" in calls[0][2]
        st = ml.load_state(t)
        assert any(v.get("summary") for v in st["sent"].values())


def _raw(subject, frm, body, auth="mx.google.com; dkim=pass header.i=@gmail.com header.s=x",
         mid="<m1@x>", in_reply_to="<ours@claud-e>"):
    hdr = "From: %s\nTo: %s\nSubject: %s\nMessage-ID: %s\n" % (frm, frm, subject, mid)
    if auth:
        hdr += "Authentication-Results: %s\n" % auth
    if in_reply_to:
        hdr += "In-Reply-To: %s\n" % in_reply_to
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


def test_check_proves_the_credential_WITHOUT_sending_anything():
    """authgate polls this every 20s while the owner regenerates the app
    password — a poll that SENT a mail would fill his inbox, and a poll
    that only read the config file would pass on the dead password that
    started this. It logs in and hangs up: the login is the proof."""
    calls = []

    class _SMTP:
        def __init__(self, host, port, timeout=0):
            calls.append(("connect", host, port))
        def login(self, user, pwd):
            calls.append(("login", user))
            if pwd != "goodpassword":
                raise RuntimeError("Username and Password not accepted")
        def quit(self):
            calls.append(("quit",))
        def sendmail(self, *a, **k):
            raise AssertionError("check must never send")
        def send_message(self, *a, **k):
            raise AssertionError("check must never send")

    with tempfile.TemporaryDirectory() as t:
        conf = os.path.join(t, "conf.json")
        open(conf, "w").write(json.dumps({"user": "me@gmail.com", "password": "goodpassword"}))
        r = ml.check(conf=conf, smtp=_SMTP)
        assert r["ok"] is True, r
        assert [c[0] for c in calls] == ["connect", "login", "quit"], calls
        assert calls[0][1] == "smtp.gmail.com" and calls[0][2] == 465, calls
        # the dead password is a NAMED failure, not a crash
        open(conf, "w").write(json.dumps({"user": "me@gmail.com", "password": "deadpassword"}))
        r = ml.check(conf=conf, smtp=_SMTP)
        assert r["ok"] is False and "not accepted" in r["why"], r
        # no config at all
        r = ml.check(conf=os.path.join(t, "nope.json"), smtp=_SMTP)
        assert r["ok"] is False and "not configured" in r["why"], r


def test_the_TEST_mail_is_recorded_so_a_reply_to_it_can_be_ACTED_ON():
    """Measured live 2026-09-07: --test sent a real mail with nonce
    #644eb874 (delivered, confirmed in the inbox), then poll_inbox answered
    "nothing was ever sent, so nothing can be a reply" — the send path
    recorded no nonce, so the one mail a person is most likely to reply to
    was the one reply the lane could never act on."""
    with tempfile.TemporaryDirectory() as t:
        sent = []
        r = ml.send_test(runner=lambda argv, body: sent.append(argv) or (True, ""), meditation_dir=t, conf=_conf(t))
        assert r["sent"], r
        st = ml.load_state(t)
        assert st.get("sent"), "the test mail left no nonce to reply to"
        nonce = list(st["sent"])[0]
        assert nonce in sent[0][2], (nonce, sent[0][2])
        assert st["sent"][nonce].get("test") is True, st["sent"][nonce]
        # and a reply to it is now actionable — it steers nothing, but it is
        # recognised as ours rather than refused as an unknown nonce
        p = ml.parse_reply(_raw("Re: [claud-e #%s] live" % nonce, "me@gmail.com", "hello"), "me@gmail.com")
        out = ml.act_on_reply(p, st, lambda n: {"ok": True}, lambda a, b: {"ok": True}, lambda a, b: {"started": True})
        assert out["why"] != "no nonce we sent", out


def test_a_reply_from_the_OWNERS_OWN_GMAIL_is_accepted_and_our_own_send_is_not():
    """Measured live 2026-09-07 over real IMAP: a message sent from
    fcpuru95@gmail.com to itself carries NO Authentication-Results, NO
    DKIM-Signature, NO Received-SPF — nothing, because it never leaves
    Google. It carries Gmail's own \\Sent label instead. A dkim-only gate
    therefore refuses every reply the owner will ever send from his own
    account (proven: the live proof mail was refused 'no dkim=pass').

    Gmail's \\Sent label is the equivalent attestation — an outsider cannot
    make Gmail label their forgery as this account's sent mail — so it is
    accepted alongside dkim=pass. What keeps our OWN outbound mail from
    being read back as a reply is a separate, stronger test: a reply has
    In-Reply-To; our sends do not."""
    st = {"sent": {"ab12cd34": {"items": [{"kind": "human", "id": "h0", "title": "Build the APK"}]}}}
    done = lambda nid: {"ok": True}
    steer = lambda nid, msg: {"ok": True}
    cont = lambda name, msg: {"started": True}

    # his own Gmail: no auth headers at all, but Gmail's \Sent label
    p = ml.parse_reply(_raw("Re: [claud-e #ab12cd34] x", "me@gmail.com", "done", auth=""), "me@gmail.com",
                       gmail_sent=True)
    assert p["dkim"] is False and p["gmail_sent"] is True, p
    r = ml.act_on_reply(p, st, done, steer, cont)
    assert r["acted"] and r["what"] == "done", r

    # our OWN outbound mail — same label, but it is not a reply
    p = ml.parse_reply(_raw("[claud-e #ab12cd34] CLAUD-E — your hands", "me@gmail.com", "ignore me",
                            auth="", in_reply_to=""), "me@gmail.com", gmail_sent=True)
    r = ml.act_on_reply(p, st, done, steer, cont)
    assert r["acted"] is False and "not a reply" in r["why"], r

    # an outsider forging his address: arrives externally, so it DOES carry
    # an Authentication-Results header, and that header says it failed
    p = ml.parse_reply(_raw("Re: [claud-e #ab12cd34] x", "me@gmail.com", "done",
                            auth="mx.google.com; dkim=fail header.i=@gmail.com"), "me@gmail.com",
                       gmail_sent=False)
    r = ml.act_on_reply(p, st, done, steer, cont)
    assert r["acted"] is False and "dkim" in r["why"], r

    # a legitimate reply from another provider still passes on dkim
    p = ml.parse_reply(_raw("Re: [claud-e #ab12cd34] x", "me@gmail.com", "done"), "me@gmail.com",
                       gmail_sent=False)
    assert ml.act_on_reply(p, st, done, steer, cont)["acted"] is True


def test_a_numbered_reply_ticks_THE_ITEM_YOU_MEANT():
    """The live digest of 2026-09-07 listed 16 things. A bare "done"
    against it ticks whichever item happens to be first — "Page token made
    permanent" — which is not what a person scanning 16 lines means. The
    mail numbers them, and "done 3" ticks the third. A bare "done" is only
    honoured when the mail carried exactly one item of his."""
    many = {"sent": {"aaaa0001": {"items": [{"kind": "human", "id": "h%d" % i, "title": "item %d" % i}
                                      for i in range(1, 5)]}}}
    ticked = []
    done = lambda nid: ticked.append(nid) or {"ok": True}
    steer = lambda nid, msg: {"ok": True}
    cont = lambda name, msg: {"started": True}

    p = ml.parse_reply(_raw("Re: [claud-e #aaaa0001] x", "me@gmail.com", "done 3"), "me@gmail.com")
    r = ml.act_on_reply(p, many, done, steer, cont)
    assert r["acted"] and ticked == ["h3"], (r, ticked)

    # bare "done" against many is refused, and says how to be specific
    ticked.clear()
    p = ml.parse_reply(_raw("Re: [claud-e #aaaa0001] x", "me@gmail.com", "done"), "me@gmail.com")
    r = ml.act_on_reply(p, many, done, steer, cont)
    assert r["acted"] is False and "which" in r["why"].lower() and ticked == [], (r, ticked)

    # out of range says so rather than guessing
    p = ml.parse_reply(_raw("Re: [claud-e #aaaa0001] x", "me@gmail.com", "done 9"), "me@gmail.com")
    assert ml.act_on_reply(p, many, done, steer, cont)["acted"] is False

    # one item: a bare "done" is unambiguous and still works
    one = {"sent": {"aaaa0001": {"items": [{"kind": "human", "id": "h0", "title": "only"}]}}}
    ticked.clear()
    p = ml.parse_reply(_raw("Re: [claud-e #aaaa0001] x", "me@gmail.com", "done"), "me@gmail.com")
    assert ml.act_on_reply(p, one, done, steer, cont)["acted"] and ticked == ["h0"]

    # and the digest body numbers them so the number means something
    d = ml.digest(_g(hands=("first thing", "second thing")), {})
    assert "1." in d["body"] and "2." in d["body"], d["body"]
    assert "done 2" in d["body"], "the mail must say how to answer"


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
