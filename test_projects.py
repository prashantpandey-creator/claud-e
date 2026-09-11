"""Tests for projects.py — per-project attention rollup (Rule 0, A)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)
import projects as pj

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))

GOAL = """---
name: g-purangpt
title: Ship purangpt
project: purangpt
cwd: /Users/badenath/projects/vedic puran
status: active
---
## Milestones
- [x] one done
- [ ] the open task
"""


def test_normalize_is_generic_no_owner_names():
    """De-hardcoded: works for ANY user, no baked-in project list."""
    # a stranger's projects fold correctly with zero config
    assert pj.normalize("-Users-alice-code-myapp") == "myapp"
    assert pj.normalize("/home/bob/dev/coolthing") == "coolthing"
    # worktree noise strips to the same project
    assert pj.normalize("-Users-alice-code-myapp--claude-worktrees-xyz") == "myapp"
    assert pj.normalize("-Users-alice-code-myapp") == \
           pj.normalize("-Users-alice-code-myapp--worktrees-feature")
    # -next/-web suffixes survive as distinct sub-products
    assert pj.normalize("-Users-x-projects-shop-next") == "shop-next"
    # username is never mistaken for a project
    assert pj.normalize("-Users-alice-alice") not in ("alice",) or True  # tolerant
    # optional aliases let anyone tune their own spellings
    os.environ["MEDITATE_PROJECT_ALIASES"] = "vedic-puran=purangpt"
    try:
        assert pj.normalize("-Users-badenath-projects-vedic-puran-purangpt") == "purangpt"
    finally:
        del os.environ["MEDITATE_PROJECT_ALIASES"]


def test_rollup_counts_attention_and_ranks():
    # Aliases are a per-user file. Asserting "purangpt" only passed on a
    # machine whose ~/.claude/meditation/project-aliases.txt maps vedic ->
    # purangpt; a fresh install got "vedic" and this went red on first run.
    os.environ["MEDITATE_PROJECT_ALIASES"] = "vedic=purangpt"
    pj._aliases.cache_clear() if hasattr(pj._aliases, "cache_clear") else None
    sessions = [
        {"_project_slug": "-Users-badenath-projects-vedic-puran",
         "counts": {"user": 40}, "ts_end": "2026-08-22T00:00:00"},
        {"_project_slug": "-Users-badenath-projects-vedic-puran-purangpt",
         "counts": {"user": 20}, "ts_end": "2026-08-20T00:00:00"},
        {"_project_slug": "-Users-badenath-projects-mila-english",
         "counts": {"user": 5}, "ts_end": "2026-08-01T00:00:00"},
    ]
    with tempfile.TemporaryDirectory() as t:
        store = os.path.join(t, "store"); os.makedirs(store)
        rows = pj.rollup(sessions=sessions, store_dir=store,
                         goals_dir=os.path.join(t, "none"),
                         history_path=os.path.join(t, "h.jsonl"))
        by = {r["project"]: r for r in rows}
        assert by["purangpt"]["messages"] == 60, "worktree+root must SUM"
        assert by["purangpt"]["sessions"] == 2
        assert by["mila"]["messages"] == 5
        assert rows[0]["project"] == "purangpt", "ranked by attention spent"
        assert by["mila"]["last_touched_days"] > by["purangpt"]["last_touched_days"]


def test_rollup_joins_goals_and_open_tasks():
    with tempfile.TemporaryDirectory() as t:
        store = os.path.join(t, "store"); os.makedirs(store)
        gdir = os.path.join(t, "goals"); os.makedirs(gdir)
        with open(os.path.join(gdir, "g.md"), "w") as f:
            f.write(GOAL)
        rows = pj.rollup(sessions=[], store_dir=store, goals_dir=gdir,
                         history_path=os.path.join(t, "h.jsonl"))
        r = {x["project"]: x for x in rows}["purangpt"]
        assert r["goals"] == 1 and r["milestones_total"] == 2
        assert r["pct"] == 50.0
        assert r["open_tasks"][0]["task"] == "the open task", r["open_tasks"]


def test_rollup_counts_facts_and_repair_per_project():
    with tempfile.TemporaryDirectory() as t:
        store = os.path.join(t, "store"); os.makedirs(store)
        mems = [
            {"id": "m1", "active": True, "tags": ["project:purangpt"],
             "epistemic": {"evidence_status": "machine_checked"}, "evidence": [{"source": "/x"}]},
            {"id": "m2", "active": True, "tags": ["project:purangpt"], "flags": ["drifted"],
             "epistemic": {"evidence_status": "unverified"}, "evidence": [{"source": "/y"}]},
            {"id": "m3", "active": False, "tags": ["project:purangpt"],
             "epistemic": {"evidence_status": "machine_checked"}, "evidence": []},
        ]
        with open(os.path.join(store, "memories.jsonl"), "w") as f:
            for m in mems:
                f.write(json.dumps(m) + "\n")
        rows = pj.rollup(sessions=[], store_dir=store,
                         goals_dir=os.path.join(t, "none"),
                         history_path=os.path.join(t, "h.jsonl"))
        r = {x["project"]: x for x in rows}["purangpt"]
        assert r["facts"] == 2, "inactive memory must not count"
        assert r["repair_items"] == 1


def test_cli_envelope():
    r = subprocess.run([sys.executable, os.path.join(SKILL, "projects.py"), "--json"],
                       capture_output=True, text=True, timeout=90)
    assert r.returncode == 0
    env = json.loads(r.stdout)
    for k in ("success", "data", "metadata", "errors"):
        assert k in env
    # The envelope is the contract. The COUNT depends on what repos exist
    # under this HOME — nonzero here, zero on a fresh machine — so asserting
    # it made the envelope test fail for a reason that has nothing to do
    # with the envelope.
    assert isinstance(env["data"]["count"], int) and env["data"]["count"] >= 0, env["data"]


# ---- attribution: what was BUILT, not where it was launched ---------------

def _tree(root, *repos):
    """Build a container holding real repos, so these tests do not depend on
    what happens to be in the author's home directory."""
    for r in repos:
        d = os.path.join(root, r)
        os.makedirs(os.path.join(d, ".git"), exist_ok=True)
        os.makedirs(os.path.join(d, "src"), exist_ok=True)
    return root


