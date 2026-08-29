"""Tests for tree — everything going, as one tree.

WHY: the tool knew all of this and showed none of it together. Goals were a
table, dormant repos a list, live sessions a roster, the repair queue a file.
Six flat surfaces meant "where is my work" was a question you answered by
reading six things and holding them in your head.

The two rules that cost something to keep, and so are the ones tested here:
every node carries a MEANING (not just a count), and nothing is SYNTHESISED —
every line is a field that already exists or a quote from disk.

Run: python3 ~/.claude/skills/meditate/test_tree.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tree  # noqa: E402


_FAKE = {
    "goals": [{"name": "g1", "title": "Ship the thing", "done": 1, "total": 3,
               "next": "the next bit", "scope_delta": 2}],
    "live_sessions": [{"sid": "abc123", "label": "working", "last_file": "/x/y/foo.py",
                       "age_s": 120},
                      {"sid": "def456", "label": "idle one", "last_file": "", "age_s": 9000}],
    "dormant": [{"project": "old-thing", "idle": "8 weeks ago",
                 "last_commit": "did a thing", "what": None, "commits": 12}],
    "repair": [{"id": "m1", "statement": "something that broke",
                "fails": ["path:/gone/file.md"]}],
    "insights": {"headline": "three things need you"},
}


def test_every_branch_carries_a_MEANING_not_just_a_count():
    """A count is a readout. "(8)" tells you nothing about whether to care."""
    t = tree.build(_FAKE)
    for b in t["children"]:
        assert b["meaning"], "%s is a bare count with no meaning" % b["label"]


def test_a_leaf_says_WHY_it_is_there():
    t = tree.build(_FAKE)
    for b in t["children"]:
        for leaf in b["children"]:
            assert leaf["meaning"], \
                "%s / %s has no meaning line" % (b["label"], leaf["label"])


def test_broken_comes_FIRST():
    """Leverage order, same as the briefing: knowledge that failed its own
    check corrupts everything read downstream of it."""
    t = tree.build(_FAKE)
    assert t["children"][0]["kind"] == "repair", \
        [b["kind"] for b in t["children"]]


def test_an_idle_session_is_called_idle_not_live():
    """10 of 16 'live' sessions had touched no file on 2026-08-26. Calling
    them all live is what made the roster untrustworthy."""
    t = tree.build(_FAKE)
    live = [b for b in t["children"] if b["kind"] == "live"][0]
    idle = [k for k in live["children"] if k["label"] == "idle one"][0]
    assert "idle" in idle["meaning"]


def test_a_goal_that_GREW_says_so():
    """A percentage can fall because scope widened. Hiding that turns growing
    ambition into what looks like going backwards."""
    t = tree.build(_FAKE)
    goal = [b for b in t["children"] if b["kind"] == "moving"][0]["children"][0]
    assert "scope grew" in goal["meaning"], goal["meaning"]


def test_a_dormant_leaf_QUOTES_the_repo():
    t = tree.build(_FAKE)
    left = [b for b in t["children"] if b["kind"] == "dormant"][0]
    assert "did a thing" in left["children"][0]["meaning"], \
        "the last commit is not quoted — the node would be an opinion"


def test_the_blind_spot_branch_survives_being_EMPTY():
    """NOT MEASURED is the branch that says what the tree cannot tell you.
    Every other branch disappears when empty; this one must not, or the tool
    silently implies it covers everything."""
    t = tree.build({"goals": [], "live_sessions": [], "dormant": [], "repair": []})
    kinds = [b["kind"] for b in t["children"]]
    assert "unassessed" in kinds, kinds


def test_actions_on_leaves_are_verbs_the_runner_knows():
    """A node you cannot act on is a readout. Same rule as the mascot: the
    action must NAME a verb, because Casper's fall-through is a fleet launch."""
    import brain
    t = tree.build(_FAKE)
    for b in t["children"]:
        for leaf in b["children"] + [b]:
            act = (leaf.get("action") or "").split(" ")[0]
            if act:
                assert act in brain.ACTIONS, \
                    "%r on %r is not a verb the runner knows" % (act, leaf["label"])


def test_text_render_collapses_and_expands():
    t = tree.build(_FAKE)
    closed = "\n".join(tree.to_text(t, expand=False))
    opened = "\n".join(tree.to_text(t, expand=True))
    assert len(opened) > len(closed)
    assert "old-thing" in opened and "old-thing" not in closed


def test_html_uses_native_details_and_no_javascript():
    """One click per branch with zero JS — the browser ships this. A second
    JS accordion would be another thing to keep in sync, and would lose
    find-in-page and open-state-through-reload."""
    h = tree.to_html(tree.build(_FAKE))
    assert "<details" in h and "<summary" in h
    assert "<script" not in h.lower() and "onclick" not in h.lower()


