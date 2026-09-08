"""Tests for voice.py — Casper: what to say, and whether now is the moment.

Two honest decisions, both deterministic:
  briefing()          — the SINGLE highest-leverage thing to say right now,
                        in plain words, drawn from data that already exists
  interruptibility()  — flow / pause / idle / away, measured from real
                        activity. NOT mood-reading: a proxy, labeled as one.
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
import voice as vc


def _coord(tmp, files_age_s=None):
    """A presence dir with one session whose last edit is files_age_s ago."""
    cd = os.path.join(tmp, "sessions"); os.makedirs(cd)
    if files_age_s is not None:
        now = time.time()
        p = os.path.join(cd, "sess-a.json")
        with open(p, "w") as f:
            json.dump({"sid": "sess-a", "cwd": "/repo",
                       "files": {"/repo/x.py": now - files_age_s}}, f)
        os.utime(p, (now - files_age_s, now - files_age_s))
    return cd


# ---- interruptibility: measured, never guessed --------------------------

# The timing layer now measures the PERSON — keyboard/mouse idle and the app
# in front — instead of guessing from session file timestamps. Signals are
# injected here so the tests do not depend on whether someone is at this Mac
# while they run.

def test_flow_when_hands_are_moving():
    r = vc.interruptibility(sig={"idle_s": 2, "frontmost": "Terminal"})
    assert r["state"] == "flow", r
    assert r["interrupt_ok"] is False, "never break flow"
    assert "Terminal" in r["why"], "should say WHERE they are working"


def test_pause_when_hands_have_been_still_a_moment():
    r = vc.interruptibility(sig={"idle_s": 45, "frontmost": "Terminal"})
    assert r["state"] == "pause" and r["interrupt_ok"] is True, r


def test_settled_when_stepped_back_but_still_around():
    r = vc.interruptibility(sig={"idle_s": 400, "frontmost": "Terminal"})
    assert r["state"] == "settled" and r["interrupt_ok"] is True, r


def test_away_when_nobody_has_touched_the_machine():
    r = vc.interruptibility(sig={"idle_s": 1800, "frontmost": "Terminal"})
    assert r["state"] == "away" and r["interrupt_ok"] is False, r


def test_never_speaks_into_a_meeting():
    """The one hard no: another human already has the conversation."""
    r = vc.interruptibility(sig={"idle_s": 1, "frontmost": "zoom.us",
                                 "in_meeting": True})
    assert r["state"] == "meeting", r
    assert r["interrupt_ok"] is False, "must not talk over a call"


def test_falls_back_to_file_activity_when_the_keyboard_cannot_be_read():
    """No HID signal is not the same as nobody home — the old proxy still
    answers, and says out loud that it is guessing."""
    with tempfile.TemporaryDirectory() as t:
        cd = _coord(t, files_age_s=20)
        r = vc.interruptibility(coord_dir=cd, sig={})
        assert r["state"] == "flow", r
        assert "guess" in r["basis"].lower(), "a guess must announce itself"


def test_basis_is_labeled_as_measurement_not_mood():
    r = vc.interruptibility(sig={"idle_s": 2, "frontmost": "Terminal"})
    b = r["basis"].lower()
    assert "idle" in b or "measured" in b, r["basis"]
    assert "mood" not in b, "it measures presence, and must not claim more"


# ---- briefing: one thing, highest leverage ------------------------------

def _world(tmp, repair=False):
    med = os.path.join(tmp, "med"); os.makedirs(med)
    store = os.path.join(med, "nidra_store"); os.makedirs(store)
    gdir = os.path.join(tmp, "goals"); os.makedirs(gdir)
    if repair:
        with open(os.path.join(med, "repair-queue.md"), "w") as f:
            f.write("# Repair queue\n## mem_x\n")
    return med, store, gdir


def test_briefing_leads_with_repair_when_knowledge_broke():
    with tempfile.TemporaryDirectory() as t:
        med, store, gdir = _world(t, repair=True)
        b = vc.briefing(meditation_dir=med, store_dir=store, goals_dir=gdir,
                        history_path=os.path.join(t, "h.jsonl"))
        assert b["headline"], b
        assert b["kind"] == "repair", "broken knowledge must be the top whisper"
        assert b["action"] == "meditate fix", "and name the fix action"


def test_briefing_is_one_thing_not_a_list():
    with tempfile.TemporaryDirectory() as t:
        med, store, gdir = _world(t)
        with open(os.path.join(gdir, "g.md"), "w") as f:
            f.write("---\nname: g\ntitle: Ship it\nproject: p\ncwd: /r\n"
                    "status: active\n---\n## Milestones\n- [ ] the task\n")
        b = vc.briefing(meditation_dir=med, store_dir=store, goals_dir=gdir,
                        history_path=os.path.join(t, "h.jsonl"))
        assert isinstance(b["headline"], str) and "\n" not in b["headline"]


def test_briefing_empty_world_says_all_clear():
    with tempfile.TemporaryDirectory() as t:
        med, store, gdir = _world(t)
        b = vc.briefing(meditation_dir=med, store_dir=store, goals_dir=gdir,
                        history_path=os.path.join(t, "h.jsonl"))
        assert b["headline"], "even quiet must say something honest"


def test_cli_envelope():
    r = subprocess.run([sys.executable, os.path.join(SKILL, "voice.py"), "--json"],
                       capture_output=True, text=True, timeout=90)
    assert r.returncode == 0
    env = json.loads(r.stdout)
    for k in ("success", "data", "metadata", "errors"):
        assert k in env
    assert "headline" in env["data"]["briefing"]
    assert "state" in env["data"]["timing"]


def test_patience_shortens_the_gap_he_waits_for():
    """"Never break flow" is right for ten minutes and wrong forever: someone
    working at a keyboard is never 25 s idle, so the rule muted him all day."""
    import tempfile, time as _t
    med = tempfile.mkdtemp()
    p = os.path.join(med, "last-spoke")

    def spoke(ago):
        open(p, "w").write("x")
        os.utime(p, (_t.time() - ago, _t.time() - ago))

    sig = {"idle_s": 8, "frontmost": "Terminal"}
    spoke(30)                      # just said something
    assert vc.interruptibility(sig=sig, meditation_dir=med)["interrupt_ok"] is False, \
        "should still be waiting for a real pause"
    spoke(2400)                    # holding it 40 minutes
    r = vc.interruptibility(sig=sig, meditation_dir=med)
    assert r["interrupt_ok"] is True, "held that long, 8 s of quiet is a fair moment"


def test_patience_never_overrides_a_meeting():
    """Politeness that decays must not decay through the one hard no."""
    import tempfile, time as _t
    med = tempfile.mkdtemp()
    p = os.path.join(med, "last-spoke")
    open(p, "w").write("x")
    os.utime(p, (_t.time() - 99999, _t.time() - 99999))
    r = vc.interruptibility(sig={"idle_s": 2, "frontmost": "zoom.us",
                                 "in_meeting": True}, meditation_dir=med)
    assert r["interrupt_ok"] is False and r["state"] == "meeting", r


# ---------------------------------------------------------------------------
# the bot volunteers what changed in the run, without being asked
# ---------------------------------------------------------------------------

def _run(done=0, running=0, hands=0, commits=0, shipped=(), events=()):
    nodes = [{"id": "r%d" % i, "kind": "goal", "status": "running",
              "title": "step %d" % i, "goal_title": "G"} for i in range(running)]
    nodes += [{"id": "d%d" % i, "kind": "goal", "status": "done", "title": t,
               "goal_title": "G", "result": {"verified_commits": ["abc1234"]}}
              for i, t in enumerate(shipped)]
    nodes += [{"id": "h%d" % i, "kind": "human", "status": "waiting",
               "title": "yours %d" % i, "goal_title": "G"} for i in range(hands)]
    return {"armed": True, "nodes": nodes,
            "events": [{"what": e} for e in events],
            "metrics": {"done": done, "nodes": 10, "running": running,
                        "spent_usd": 3.0, "verified_commits": commits, "human": hands}}


def test_a_finished_step_is_VOLUNTEERED_once():
    """"The bot should give us an update on what it's doing." It could
    answer when asked, but never spoke first — so a step that shipped while
    he was away was news nobody delivered."""
    import tempfile, voice as vc
    with tempfile.TemporaryDirectory() as t:
        before = _run(done=0, running=1)
        after = _run(done=1, running=0, commits=1, shipped=("Wire the CI gate",))
        # first call learns the world; it must not narrate history at him
        first = vc.run_update(status=lambda: before, meditation_dir=t)
        assert first is None, first
        said = vc.run_update(status=lambda: after, meditation_dir=t)
        assert said and "CI gate" in said, said
        # and it is said ONCE
        assert vc.run_update(status=lambda: after, meditation_dir=t) is None


def test_a_NEW_thing_on_his_list_is_volunteered_too():
    import tempfile, voice as vc
    with tempfile.TemporaryDirectory() as t:
        vc.run_update(status=lambda: _run(running=1), meditation_dir=t)
        said = vc.run_update(status=lambda: _run(running=1, hands=2), meditation_dir=t)
        assert said and ("2" in said or "two" in said.lower()), said
        assert "you" in said.lower(), said


def test_a_QUIET_run_says_nothing_at_all():
    """The failure mode that kills a notifier: something to say every time.
    An unchanged run is silence, not a status recital."""
    import tempfile, voice as vc
    with tempfile.TemporaryDirectory() as t:
        state = _run(done=3, running=2, hands=1)
        vc.run_update(status=lambda: state, meditation_dir=t)
        for _ in range(3):
            assert vc.run_update(status=lambda: state, meditation_dir=t) is None


def test_the_voice_bookmark_does_not_EAT_the_mail_lane_s_changes():
    """Both channels read the same fingerprint. If they shared one bookmark
    whichever ran first would mark the change seen and the other would go
    silent — the mail digest and the spoken update must each get it."""
    import tempfile, voice as vc, mail as ml
    with tempfile.TemporaryDirectory() as t:
        before, after = _run(running=1), _run(done=1, commits=1, shipped=("Ship it",))
        # Both lanes take the bookmark first, then the change arrives.
        # Asserted on SEEING it, not on delivery: a clean HOME has no
        # ~/.sendmail.conf, so asserting the mail actually sent tested the
        # machine's mail setup instead of the bookmarks.
        vc.run_update(status=lambda: before, meditation_dir=t)
        st = ml.load_state(t)
        st["fingerprint"] = ml.fingerprint(before)
        ml.save_state(st, t)
        assert vc.run_update(status=lambda: after, meditation_dir=t), "voice went silent"
        assert ml.digest(after, ml.load_state(t)), "mail went silent after voice spoke"


def test_the_briefing_LEADS_with_run_news_when_there_is_some():
    """Casper speaks voice.py's headline — that is the whole delivery path,
    no Swift changes. So the update has to reach the headline or it is
    built and not wired."""
    import inspect, voice as vc
    src = inspect.getsource(vc.briefing)
    assert "run_update" in src, "the briefing never asks for run news"


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