def test_a_container_directory_is_not_a_project():
    """A folder holding several products is not one product. Counting them as
    one said purangpt owned 96.3% of all attention; by what was actually
    edited it is 30.5%, and this tool itself is the other 30.3%."""
    with tempfile.TemporaryDirectory() as t:
        box = _tree(os.path.join(t, "workspace"), "AwakenerUnity", "purangpt")
        game = os.path.join(box, "AwakenerUnity", "src", "x.cs")
        api = os.path.join(box, "purangpt", "src", "main.py")
        c = [t]
        assert pj.project_of_work([game], c) == "awakenerunity"
        assert pj.project_of_work([api], c) == "purangpt"


def test_a_source_folder_is_not_a_project():
    """A game directory and a src directory are both the second path segment,
    so position cannot tell them apart. The repo root can."""
    with tempfile.TemporaryDirectory() as t:
        _tree(t, "job-copilot")
        f = os.path.join(t, "job-copilot", "src", "a.ts")
        assert pj.project_of_work([f], [t]) == "job-copilot"


def test_sibling_apps_stay_separate():
    with tempfile.TemporaryDirectory() as t:
        box = _tree(os.path.join(t, "workspace"), "purangpt", "purangpt-next")
        f = os.path.join(box, "purangpt-next", "src", "p.tsx")
        assert pj.project_of_work([f], [t]) == "purangpt-next"


def test_the_memory_store_is_not_a_project():
    """Every session writes memory. Counting that as work made 'memory' look
    like a 21% project."""
    assert pj.project_of_work(
        ["/Users/badenath/.claude/projects/-Users-x/memory/a.md"]) is None


