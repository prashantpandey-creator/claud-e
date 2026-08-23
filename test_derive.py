"""Tests for derive.py — goal proposals from real work (Rule 0, precondition A).

Everything runs against temp goal dirs and fixture sessions. The live goals
directory drives a real fleet; a test that writes there dispatches agents.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import derive


def _sess(sid, project, turns, title=None, cwd="/tmp/p", start=None, end=None):
    return {"session_id": sid, "projects": [project], "title": title,
            "cwd": cwd, "counts": {"user": turns},
            "ts_start": start, "ts_end": end}


def _goals(d, **files):
    os.makedirs(d, exist_ok=True)
    for name, project in files.items():
        with open(os.path.join(d, name + ".md"), "w") as f:
            f.write("---\nname: %s\ntitle: T\nproject: %s\nstatus: active\n---\n"
                    % (name, project))
    return d


def test_work_with_a_goal_is_not_proposed():
    with tempfile.TemporaryDirectory() as d:
        g = _goals(os.path.join(d, "goals"), purangpt_live="purangpt")
        s = [_sess("a", "purangpt", 500), _sess("b", "purangpt", 500)]
        assert derive.candidates(s, g) == []


def test_work_with_no_goal_is_proposed():
    with tempfile.TemporaryDirectory() as d:
        g = _goals(os.path.join(d, "goals"), purangpt_live="purangpt")
        s = [_sess("a", "AwakenerUnity", 137, "Game work elements resume"),
             _sess("b", "AwakenerUnity", 137, "Game work elements resume")]
        got = derive.candidates(s, g)
        assert len(got) == 1, got
        assert got[0]["project"] == "AwakenerUnity"
        assert got[0]["turns"] == 274


def test_the_title_is_the_PROJECT_never_a_guessed_intent():
    """Three heuristics tried, all wrong, so the claim was dropped.

    Deriving an intent-title from session titles read well and was false.
    Sessions here touch many projects each (a single session edits the memory
    store, the tool, and the app), so the "dominant title" of a project bucket
    is whichever unrelated session happened to weigh most: 1358 turns of work
    came out labelled "Lexis Nexis employee country rule".

    What IS true and checkable is that a project carries real effort and has
    no goal. So the proposal states that, names the project, and hands the
    session titles over as raw evidence for a human to name. A wrong title
    that looks authoritative is worse than an obviously unfinished one."""
    with tempfile.TemporaryDirectory() as d:
        g = _goals(os.path.join(d, "goals"))
        s = [_sess("a", "acme-api", 100, "Fix the retry loop"),
             _sess("b", "acme-api", 10, "unrelated tangent")]
        got = derive.candidates(s, g)
        assert got[0]["title"] == "acme-api", got[0]["title"]
        # the owner's own titles survive as EVIDENCE, not as a verdict
        assert "Fix the retry loop" in got[0]["session_titles"], got[0]


def test_errands_do_not_become_goals():
    """A queue of trivia is how a fleet stays busy going nowhere."""
    with tempfile.TemporaryDirectory() as d:
        g = _goals(os.path.join(d, "goals"))
        s = [_sess("a", "tiny", 3), _sess("b", "tiny", 4)]
        assert derive.candidates(s, g) == []


def test_one_session_is_not_a_pattern():
    with tempfile.TemporaryDirectory() as d:
        g = _goals(os.path.join(d, "goals"))
        assert derive.candidates([_sess("a", "proj", 9999, "big")], g) == []


def test_every_proposal_carries_its_evidence():
    with tempfile.TemporaryDirectory() as d:
        g = _goals(os.path.join(d, "goals"))
        s = [_sess("aaaa1111", "proj", 100, "The work", start="2026-01-01T00:00:00"),
             _sess("bbbb2222", "proj", 100, "The work", end="2026-02-01T00:00:00")]
        body = derive.render(derive.candidates(s, g)[0])
        assert "200 human turns" in body, body
        assert "The work" in body, "session titles must appear as evidence"
        assert "aaaa1111" in body and "bbbb2222" in body
        assert "2026-01-01" in body and "2026-02-01" in body


def test_proposal_has_NO_invented_milestones():
    """An invented milestone reports progress that never happened. Percentage
    is checked/total, so a fabricated checklist fabricates a number."""
    with tempfile.TemporaryDirectory() as d:
        g = _goals(os.path.join(d, "goals"))
        s = [_sess("a", "proj", 100, "The work"), _sess("b", "proj", 100, "The work")]
        body = derive.render(derive.candidates(s, g)[0])
        assert "- [ ]" not in body and "- [x]" not in body, body
        assert "status: proposed" in body


def test_proposals_are_INVISIBLE_to_the_fleet():
    """THE falsifier for the whole design. goals.py lists *.md in the goals
    dir itself; a proposal must never be picked up and dispatched against."""
    import goals
    with tempfile.TemporaryDirectory() as d:
        g = _goals(os.path.join(d, "goals"))
        s = [_sess("a", "proj", 100, "The work"), _sess("b", "proj", 100, "The work")]
        prop = os.path.join(g, "proposed")
        written = derive.write_proposals(derive.candidates(s, g), prop)
        assert written, "nothing written"
        assert os.path.dirname(written[0]) == prop
        seen = goals.load(g) if hasattr(goals, "load") else None
        if seen is not None:
            names = [x.get("name") for x in seen]
            assert not any("the-work" in str(n) for n in names), names
        # and the fleet's own listing must not contain the proposal file
        assert "proposed" not in [f for f in os.listdir(g) if f.endswith(".md")]


def test_write_never_overwrites_an_existing_proposal():
    """The owner edits proposals. A rerun that clobbers their edits is theft."""
    with tempfile.TemporaryDirectory() as d:
        g = _goals(os.path.join(d, "goals"))
        prop = os.path.join(g, "proposed")
        s = [_sess("a", "proj", 100, "The work"), _sess("b", "proj", 100, "The work")]
        first = derive.write_proposals(derive.candidates(s, g), prop)
        with open(first[0], "a") as f:
            f.write("\nOWNER EDIT\n")
        again = derive.write_proposals(derive.candidates(s, g), prop)
        assert again == [], again
        assert "OWNER EDIT" in open(first[0]).read()


def test_dry_run_writes_nothing():
    with tempfile.TemporaryDirectory() as d:
        g = _goals(os.path.join(d, "goals"))
        prop = os.path.join(g, "proposed")
        s = [_sess("a", "proj", 100, "The work"), _sess("b", "proj", 100, "The work")]
        env = derive.run(sessions=s, goals_dir=g, write=False, proposed_dir=prop)
        assert env["data"]["written"] == []
        assert not os.path.exists(prop)


def test_envelope_shape():
    import json as _j
    with tempfile.TemporaryDirectory() as d:
        g = _goals(os.path.join(d, "goals"))
        env = derive.run(sessions=[], goals_dir=g, proposed_dir=os.path.join(d, "p"))
        for k in ("tool_name", "success", "data", "metadata", "errors"):
            assert k in env, "envelope missing " + k
        _j.dumps(env)


def test_does_not_touch_the_live_goals_dir():
    live = derive.GOALS_DIR
    before = sorted(os.listdir(live)) if os.path.isdir(live) else None
    with tempfile.TemporaryDirectory() as d:
        g = _goals(os.path.join(d, "goals"))
        s = [_sess("a", "proj", 100, "x"), _sess("b", "proj", 100, "x")]
        derive.run(sessions=s, goals_dir=g, write=True,
                   proposed_dir=os.path.join(d, "p"))
    after = sorted(os.listdir(live)) if os.path.isdir(live) else None
    assert before == after, "derive tests wrote into the LIVE goals dir"


def test_one_intent_spanning_several_dirs_is_ONE_goal():
    """The goal is the invariant, not the directory.

    Live fire caught this: "Game work elements resume" proposed TWICE
    (AwakenerUnity + TheAwakener) and "Puran nodes and astrology data" FIVE
    times (one per wt-astro-* worktree). Clustering by directory clusters the
    surface; the owner's own title is the intent underneath it.
    """
    with tempfile.TemporaryDirectory() as d:
        g = _goals(os.path.join(d, "goals"))
        # As the real scan reports it: BOTH sessions touch BOTH dirs.
        both = ["AwakenerUnity", "TheAwakener"]
        s = [{"session_id": "a", "projects": both, "title": "Game work elements resume",
              "cwd": "/tmp/g", "counts": {"user": 137}, "ts_start": None, "ts_end": None},
             {"session_id": "b", "projects": both, "title": "Game work elements resume",
              "cwd": "/tmp/g", "counts": {"user": 137}, "ts_start": None, "ts_end": None}]
        got = derive.candidates(s, g)
        assert len(got) == 1, got
        assert got[0]["turns"] == 274, got[0]
        assert set(got[0]["projects"]) == {"AwakenerUnity", "TheAwakener"}, got[0]


def test_a_session_touching_two_dirs_is_not_double_counted():
    """Its turns belong to one intent, not to each directory it happened to
    write into."""
    with tempfile.TemporaryDirectory() as d:
        g = _goals(os.path.join(d, "goals"))
        s = [{"session_id": "a", "projects": ["wt-one", "wt-two"], "title": "The work",
              "cwd": "/tmp/p", "counts": {"user": 100}, "ts_start": None, "ts_end": None},
             _sess("b", "wt-one", 100, "The work")]
        got = derive.candidates(s, g)
        assert len(got) == 1, got
        assert got[0]["turns"] == 200, got[0]["turns"]


def test_untitled_sessions_still_cluster_by_project():
    with tempfile.TemporaryDirectory() as d:
        g = _goals(os.path.join(d, "goals"))
        s = [_sess("a", "proj", 100, None), _sess("b", "proj", 100, None)]
        got = derive.candidates(s, g)
        assert len(got) == 1 and got[0]["turns"] == 200, got


def test_a_title_shared_by_several_projects_identifies_none_of_them():
    """Live fire, second round: once project attribution actually worked,
    claude-sync / meditate / nidra were all touched by the same sessions, so
    all three inherited the same dominant title and "voice bot voice quality"
    was proposed three times. A title that lands in several buckets is not
    evidence about any one of them: it belongs to the bucket where the effort
    actually is, and the others fall back to their project name."""
    with tempfile.TemporaryDirectory() as d:
        g = _goals(os.path.join(d, "goals"))
        # OVERLAPPING but not identical session sets — the real shape, which
        # exact-set merging cannot collapse
        def S(sid, projects, turns):
            return {"session_id": sid, "projects": projects,
                    "title": "voice bot voice quality", "cwd": "/tmp/x",
                    "counts": {"user": turns}, "ts_start": None, "ts_end": None}
        s = [S("a", ["claude-sync", "meditate"], 300),
             S("b", ["claude-sync", "nidra"], 300),
             S("c", ["meditate", "nidra"], 300)]
        got = derive.candidates(s, g)
        titles = [c["title"] for c in got]
        assert len(titles) == len(set(titles)), "same title proposed twice: %s" % titles
        assert set(titles) <= {"claude-sync", "meditate", "nidra"}, titles


def _main():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("  ok   %s" % fn.__name__)
        except AssertionError as e:
            failed += 1
            print("  FAIL %s: %s" % (fn.__name__, e))
        except Exception as e:
            failed += 1
            print("  ERR  %s: %s: %s" % (fn.__name__, type(e).__name__, e))
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
