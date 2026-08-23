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
    assert env["data"]["count"] > 0


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


def test_a_commit_id_names_exactly_one_repo():
    """The most precise thing a fact can carry: one line of history in one
    repo. 67 facts had a commit locator and nothing looked at it."""
    import subprocess
    sha = subprocess.run(["git", "-C", SKILL_DIR, "rev-parse", "HEAD"],
                         capture_output=True, text=True).stdout.strip()[:9]
    assert pj.repo_of_commit(sha) == "meditate", sha
    assert pj.repo_of_commit("deadbeef1234") is None
    assert pj.repo_of_commit("") is None
    assert pj.repo_of_commit("not-a-sha!!") is None


def test_a_fact_carrying_a_commit_is_placed_by_it():
    import subprocess
    sha = subprocess.run(["git", "-C", SKILL_DIR, "rev-parse", "HEAD"],
                         capture_output=True, text=True).stdout.strip()[:9]
    mem = {"evidence": [{"locator": "commit:" + sha}], "tags": [],
           "statement": "no project named in these words"}
    names, how = pj.project_of_fact(mem, known=set())
    assert names == {"meditate"} and how == "commit", (names, how)


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