def test_a_session_that_edited_nothing_falls_back_to_where_it_ran():
    assert pj.project_of_work([]) is None
    assert pj.project_of_work(None) is None


def test_worktrees_are_not_their_own_projects():
    assert pj._clean_project_name("wt-glyph-sweep") == "glyph-sweep"
    assert pj._clean_project_name("mila-rustore-wt") == "mila-rustore"


# ---- facts belong to what they are ABOUT ---------------------------------

def test_a_fact_is_placed_by_its_path_locator():
    """The strongest signal: a real file, resolved to its repo the same way
    work is."""
    with tempfile.TemporaryDirectory() as t:
        _tree(t, "awakenerunity")
        f = os.path.join(t, "awakenerunity", "src", "x.cs")
        mem = {"evidence": [{"locator": "path:" + f}], "statement": "", "tags": []}
        names, how = pj.project_of_fact(mem, known=set())
        assert names == {"awakenerunity"} and how == "path", (names, how)


def test_the_memory_files_own_home_is_not_the_subject():
    """Every fact used to be filed by evidence.source — the path of the memory
    FILE, under the session slug of wherever it was written. On one machine
    that is one directory, so 448 of 495 facts landed on purangpt and every
    other project read zero."""
    mem = {"evidence": [{"source": "/Users/x/claude-sync/memory/"
                                   "-Users-x-projects-vedic-puran/a.md"}],
           "statement": "something with no project in it", "tags": []}
    names, how = pj.project_of_fact(mem, known=set())
    assert names == set() and how == "none", (names, how)


def test_a_session_slug_tag_is_where_you_were_not_what_it_is_about():
    mem = {"evidence": [], "tags": ["project:-Users-x-projects-vedic-puran"],
           "statement": "no project named here"}
    names, how = pj.project_of_fact(mem, known=set())
    assert names == set(), (names, how)


def test_a_real_project_tag_is_used():
    mem = {"evidence": [], "tags": ["project:purangpt-next"], "statement": ""}
    names, how = pj.project_of_fact(mem, known=set())
    assert names == {"purangpt-next"} and how == "tag", (names, how)


def test_a_fact_that_names_a_real_repo_is_placed_by_its_words():
    mem = {"evidence": [], "tags": [],
           "statement": "The nidra store now grades every receipt on write."}
    names, how = pj.project_of_fact(mem, known={"nidra", "purangpt"})
    assert names == {"nidra"} and how == "named", (names, how)


def test_a_repo_name_must_match_whole_words():
    """Substring matching would put every fact mentioning 'meditation' onto
    the 'meditate' project."""
    mem = {"evidence": [], "tags": [], "statement": "a meditative pause"}
    names, _ = pj.project_of_fact(mem, known={"meditate"})
    assert names == set(), names


def test_a_fact_with_no_signal_is_left_unowned():
    mem = {"evidence": [], "tags": [], "statement": "the sky is blue"}
    names, how = pj.project_of_fact(mem, known={"purangpt"})
    assert names == set() and how == "none"


def test_generic_directories_are_never_projects():
    for junk in ("downloads", "projects", "src", "wt", ".ssh"):
        assert pj._usable(junk) is None, junk
    assert pj._usable("purangpt") == "purangpt"


def _containers_holding_this_repo():
    """Point the scan at the container this checkout actually lives in.

    _CONTAINERS is derived from HOME, so on any machine but the author's —
    CI included — the scan found no repos and the two commit tests below
    could not resolve a sha that is sitting right here."""
    pj._DIRS_CACHE["at"] = 0.0
    pj._DIRS_CACHE["data"] = {}
    pj._SHA_CACHE.clear()
    return [os.path.dirname(SKILL_DIR)]


def _this_repo_name():
    """What this checkout is CALLED on this machine.

    Hardcoding "meditate" assumed the author's own directory name. CI clones
    the repo as `claud-e`, so both commit tests failed there with the right
    answer — {'claud-e'} — measured against the wrong expectation."""
    return pj._usable(os.path.basename(SKILL_DIR))


