"""Mined goals: work the owner is doing that no goal file carries.

The failure this pins (2026-09-03): 49 sessions touched the Meta ads
campaign, two memory files recorded its state and its blockers, and the
all-goals run could not see any of it — the campaign builds from goal files
alone, and nobody had written one. "I was working on meta ad campaigns,
that is not visible in mined goals."

A candidate is a `type: project` memory whose words appear in no goal file,
ranked by whether its description carries an open marker and by how many
recent sessions touch it. Accepting one writes a goal file the planner
picks up on the next plan. Deterministic, no model call, no money.

Run: python3 ~/.claude/skills/meditate/test_mine.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)

import goals as gl  # noqa: E402

MEM_COVERED = """---
name: mila-subscription-system
description: ✅ entitlements live, flag off; Phase 2 blocked on Stripe
metadata:
  type: project
---
Mila subscriptions. Related [[mila-live]].
"""
MEM_OPEN = """---
name: meta-ads-campaign-setup
description: "Meta (FB/IG) ad campaign for India — instrumentation SHIPPED 2026-08-15, ⚠️ waiting on owner's Pixel ID + System User token"
metadata:
  type: project
---
Activation = set NEXT_PUBLIC_META_PIXEL_ID in /root/stack.env + redeploy.
Blocked on owner: Pixel ID, System User token. cwd hint: /Users/x/projects/purangpt-next
"""
MEM_REFERENCE = """---
name: some-dashboard-url
description: where the grafana lives
metadata:
  type: reference