def test_the_live_machine_builds_a_tree():
    t = tree.build()
    assert t["children"], "no branches at all on a machine with live work"
    print("       live: %s" % ", ".join("%s(%d)" % (b["label"], b["count"])
                                        for b in t["children"]))



# ---------------------------------------------------------------------------
# the audit — every one of these was a lie the tree told on 2026-08-29
# ---------------------------------------------------------------------------

def test_a_branch_NEVER_under_reports_its_own_size():
    """NOT MEASURED showed 8 children and reported its count as 8 while the
    truth was 29 unassessed products. A silent truncation, inside the one
    branch whose entire job is to admit the blind spot."""
    import projects as pj
    t = tree.build()
    u = [b for b in t["children"] if b["kind"] == "unassessed"][0]
    truth = len(pj.assessment_gaps().get("unassessed", []))
    if truth > 8:
        assert "more" in " ".join(k["label"] for k in u["children"]), \
            "%d unassessed, branch shows %d and never says so" % (truth, len(u["children"]))
        assert str(truth) in u["meaning"], \
            "the true count %d appears nowhere: %r" % (truth, u["meaning"])


def test_an_open_window_is_not_called_WORK_happening():
    """The branch was "LIVE RIGHT NOW (34)". coordination's own working/idle
    split said 7 moving, 27 idle — a 5x overstatement, and it ignored a
    `_state` field that already existed because brain.state() dropped it on
    the way out."""
    fake = {"live_sessions": [
        {"sid": "a", "label": "busy", "state": "working", "last_file": "/x/a.py", "age_s": 30},
        {"sid": "b", "label": "sat there", "state": "idle", "last_file": "", "age_s": 1200},
        {"sid": "c", "label": "also sat", "state": "idle", "last_file": "", "age_s": 1500}]}
    t = tree.build(fake)
    live = [b for b in t["children"] if b["kind"] == "live"][0]
    assert "1 moving" in live["meaning"] and "2 idle" in live["meaning"], live["meaning"]
    assert live["children"][0]["label"] == "busy", \
        "the ones actually moving are not at the top"
    assert live["children"][1]["meaning"].startswith("idle")


def test_brain_state_CARRIES_the_working_flag():
    """The field existed in coordination and died in the payload, so every
    consumer re-guessed liveness from last_file and got it wrong."""
    import brain
    rows = brain.state().get("live_sessions") or []
    if rows:
        assert any("state" in r for r in rows), \
            "brain.state() drops the working/idle flag again"


def test_the_headline_counts_MOVING_not_open():
    """The first line anyone reads said "33 Claude sessions live" while 7 were
    moving. Loudest place the tool overstated itself."""
    from insights import insights
    d = {"live_sessions": [{"sid": "a", "cwd": "/x", "state": "working"},
                           {"sid": "b", "cwd": "/x", "state": "idle"}],
         "goals": [], "repair": [], "fleet": []}
    h = insights(d)["headline"]
    # and with the flag ABSENT it must claim nothing about activity
    d2 = {"live_sessions": [{"sid": "a", "cwd": "/x"}, {"sid": "b", "cwd": "/x"}],
          "goals": [], "repair": [], "fleet": []}
    h2 = insights(d2)["headline"]
    assert "moving" not in h2, "guessed activity from a flag that was not there: %r" % h2
    assert "1 moving of 2 open" in h, h



def test_the_tool_reports_its_OWN_failures():
    """The loop it runs for your memories did not run on itself.

    Measured 2026-08-29: heartbeat.log held 45 identical `osascript error`
    lines — one class of failure, every one after a 75-second timeout — in a
    log nothing reads, while the dispatch ledger recorded 59 successes. A
    tool that reports on your work and never on itself is asking to be
    trusted on the one subject it has never checked.
    """
    t = tree.build()
    me = [b for b in t["children"] if b["kind"] == "self"]
    assert me, "no branch for the tool's own state"
    for k in me[0]["children"]:
        assert k["meaning"], "%s reports a failure with no explanation" % k["label"]


def test_repeated_failures_are_counted_by_CLASS_not_by_line():
    """45 lines of one error is one problem. Reporting 45 makes it look like
    45 problems and buries the single cause."""
    t = tree.build()
    me = [b for b in t["children"] if b["kind"] == "self"][0]
    labels = [k["label"] for k in me["children"]]
    # one entry per class, carrying its own multiplier
    assert len(labels) == len(set(labels)), labels


def test_the_self_branch_survives_being_CLEAN():
    """It must still appear when nothing is wrong, saying so — a branch that
    vanishes when healthy is indistinguishable from one that broke."""
    import doctor
    real = doctor._check_fleet
    doctor._check_fleet = lambda: {"checked": True, "dispatched": 0}
    try:
        b = tree._itself({})
        assert b["kind"] == "self"
        assert "nothing" in b["meaning"].lower() or b["children"]
    finally:
        doctor._check_fleet = real


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