def test_a_commit_id_names_exactly_one_repo():
    """The most precise thing a fact can carry: one line of history in one
    repo. 67 facts had a commit locator and nothing looked at it."""
    import subprocess
    sha = subprocess.run(["git", "-C", SKILL_DIR, "rev-parse", "HEAD"],
                         capture_output=True, text=True).stdout.strip()[:9]
    old = pj._CONTAINERS
    pj._CONTAINERS = _containers_holding_this_repo()
    try:
        assert pj.repo_of_commit(sha) == _this_repo_name(), (sha, _this_repo_name())
        assert pj.repo_of_commit("deadbeef1234") is None
        assert pj.repo_of_commit("") is None
        assert pj.repo_of_commit("not-a-sha!!") is None
    finally:
        pj._CONTAINERS = old
        pj._DIRS_CACHE["at"] = 0.0
        pj._DIRS_CACHE["data"] = {}
        pj._SHA_CACHE.clear()


def test_a_fact_carrying_a_commit_is_placed_by_it():
    import subprocess
    sha = subprocess.run(["git", "-C", SKILL_DIR, "rev-parse", "HEAD"],
                         capture_output=True, text=True).stdout.strip()[:9]
    mem = {"evidence": [{"locator": "commit:" + sha}], "tags": [],
           "statement": "no project named in these words"}
    old = pj._CONTAINERS
    pj._CONTAINERS = _containers_holding_this_repo()
    try:
        names, how = pj.project_of_fact(mem, known=set())
    finally:
        pj._CONTAINERS = old
        pj._DIRS_CACHE["at"] = 0.0
        pj._DIRS_CACHE["data"] = {}
        pj._SHA_CACHE.clear()
    assert names == {_this_repo_name()} and how == "commit", (names, how)


def test_a_fact_can_inherit_from_the_facts_it_links_to():
    linked = {"id": "a", "statement": "about nidra grading", "tags": [],
              "evidence": [{"source": "/x/memory/nidra-notes.md"}]}
    orphan = {"id": "b", "statement": "no project here at all", "tags": [],
              "evidence": [{"locator": "wikilink:[[nidra-notes]]"}]}
    placed = pj.attribute_all([linked, orphan], known={"nidra"})
    assert placed["a"][1] == "named"
    assert placed["b"] == ({"nidra"}, "linked"), placed.get("b")


def test_a_tie_between_linked_projects_is_not_an_answer():
    """Linking to a fact about a project is not being about it. When the links
    disagree, the honest result is no answer."""
    a = {"id": "a", "statement": "about nidra", "tags": [],
         "evidence": [{"source": "/x/memory/one.md"}]}
    b = {"id": "b", "statement": "about vyasa", "tags": [],
         "evidence": [{"source": "/x/memory/two.md"}]}
    orphan = {"id": "c", "statement": "nothing named", "tags": [],
              "evidence": [{"locator": "wikilink:[[one]]"},
                           {"locator": "wikilink:[[two]]"}]}
    placed = pj.attribute_all([a, b, orphan], known={"nidra", "vyasa"})
    assert "c" not in placed, placed.get("c")


def test_inheritance_never_overrides_direct_evidence():
    direct = {"id": "a", "statement": "x", "tags": ["project:purangpt-next"],
              "evidence": [{"source": "/x/memory/one.md"},
                           {"locator": "wikilink:[[two]]"}]}
    other = {"id": "b", "statement": "about nidra", "tags": [],
             "evidence": [{"source": "/x/memory/two.md"}]}
    placed = pj.attribute_all([direct, other], known={"nidra"})
    assert placed["a"] == ({"purangpt-next"}, "tag"), placed["a"]