---
https://example
"""
GOAL_MILA = """---
name: mila-live
title: Mila live
project: mila
cwd: /tmp
status: active
---
## Milestones
- [x] entitlements shipped
- [ ] subscription phase 2
"""


def _world(t):
    mem = os.path.join(t, "memory"); os.makedirs(mem)
    open(os.path.join(mem, "mila-subscription-system.md"), "w").write(MEM_COVERED)
    open(os.path.join(mem, "meta-ads-campaign-setup.md"), "w").write(MEM_OPEN)
    open(os.path.join(mem, "some-dashboard-url.md"), "w").write(MEM_REFERENCE)
    gdir = os.path.join(t, "goals"); os.makedirs(gdir)
    open(os.path.join(gdir, "mila-live.md"), "w").write(GOAL_MILA)
    sessions = [
        {"title": "meta ads pixel wiring", "ts_end": "2026-09-01T10:00:00Z",
         "user_messages": ["set up the meta pixel", "system user token"]},
        {"title": "meta ads creative", "ts_end": "2026-08-30T10:00:00Z", "user_messages": []},
        {"title": "old thing", "ts_end": "2026-01-01T10:00:00Z", "user_messages": ["meta ads"]},
        {"title": "mila subs", "ts_end": "2026-09-02T10:00:00Z", "user_messages": []},
    ]
    return mem, gdir, sessions


def test_an_UNCOVERED_project_memory_is_a_candidate():
    with tempfile.TemporaryDirectory() as t:
        mem, gdir, sessions = _world(t)
        c = gl.mine(memory_dir=mem, goals_dir=gdir, sessions=sessions, now="2026-09-03T00:00:00Z")
        names = [x["name"] for x in c]
        assert "meta-ads-campaign-setup" in names, names


def test_a_memory_a_goal_already_COVERS_is_not_proposed():
    """mila-subscription-system's words are in mila-live.md. Proposing it
    would be the planner nagging about a goal the owner already wrote."""
    with tempfile.TemporaryDirectory() as t:
        mem, gdir, sessions = _world(t)
        c = gl.mine(memory_dir=mem, goals_dir=gdir, sessions=sessions, now="2026-09-03T00:00:00Z")
        assert "mila-subscription-system" not in [x["name"] for x in c], c


def test_only_PROJECT_memories_are_candidates():
    """A reference memory is a bookmark, not work."""
    with tempfile.TemporaryDirectory() as t:
        mem, gdir, sessions = _world(t)
        c = gl.mine(memory_dir=mem, goals_dir=gdir, sessions=sessions, now="2026-09-03T00:00:00Z")
        assert "some-dashboard-url" not in [x["name"] for x in c], c


def test_the_candidate_carries_its_EVIDENCE():
    """Sessions in the last 30 days that touch it, the open marker, the
    memory it came from — the owner accepts a fact, not a hunch."""
    with tempfile.TemporaryDirectory() as t:
        mem, gdir, sessions = _world(t)
        c = [x for x in gl.mine(memory_dir=mem, goals_dir=gdir, sessions=sessions,
                                now="2026-09-03T00:00:00Z")
             if x["name"] == "meta-ads-campaign-setup"][0]
        ev = c["evidence"]
        assert ev["sessions_30d"] == 2, ev      # the January one is out of window
        assert ev["open_signal"] is True, ev    # the ⚠️ / "waiting on"
        assert ev["memory"].endswith("meta-ads-campaign-setup.md")
        assert c["title"].startswith("Meta (FB/IG) ad campaign"), c["title"]
        assert c["suggested_milestones"], c
        assert c["cwd"] == "/Users/x/projects/purangpt-next", c["cwd"]


def test_accepting_writes_a_goal_the_scanner_PARSES():
    with tempfile.TemporaryDirectory() as t:
        mem, gdir, sessions = _world(t)
        c = [x for x in gl.mine(memory_dir=mem, goals_dir=gdir, sessions=sessions,
                                now="2026-09-03T00:00:00Z")
             if x["name"] == "meta-ads-campaign-setup"][0]
        r = gl.accept_mined(c, goals_dir=gdir)
        assert r["ok"] and os.path.exists(r["path"]), r
        rows = {g["name"]: g for g in gl.scan(gdir, history_path=os.path.join(t, "h.jsonl"))}
        assert "meta-ads-campaign-setup" in rows, list(rows)
        g = rows["meta-ads-campaign-setup"]
        assert g["total"] >= 1 and g["done"] == 0, g
        assert "mined" in open(r["path"]).read().lower()
        # accepting twice does not clobber the owner's edits
        open(r["path"], "a").write("- [ ] owner added this\n")
        r2 = gl.accept_mined(c, goals_dir=gdir)
        assert r2["ok"] is False and "exists" in r2["why"], r2
        assert "owner added this" in open(r["path"]).read()


def test_mine_on_the_REAL_store_returns_without_error():
    c = gl.mine()
    assert isinstance(c, list)
    for x in c[:3]:
        assert x["name"] and x["evidence"]["memory"]


def test_a_thread_with_NO_memory_still_surfaces_from_sessions():
    """Organic capture while searching: two or more recent sessions sharing
    a subject that no goal and no memory names is a candidate on its own —
    the tool must not depend on somebody having written a memory first."""
    with tempfile.TemporaryDirectory() as t:
        mem, gdir, sessions = _world(t)
        sessions = sessions + [
            {"title": "linkedin outreach tool scraper", "ts_end": "2026-09-02T10:00:00Z",
             "user_messages": ["build the linkedin outreach scraper", "linkedin api limits"]},
            {"title": "linkedin outreach followups", "ts_end": "2026-08-28T10:00:00Z",
             "user_messages": ["outreach message templates for linkedin"]},
        ]
        c = gl.mine(memory_dir=mem, goals_dir=gdir, sessions=sessions, now="2026-09-03T00:00:00Z")
        hit = [x for x in c if "linkedin" in x["name"]]
        assert hit, [x["name"] for x in c]
        ev = hit[0]["evidence"]
        assert ev["sessions_30d"] == 2 and ev.get("memory") == "" and ev.get("source") == "sessions", ev
        assert "outreach" in hit[0]["title"].lower()


def test_a_single_session_is_NOT_a_thread():
    """One session on a subject is a visit; two is a thread. Below two the
    list would be every session title on the machine."""
    with tempfile.TemporaryDirectory() as t:
        mem, gdir, sessions = _world(t)
        sessions = sessions + [{"title": "random one-off spike", "ts_end": "2026-09-02T10:00:00Z",
                                "user_messages": ["try the random spike"]}]
        c = gl.mine(memory_dir=mem, goals_dir=gdir, sessions=sessions, now="2026-09-03T00:00:00Z")
        assert not any("random" in x["name"] for x in c), [x["name"] for x in c]


# ---------------------------------------------------------------------------
# discard: the owner says no, and it never comes back
# ---------------------------------------------------------------------------

def test_discarding_a_mined_goal_MOVES_its_memory_and_it_never_resurfaces():
    """Not deleted — moved to .discarded so it can be put back — and written
    to a ledger the miner reads first, so no session or memory can bring
    it up again."""
    with tempfile.TemporaryDirectory() as t:
        mem, gdir, sessions = _world(t)
        led = os.path.join(t, "discarded.jsonl")
        c = [x for x in gl.mine(memory_dir=mem, goals_dir=gdir, sessions=sessions,
                                now="2026-09-03T00:00:00Z", ledger=led)
             if x["name"] == "meta-ads-campaign-setup"][0]
        r = gl.discard_mined(c, memory_dir=mem, ledger=led, store_dir=None, reason="not now")
        assert r["ok"], r
        assert not os.path.exists(os.path.join(mem, "meta-ads-campaign-setup.md"))
        assert os.path.exists(os.path.join(mem, ".discarded", "meta-ads-campaign-setup.md"))
        again = gl.mine(memory_dir=mem, goals_dir=gdir, sessions=sessions,
                        now="2026-09-03T00:00:00Z", ledger=led)
        assert "meta-ads-campaign-setup" not in [x["name"] for x in again]
        row = json.loads(open(led).read().splitlines()[-1])
        assert row["name"] == "meta-ads-campaign-setup" and row["reason"] == "not now"


def test_discarding_TOMBSTONES_the_store_rows_that_cite_the_memory():
    """The graded store keeps serving a memory the owner discarded unless
    its rows go inactive. Rows are found by the evidence source path."""
    with tempfile.TemporaryDirectory() as t:
        mem, gdir, sessions = _world(t)
        store = os.path.join(t, "store"); os.makedirs(store)
        src = os.path.join(mem, "meta-ads-campaign-setup.md")
        rows = [{"id": "mem_1", "statement": "x", "active": True, "flags": [],
                 "evidence": [{"source": src, "locator": "wikilink:x"}]},
                {"id": "mem_2", "statement": "y", "active": True, "flags": [],
                 "evidence": [{"source": os.path.join(mem, "other.md")}]}]
        open(os.path.join(store, "memories.jsonl"), "w").write(
            "".join(json.dumps(r) + "\n" for r in rows))
        c = {"name": "meta-ads-campaign-setup", "evidence": {"memory": src}}
        r = gl.discard_mined(c, memory_dir=mem, ledger=os.path.join(t, "d.jsonl"),
                             store_dir=store, reason="")
        assert r["ok"] and r["tombstoned"] == 1, r
        back = [json.loads(l) for l in open(os.path.join(store, "memories.jsonl"))]
        by = {x["id"]: x for x in back}
        assert by["mem_1"]["active"] is False and "discarded" in by["mem_1"]["flags"]
        assert by["mem_2"]["active"] is True


def test_a_session_thread_can_be_discarded_too():
    """No memory to move — the ledger alone keeps it from coming back."""
    with tempfile.TemporaryDirectory() as t:
        mem, gdir, sessions = _world(t)
        led = os.path.join(t, "d.jsonl")
        sessions = sessions + [
            {"title": "linkedin outreach tool scraper", "ts_end": "2026-09-02T10:00:00Z", "user_messages": []},
            {"title": "linkedin outreach followups", "ts_end": "2026-08-28T10:00:00Z", "user_messages": []}]
        c = [x for x in gl.mine(memory_dir=mem, goals_dir=gdir, sessions=sessions,
                                now="2026-09-03T00:00:00Z", ledger=led) if "linkedin" in x["name"]][0]
        r = gl.discard_mined(c, memory_dir=mem, ledger=led, store_dir=None)
        assert r["ok"] and r["moved"] == "", r
        again = gl.mine(memory_dir=mem, goals_dir=gdir, sessions=sessions,
                        now="2026-09-03T00:00:00Z", ledger=led)
        assert not any("linkedin" in x["name"] for x in again)


def test_discarding_a_GOAL_moves_its_file_and_the_memories_it_cites():
    with tempfile.TemporaryDirectory() as t:
        mem, gdir, sessions = _world(t)
        gp = os.path.join(gdir, "meta-ads-india.md")
        open(gp, "w").write("---\nname: meta-ads-india\ntitle: Meta\nproject: p\ncwd: /tmp\nstatus: active\n---\n"
                            "## Milestones\n- [ ] x\n\n## Note\nSeeded from the memory index (meta-ads-campaign-setup).\n")
        r = gl.discard_goal("meta-ads-india", goals_dir=gdir, memory_dir=mem,
                            ledger=os.path.join(t, "d.jsonl"), store_dir=None, reason="done with it")
        assert r["ok"], r
        assert not os.path.exists(gp) and os.path.exists(os.path.join(gdir, ".discarded", "meta-ads-india.md"))
        assert "meta-ads-campaign-setup.md" in " ".join(r["memories_moved"]), r
        assert not os.path.exists(os.path.join(mem, "meta-ads-campaign-setup.md"))
        assert "meta-ads-india" not in [g["name"] for g in gl.scan(gdir, history_path=os.path.join(t, "h.jsonl"))]


def test_restore_puts_it_BACK():
    with tempfile.TemporaryDirectory() as t:
        mem, gdir, sessions = _world(t)
        led = os.path.join(t, "d.jsonl")
        c = [x for x in gl.mine(memory_dir=mem, goals_dir=gdir, sessions=sessions,
                                now="2026-09-03T00:00:00Z", ledger=led) if x["name"] == "meta-ads-campaign-setup"][0]
        gl.discard_mined(c, memory_dir=mem, ledger=led, store_dir=None)
        r = gl.restore_discarded("meta-ads-campaign-setup", memory_dir=mem, goals_dir=gdir, ledger=led)
        assert r["ok"], r
        assert os.path.exists(os.path.join(mem, "meta-ads-campaign-setup.md"))
        again = gl.mine(memory_dir=mem, goals_dir=gdir, sessions=sessions, now="2026-09-03T00:00:00Z", ledger=led)
        assert "meta-ads-campaign-setup" in [x["name"] for x in again]


def _main():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
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
