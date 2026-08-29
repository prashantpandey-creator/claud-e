"""Tests for projects.assessment_gaps — is meditate actually judging your work?

WHY (measured on the real machine 2026-08-25):

`projects.py` tracked 83 entries. Two things were wrong with that number and
neither was visible from the report:

1. NOT ALL OF THEM ARE PROJECTS. normalize() derives a name from the first
   real path segment, so a session opened in ~/.claude/skills or
   ~/claude-sync produced a "project" called `skills` (37 sessions) or
   `claude-sync` (27) — more sessions than most real products. `private`,
   `web` and `other` were in the list too. `other` is normalize()'s own
   fallback for "I could not name this", which had been sitting in the
   project table as if it were a product.

2. ALMOST NOTHING IS ASSESSED. 79 of 83 entries had ZERO goals. There are 6
   goal files covering 3 products (purangpt, meditate, mila). Where a goal
   DOES exist the assessment is good — purangpt-mobile-live reads 6/8 with
   live-verified evidence on every box. The defect is not judgement quality,
   it is that ~20 real products have no yardstick at all, so the tool is
   silent about most of the work and that silence looks like health.

The fix is NOT to hand-write alias lines and goal files. That is the same
mistake as hand-typing a symlink: it fixes one machine, teaches nobody, and
nothing re-checks it. The tool reports the gap; the owner decides which
products deserve a goal.

Run: python3 ~/.claude/skills/meditate/test_assessment_gaps.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import projects  # noqa: E402


def _p(name, sessions=0, goals=0, facts=0, days=1.0):
    return {"project": name, "sessions": sessions, "goals": goals,
            "facts": facts, "last_touched_days": days}


# ---------------------------------------------------------------------------
# not-a-project detection
# ---------------------------------------------------------------------------

def test_a_declared_container_is_not_a_project():
    """`skills` had 37 sessions — more than any real product except purangpt —
    purely because normalize() takes the first path segment and ~/.claude/skills
    is a CONTAINER. It is already declared as one in _CONTAINERS; that config
    is the source, not a hardcoded name list."""
    gaps = projects.assessment_gaps([_p("skills", sessions=37),
                                     _p("purangpt", sessions=89, goals=2)])
    names = {g["project"] for g in gaps["not_projects"]}
    assert "skills" in names, names
    assert "purangpt" not in names


def test_the_not_a_project_rule_uses_NO_hardcoded_names():
    """The first cut of this was 18 literal names — the author's own machine
    written into the tool. On another layout those names are wrong AND the
    real containers are missing. Every rule must derive from _CONTAINERS /
    _NOT_WORK or from shape, never from a name someone typed."""
    import inspect
    src = inspect.getsource(projects._is_product) + inspect.getsource(projects._container_names)
    for leaked in ("skills", "downloads", "library", "claude-sync", "desktop", "plugins"):
        assert '"%s"' % leaked not in src and "'%s'" % leaked not in src, \
            "%r is hardcoded — it is this machine's layout, not a rule" % leaked


def test_an_unknown_name_is_TRUSTED_as_a_product():
    """FALSIFIER for the rule that was removed.

    A "name must resolve to a real directory" rule was tried and cut: it
    called purangpt a fragment (nested at ~/projects/vedic puran/purangpt)
    and still missed puranastro (under ~/.scratch-worktrees/). Trusting an
    unrecognised name is the safe direction — a stray entry in the list costs
    one glance; a real product silently dropped from assessment costs the
    thing the tool exists for."""
    gaps = projects.assessment_gaps([_p("zzz-some-new-product", sessions=4)])
    assert gaps["not_projects"] == [], "an unrecognised name was dropped as a fragment"
    assert [g["project"] for g in gaps["unassessed"]] == ["zzz-some-new-product"]


def test_the_fallback_name_is_flagged():
    """`other` is normalize()'s own 'I could not name this'. It must never
    sit in the project table looking like a product."""
    gaps = projects.assessment_gaps([_p("other", sessions=2)])
    assert any(g["project"] == "other" for g in gaps["not_projects"])


def test_worktree_hash_names_are_flagged():
    """`amazing-bartik-bd7fe3` is a git worktree directory, not a product."""
    gaps = projects.assessment_gaps([_p("amazing-bartik-bd7fe3", sessions=3),
                                     _p("wonderful-yonath-ae5879", sessions=2)])
    assert len(gaps["not_projects"]) == 2, gaps["not_projects"]


def test_a_real_product_is_NOT_called_a_fragment():
    """FALSIFIER. Over-flagging would bury the real signal in noise."""
    for good in ("purangpt", "puranastro", "nidra", "cantax", "mila-english",
                 "awakener", "carrymate", "vyasa", "bro-os", "puran-offline"):
        gaps = projects.assessment_gaps([_p(good, sessions=5)])
        assert not gaps["not_projects"], "%s was wrongly called a fragment" % good


# ---------------------------------------------------------------------------
# unassessed detection — the bigger gap
# ---------------------------------------------------------------------------

def test_active_project_with_no_goal_is_unassessed():
    gaps = projects.assessment_gaps([_p("nidra", sessions=2, facts=17),
                                     _p("purangpt", sessions=89, goals=2)])
    names = {g["project"] for g in gaps["unassessed"]}
    assert names == {"nidra"}, names


def test_unassessed_is_ranked_by_how_much_you_work_there():
    """The one you touch most and measure least should be top of the list."""
    gaps = projects.assessment_gaps([_p("small", sessions=1),
                                     _p("big", sessions=40),
                                     _p("mid", sessions=9)])
    assert [g["project"] for g in gaps["unassessed"]] == ["big", "mid", "small"]


def test_a_project_WITH_a_goal_is_not_reported():
    gaps = projects.assessment_gaps([_p("meditate", sessions=15, goals=2)])
    assert gaps["unassessed"] == []


def test_a_fragment_is_not_ALSO_reported_as_unassessed():
    """`skills` needs an alias, not a goal. Telling you to write a goal file
    for a directory name is worse than saying nothing."""
    gaps = projects.assessment_gaps([_p("skills", sessions=37)])
    assert gaps["not_projects"] and gaps["unassessed"] == []


def test_a_dormant_project_is_not_nagged_about():
    """Zero sessions means you are not working there. Reporting it as
    unassessed manufactures work out of silence."""
    gaps = projects.assessment_gaps([_p("old-thing", sessions=0)])
    assert gaps["unassessed"] == []


def test_totals_are_reported():
    gaps = projects.assessment_gaps([_p("a", sessions=5, goals=1),
                                     _p("b", sessions=5), _p("skills", sessions=5)])
    assert gaps["tracked"] == 3
    assert gaps["assessed"] == 1
    assert gaps["real_projects"] == 2


def test_a_project_with_history_but_nothing_recent_is_DORMANT():
    """"Started and left" is a THIRD state, not an absence.

    Before the scan was widened, `_repo_dirs` read `_CONTAINERS[:2]` and found
    26 of 71 real repos; `commit_history` then covered 11 projects. A project
    untouched for 31 days reported a plain 0 and was indistinguishable from
    one that never existed — so airun (118 commits, last touched June),
    mila-english, bro-os, fluency-bridge and orchestrator-first were invisible
    to the fleet. Measured after the fix: 42 repos, history for 40, 10 dormant.
    """
    g = projects.assessment_gaps()
    assert "dormant" in g, "dormant is not reported at all"
    assert isinstance(g["dormant"], list)
    for d in g["dormant"]:
        assert d["commits"] > 5, "a project with almost no history was called dormant"


def test_the_scan_reaches_past_the_first_two_containers():
    """The one-line defect: `_CONTAINERS[:2]` skipped ~/Documents and ~.

    Behavioural, not a source grep — the first cut of this test grepped
    `_repo_dirs` for the literal slice and failed on the COMMENT that explains
    why the slice was removed. A test that reads the prose instead of the
    behaviour tells you nothing about the behaviour.
    """
    sliced = projects._repo_dirs(containers=projects._CONTAINERS[:2])
    full = projects._repo_dirs()
    assert len(full) > len(sliced), \
        "full scan found %d, sliced found %d — the later containers are dark" \
        % (len(full), len(sliced))
    assert set(sliced) <= set(full)


def test_a_third_party_checkout_is_not_called_MY_abandoned_project():
    """FALSIFIER for dormancy.

    comfyui (4686 commits, ~/ComfyUI) topped the first dormant list. It is a
    third-party tool that happens to sit under a scanned container — telling
    the owner he abandoned it is a false finding, and the loudest one, since
    the list is ranked by commit count. Same for airun: 118 of 118 commits by
    Jed White, a clone, not abandoned work.
    """
    g = projects.assessment_gaps()
    names = {d["project"] for d in g["dormant"]}
    for theirs in ("comfyui", "airun"):
        assert theirs not in names, "%s is someone else's repo, reported as abandoned work" % theirs
    for d in g["dormant"]:
        assert d.get("mine") != 0, "%s has zero commits by any identity of this machine" % d["project"]


def test_ownership_survives_MULTIPLE_git_identities():
    """FALSIFIER, and the reason the first rule was thrown out.

    `git config user.email` returns ONE address. The owner has committed
    under at least four — a GitHub noreply, a personal gmail, a work address,
    and badenath@Prashants-MacBook-Pro.local. Filtering on the configured one
    dropped `flight postman` (16 of 16 his) and `gurugpt-next` (12 of 12) from
    the dormant list while claiming to remove only third-party checkouts:
    10 dormant became 5, and 3 of the 5 removals were wrong.

    The replacement derives identity from repo SPREAD — an address seen in >=3
    of your repos is yours; comfyanonymous has 2416 commits in exactly one.
    """
    hist = projects.commit_history()
    ids = projects._my_identities({n: projects._authors(p)
                                   for n, p in projects._repo_dirs().items()})
    assert len(ids) >= 2, "only %d identity derived — the multi-email case is unhandled" % len(ids)
    for own in ("flight-postman", "gurugpt-next"):
        h = hist.get(own)
        if h:
            assert h["commits_mine"] == h["commits"], \
                "%s: %s of %s commits credited — an identity was missed" \
                % (own, h["commits_mine"], h["commits"])


def test_unknowable_ownership_does_NOT_exclude():
    """not-checkable is not false. With too few repos to derive an identity,
    commits_mine is None and the repo stays in — the same rule grade() follows
    when it cannot reach a source."""
    assert projects._my_identities({"solo": {"a@b.c": 9}}) == set()
    hist = projects.commit_history(containers=[], ttl_s=0)
    assert hist == {} or all(h["commits_mine"] is None for h in hist.values())


def test_a_container_name_is_not_reported_as_dormant():
    """`bin`, `claude-sync` and `skills` are directories that hold work. The
    same _is_product rule that guards the unassessed list guards this one."""
    g = projects.assessment_gaps()
    for d in g["dormant"]:
        assert projects._is_product(d["project"]), \
            "%s is a container, not a dormant product" % d["project"]



def test_doctor_only_asks_for_alias_lines_a_PERSON_could_write():
    """Measured 2026-08-29: 8 names flagged as not-projects, and every one was
    already handled by a rule — `skills` is a declared container, `other` is
    normalize()'s own sentinel, six were worktree hashes caught by shape. Yet
    doctor went red on `project_names_unaliased` and printed those very names
    as the ones to alias. Zero were actionable, so the health check was
    permanently red over work nobody should do."""
    gaps = projects.assessment_gaps([
        _p("skills", sessions=37), _p("other", sessions=2),
        _p("amazing-bartik-bd7fe3", sessions=3),
        _p("purangpt", sessions=89, goals=2)])
    assert gaps["not_projects"], "the informational list disappeared"
    assert gaps["needs_alias"] == [], \
        "asking for an alias for %s — all three are handled by a rule" \
        % [g["project"] for g in gaps["needs_alias"]]


def test_a_REAL_fragment_still_asks_for_its_alias():
    """FALSIFIER. Emptying needs_alias by always returning [] would 'fix' the
    red light and lose the signal. A name that no rule explains still needs a
    person."""
    gaps = projects.assessment_gaps([_p("zz-frag", sessions=4)])
    # an unknown name is TRUSTED as a product (see the test above), so it is
    # not in not_projects at all — and therefore not in needs_alias either
    assert gaps["needs_alias"] == []
    # but one that IS classed as a fragment and is not rule-explained must be
    fake = dict(gaps)
    got = projects.assessment_gaps([_p("other", sessions=1), _p("skills", sessions=1)])
    assert len(got["not_projects"]) == 2 and got["needs_alias"] == []


def test_live_machine_reports_without_crashing():
    g = projects.assessment_gaps()
    print("       live: %d tracked, %d real, %d assessed, %d unassessed, %d not-projects"
          % (g["tracked"], g["real_projects"], g["assessed"],
             len(g["unassessed"]), len(g["not_projects"])))
    assert g["tracked"] > 0


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