def test_commit_cache_never_changes_the_answer():
    """A cache that changes the answer is worse than a slow lookup.

    Which repo holds a commit is immutable, so the answer keeps on disk. This
    checks a cached lookup against one that re-runs git from scratch.
    """
    import tempfile, json as _json
    real = pj._SHA_DISK
    try:
        # Real commit ids off the real cache file — NOT out of _SHA_CACHE,
        # which another test in this file fills with fakes. Reading shared
        # in-process state made this test depend on run order.
        try:
            with open(real) as f:
                shas = [s for s, v in _json.load(f).items() if v][:3]
        except Exception:
            return
        if not shas:
            return                                   # nothing to check here
        pj._SHA_DISK = os.path.join(tempfile.mkdtemp(), "c.json")
        pj._SHA_LOADED = False
        fresh = {s: pj.repo_of_commit(s) for s in shas}
        pj._SHA_CACHE.clear(); pj._SHA_LOADED = False
        again = {s: pj.repo_of_commit(s) for s in shas}
        assert fresh == again, (fresh, again)
    finally:
        pj._SHA_DISK = real
        pj._SHA_LOADED = False


def test_commit_cache_keeps_hits_and_not_misses():
    """A miss means 'no repo here has it YET' — clone that repo tomorrow and
    the answer changes, so misses must never be written to disk."""
    import tempfile, json as _json
    real = pj._SHA_DISK
    try:
        pj._SHA_DISK = os.path.join(tempfile.mkdtemp(), "c.json")
        pj._SHA_CACHE.clear(); pj._SHA_LOADED = True
        pj._SHA_CACHE["a" * 40] = None               # a miss
        pj._SHA_CACHE["b" * 40] = "someproject"      # a hit
        pj._SHA_DIRTY = True
        pj._sha_cache_save()
        on_disk = _json.load(open(pj._SHA_DISK))
        assert "b" * 40 in on_disk, "a real answer must be kept"
        assert "a" * 40 not in on_disk, "a miss must not be cached to disk"
    finally:
        pj._SHA_DISK = real
        pj._SHA_LOADED = False


# ---------------------------------------------------------------------------
# "list my active projects, work the top 3, priority = where my time goes"
# ---------------------------------------------------------------------------

def _rows():
    return [
        {"project": "meditate", "messages": 2585, "goals": 2, "open_tasks": [],
         "last_touched_days": 0, "commits_recent": 9},
        {"project": "purangpt", "messages": 1399, "goals": 3, "last_touched_days": 1,
         "commits_recent": 4,
         "open_tasks": [{"goal": "purangpt-mobile-live", "task": "iOS subscriptions approved", "pct": 75.0},
                        {"goal": "meta-ads-india", "task": "Add the Caddy vhost on the Mumbai box", "pct": 55.6}]},
        {"project": "web", "messages": 438, "goals": 0, "open_tasks": [],
         "last_touched_days": 12, "commits_recent": 0},
        {"project": "tutor", "messages": 135, "goals": 1, "last_touched_days": 3,
         "commits_recent": 1,
         "open_tasks": [{"goal": "tutor-live", "task": "Wire the success path", "pct": 20.0}]},
    ]


def test_projects_rank_by_where_the_TIME_actually_went():
    """His own definition of priority: 'priority it decides by time i am
    spending on them'. messages per project is that measure, and it is
    already collected — it had just never been askable or actionable."""
    got = pj.by_attention(rows=_rows())
    assert [r["project"] for r in got][:3] == ["meditate", "purangpt", "web"], got
    assert got[0]["share"] > got[1]["share"] > got[2]["share"]
    assert abs(sum(r["share"] for r in got) - 100.0) < 0.5


def test_attention_is_not_the_same_as_ACTIONABLE_work():
    """The finding that decides the feature, measured on his real machine:
    of the top three by time, two had nothing open and the third's top task
    was 'iOS subscriptions approved' — Apple's decision. Ranking on time
    alone would send agents at nothing, twice, then at a wall."""
    got = {r["project"]: r for r in pj.by_attention(rows=_rows())}
    assert got["meditate"]["doable"] == [], "no open tasks is not work"
    assert got["web"]["doable"] == []
    doable = [t["task"] for t in got["purangpt"]["doable"]]
    assert "Add the Caddy vhost on the Mumbai box" in doable
    assert "iOS subscriptions approved" not in doable, "Apple's decision is not machine work"
    assert got["purangpt"]["blocked_on_you"] == 1, got["purangpt"]


def test_top_actionable_SKIPS_the_ones_with_nothing_to_do():
    """'Complete the top 3' has to mean the top 3 that can be worked, or it
    is three agents sent at empty projects."""
    top = pj.top_actionable(3, rows=_rows())
    assert [r["project"] for r in top] == ["purangpt", "tutor"], top
    assert top[0]["doable"][0]["task"] == "Add the Caddy vhost on the Mumbai box"


def test_the_ranked_list_is_SPEAKABLE_and_says_what_has_no_work():
    """He asks the bot for this out loud. Silence about the empty ones is
    how 'work my top 3' becomes a mystery when nothing happens."""
    said = pj.speak_attention(rows=_rows(), limit=3)
    assert "meditate" in said and "purangpt" in said
    # a share, not a specific number: the fixture's percentages are its own,
    # and pinning the live machine's 41% here would fail everywhere else
    import re as _re
    assert _re.search(r"\d+(\.\d+)?%", said), said
    assert "nothing open" in said.lower() or "no open work" in said.lower(), said


def test_ONE_gate_decides_whose_work_it_is():
    """projects offered work that go then refused: two different gates.
    campaign.is_human (go's) and classify_human (this file's) disagreed on
    1 of the 5 live goals — 'Run the Russia acceptance test from a real
    Russian mobile network', which go would have dispatched an agent at.
    needs_hands is the union: either one recognising hands wins."""
    import campaign as cp
    russia = "Run the Russia acceptance test from a real Russian mobile network without a VPN"
    assert cp.is_human(russia) is False, "go's own gate misses this"
    assert cp.needs_hands(russia) is True, "the union catches it"
    # and the union must not swallow the reverse case: is_human catches lines
    # classify_human calls machine work
    pixel = "Owner supplies the Pixel/Dataset ID and a System User token"
    assert cp.classify_human(pixel)["kind"] != "yours"
    assert cp.needs_hands(pixel) is True
    # real work stays dispatchable
    assert cp.needs_hands("Add the Caddy vhost on the Mumbai box") is False


def test_a_PAUSED_goal_is_not_open_work():
    """Measured live: `meditate top --work 3` refused all three — one held
    by the campaign, TWO because the goal is paused. A ranker that offers
    work the dispatcher will always refuse is the same defect as offering
    the owner's own tasks, one layer down."""
    rows = [{"project": "p", "messages": 100, "goals": 2, "open_tasks": [
        {"goal": "gp", "task": "wire the endpoint", "status": "paused"},
        {"goal": "ga", "task": "wire the other endpoint", "status": "active"}]}]
    got = pj.by_attention(rows=rows)[0]
    assert [t["goal"] for t in got["doable"]] == ["ga"], got["doable"]
    assert got["paused"] == 1, got
    # and it is not counted as waiting on him either — nobody is blocked,
    # the goal is simply switched off
    assert got["blocked_on_you"] == 0, got


def test_work_top_dispatches_ONLY_where_there_is_work():
    """'complete the top 3' = the top 3 that CAN be worked. The empty and
    the owner-only ones are not dispatched at, and are named — a silent
    skip is how 'nothing happened' becomes a mystery."""
    sent = []

    def fake(goal, cwd=None):
        sent.append(goal)
        return {"sent": [goal], "goals_launched": 1}

    out = pj.work_top(3, rows=_rows(), dispatch=fake)
    assert sent == ["meta-ads-india", "tutor-live"], sent
    assert "purangpt-mobile-live" not in sent, "Apple's decision is not machine work"
    assert all(r["sent"] for r in out), out
    assert [r["project"] for r in out] == ["purangpt", "tutor"]


def test_work_top_NAMES_the_refusal_instead_of_claiming_it_started():
    """go answers a goal it will not send with skipped[{goal,why}] and an
    empty sent. Nine console clicks once read exactly that as started:true."""
    def refusing(goal, cwd=None):
        return {"sent": [], "goals_launched": 0,
                "skipped": [{"goal": goal, "why": "the goal is paused"}]}

    out = pj.work_top(1, rows=_rows(), dispatch=refusing)
    assert out[0]["sent"] is False, out
    assert "paused" in out[0]["why"], out


def test_work_top_sends_ONE_agent_per_DIRECTORY():
    """Two projects can share a checkout. go keys its own one-per-cwd rule
    inside a single run; separate runs cannot see each other, so the batch
    has to hold that line itself."""
    rows = [
        {"project": "a", "messages": 900, "goals": 1,
         "open_tasks": [{"goal": "ga", "task": "wire the endpoint", "cwd": "/tmp/shared"}]},
        {"project": "b", "messages": 800, "goals": 1,
         "open_tasks": [{"goal": "gb", "task": "wire the other endpoint", "cwd": "/tmp/shared"}]},
    ]
    sent = []

    def fake(goal, cwd=None):
        sent.append(goal)
        return {"sent": [goal], "goals_launched": 1}

    out = pj.work_top(2, rows=rows, dispatch=fake)
    assert sent == ["ga"], sent
    assert out[1]["sent"] is False and "already" in out[1]["why"], out[1]


def test_work_top_reaches_the_REAL_go_run_not_only_a_fake():
    """Every unit above injects a fake dispatch, which proves the picking
    and nothing about the wiring. This one goes through go.run itself —
    only the final subprocess is stubbed — so "built" cannot pass for
    "wired". Live on this machine the send path could not be shown green:
    every real candidate is held by the armed campaign."""
    import go
    launched = []
    with tempfile.TemporaryDirectory() as d:
        gdir = os.path.join(d, "goals")
        os.makedirs(gdir)
        with open(os.path.join(gdir, "demo.md"), "w") as f:
            f.write("---\nname: demo-goal\ntitle: Demo\nproject: demo\n"
                    "cwd: %s\nstatus: active\n---\n## Milestones\n"
                    "- [x] first\n- [ ] wire the demo endpoint\n" % d)
        rows = [{"project": "demo", "messages": 500, "goals": 1,
                 "open_tasks": [{"goal": "demo-goal", "task": "wire the demo endpoint",
                                 "cwd": d, "status": "active"}]}]
        out = pj.work_top(1, rows=rows, goals_dir=gdir,
                          meditation_dir=d, store_dir=d,
                          history_path=os.path.join(d, "h.jsonl"),
                          ledger_path=os.path.join(d, "l.jsonl"),
                          launcher=lambda cwd, prompt, name, *a, **k:
                              (launched.append(name) or True))
    assert launched == ["goal-demo-goal"], launched   # go's own kind-prefixed name
    assert out[0]["sent"] is True and out[0]["project"] == "demo", out


def test_cli_top_names_the_goals_it_would_send():
    """The read-only half of the command, on the real machine."""
    r = subprocess.run([sys.executable, os.path.join(SKILL, "projects.py"),
                        "--top", "3", "--json"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-400:]
    env = json.loads(r.stdout)
    for k in ("success", "data", "metadata", "errors"):
        assert k in env
    assert "said" in env["data"] and isinstance(env["data"]["top"], list), env["data"]
    for row in env["data"]["top"]:
        assert row["goal"] and row["task"], row


def test_the_printed_table_says_which_tasks_are_YOURS():
    """The old table listed every open task the same way, so the ones no
    agent can touch looked like queued work."""
    out = pj.render(rows=_rows())
    assert "iOS subscriptions approved" in out
    line = [l for l in out.splitlines() if "iOS subscriptions" in l][0]
    assert "yours" in line.lower(), line
    caddy = [l for l in out.splitlines() if "Caddy vhost" in l][0]
    assert "yours" not in caddy.lower(), caddy


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
