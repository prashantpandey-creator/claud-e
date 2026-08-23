"""derive — where the next goal comes from when nobody wrote one.

THE GAP THIS FILLS
------------------
A goal today is a hand-written .md with hand-ticked boxes. Nothing turns work
into a goal. So a task stays a task: "remove the clutter from the chart page"
is executed and forgotten, when the standing intent behind it — this product
should be easier to use — is the thing that should outlive it and keep
driving. When the six hand-written goals run out of open milestones, the fleet
has nothing to reach for and momentum stops on a file nobody wrote at 2am.

Measured on the real workspace: 6 goal files cover 4 projects, while The
Awakener (the owner's stated north star) carries 274 human turns across 4
sessions with NO goal file, and the job-hunt cluster 282 turns with none.
Real effort, no destination recorded.

HOW IT DERIVES WITHOUT INVENTING
--------------------------------
The obvious approach — ask a model "what is the goal here?" — produces
fluent fiction, and a fictional goal that reaches the fleet costs a night of
agents. So nothing here is generated. A candidate is a CLUSTER OF REAL WORK,
and its title is the owner's OWN most-used session title for that cluster.
Every proposal carries its evidence: which sessions, how many human turns,
over what dates. Same discipline as an evidence-graded memory — a claim you
can check, not a claim you must believe.

WHY IT PROPOSES AND NEVER ADOPTS
--------------------------------
This is the load-bearing decision, and it is not timidity.

An agent that writes its own goals AND closes them is answerable to nothing
outside itself. That is the documented failure mode — goal drift, where
behaviour shifts from the original intent as the agent's own output becomes
the evidence for its next step — and it is the same disease this workspace
already measured one layer down: 86% of its graded memories are answerable
only to the store itself. A self-goaling loop converges on whatever it
already believed.

`milestones.py` states the house rule for exactly this reason: "a ledger that
edits itself is a ledger you can no longer trust to disagree with the tool."
So proposals land in goals/proposed/, which `goals.py` cannot see — it reads
`os.listdir` and filters `.md`, so a subdirectory is invisible to the fleet.
Adoption is one `mv`, by a person, in seconds.

That single human tick is what makes LONG autonomy safe rather than merely
long: derivation keeps the queue fed so momentum never waits on someone
writing a file, and the anchor keeps the direction honest.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

import paths

# Resolved, never hardcoded. A `~/claude-sync/goals` fallback here would bake
# this author's machine into a shipped tool — the exact defect test_packaging
# exists to catch, and it caught this line.
GOALS_DIR = paths.goals_dir()
PROPOSED_DIR = os.path.join(GOALS_DIR, "proposed")

# A cluster earns a proposal only above this much real human effort. Below it
# you are proposing goals for one-off errands, and a queue of trivia is how a
# fleet stays busy while going nowhere. Tuned to the measured corpus: the
# smallest thing that deserved a goal (the game) had 274 turns.
MIN_TURNS = 60
MIN_SESSIONS = 2


def existing_goals(goals_dir: str = GOALS_DIR) -> Dict[str, str]:
    """name -> project, for goals the fleet can actually see."""
    out = {}
    try:
        names = sorted(os.listdir(goals_dir))
    except OSError:
        return out
    for fn in names:
        if not fn.endswith(".md") or fn == "README.md":
            continue
        try:
            with open(os.path.join(goals_dir, fn), encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        m = re.search(r"^project:\s*(.+)$", text, re.M)
        out[fn[:-3]] = (m.group(1).strip() if m else "")
    return out


def _covered(project: str, covered: List[str]) -> bool:
    p = (project or "").lower()
    return any(c and (c.lower() in p or p in c.lower()) for c in covered)


def candidates(sessions: List[Dict[str, Any]], goals_dir: str = GOALS_DIR,
               min_turns: int = MIN_TURNS,
               min_sessions: int = MIN_SESSIONS) -> List[Dict[str, Any]]:
    """Clusters of real work with no goal — each with its evidence.

    Deterministic. No model, no generation: the cluster is the project the
    sessions actually touched, and the title is the owner's own words.
    """
    covered = list(existing_goals(goals_dir).values())
    # Cluster by project — the product IS the invariant behind its tasks — but
    # a session that wrote into several directories must count ONCE, and two
    # project-buckets built from the SAME sessions are one intent wearing two
    # directory names. Live fire: "Game work elements resume" was proposed
    # twice (AwakenerUnity + TheAwakener) and "Puran nodes and astrology data"
    # five times, once per wt-astro-* worktree — all the same sessions.
    buckets: Dict[str, Dict[str, Any]] = {}
    for s in sessions:
        sid = (s.get("session_id") or "")[:8]
        if not sid:
            continue
        for project in (s.get("projects") or []):
            if not project:
                continue
            b = buckets.setdefault(project, {
                "projects": [project], "turns_by_sid": {},
                "titles": collections.Counter(), "cwds": collections.Counter(),
                "first": None, "last": None})
            turns = s.get("counts", {}).get("user", 0)
            b["turns_by_sid"][sid] = turns          # per SESSION, never summed twice
            if s.get("title"):
                b["titles"][s["title"]] += turns
            if s.get("cwd"):
                b["cwds"][s["cwd"]] += 1
            for fld, val in (("first", s.get("ts_start")), ("last", s.get("ts_end"))):
                if not val:
                    continue
                cur = b[fld]
                if cur is None or (val < cur if fld == "first" else val > cur):
                    b[fld] = val

    # Merge buckets built from exactly the same sessions — same work, two names.
    merged: List[Dict[str, Any]] = []
    for b in buckets.values():
        sids = frozenset(b["turns_by_sid"])
        for m in merged:
            if frozenset(m["turns_by_sid"]) == sids:
                m["projects"].extend(b["projects"])
                m["titles"].update(b["titles"])
                m["cwds"].update(b["cwds"])
                break
        else:
            merged.append(b)

    out = []
    for b in merged:
        if any(_covered(p, covered) for p in b["projects"]):
            continue
        turns = sum(b["turns_by_sid"].values())     # unique sessions only
        if turns < min_turns or len(b["turns_by_sid"]) < min_sessions:
            continue
        title = (b["titles"].most_common(1)[0][0] if b["titles"]
                 else b["projects"][0])
        out.append({
            "project": b["projects"][0],
            "projects": b["projects"],
            "title": title,                 # the OWNER's words, never generated
            "turns": turns,
            "sessions": sorted(b["turns_by_sid"]),
            "cwd": b["cwds"].most_common(1)[0][0] if b["cwds"] else "",
            "first_seen": b["first"],
            "last_seen": b["last"],
        })
    out.sort(key=lambda c: -c["turns"])
    return out


def render(c: Dict[str, Any]) -> str:
    """A proposal in the goal-file format, with its evidence in the body.

    Milestones are deliberately EMPTY. Inventing them is the one step where
    fiction would enter, and an unchecked invented milestone would report
    progress that never happened. The owner (or an agent, when asked) writes
    them; until then the proposal is honest about being a destination with no
    route yet.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", c["title"].lower()).strip("-")[:48] or c["project"]
    span = ""
    if c.get("first_seen") and c.get("last_seen"):
        span = " between %s and %s" % (str(c["first_seen"])[:10], str(c["last_seen"])[:10])
    return (
        "---\n"
        "name: %s\n"
        "title: %s\n"
        "project: %s\n"
        "cwd: %s\n"
        "status: proposed\n"
        "---\n"
        "PROPOSED, not adopted — derived from work that has no goal file.\n"
        "The fleet cannot see this file: it lives in goals/proposed/, and\n"
        "goals.py lists only *.md in the goals directory itself.\n"
        "\n"
        "## Evidence\n"
        "- %d human turns across %d session(s)%s\n"
        "- sessions: %s\n"
        "- title is the owner's own most-used session title for this cluster,\n"
        "  not generated text\n"
        "\n"
        "## Milestones\n"
        "(none yet — deliberately. An invented milestone would report progress\n"
        "that never happened. Write the real ones, then move this file up one\n"
        "directory to adopt it.)\n"
        % (slug, c["title"], c["project"], c.get("cwd", ""),
           c["turns"], len(c["sessions"]), span, ", ".join(c["sessions"][:8]))
    )


def write_proposals(cands: List[Dict[str, Any]],
                    proposed_dir: str = PROPOSED_DIR) -> List[str]:
    """Write proposals where the fleet CANNOT see them. Never overwrites."""
    written = []
    os.makedirs(proposed_dir, exist_ok=True)
    for c in cands:
        body = render(c)
        name = re.search(r"^name:\s*(.+)$", body, re.M).group(1)
        path = os.path.join(proposed_dir, name + ".md")
        if os.path.exists(path):
            continue
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        written.append(path)
    return written


def run(sessions: Optional[List[Dict[str, Any]]] = None,
        goals_dir: str = GOALS_DIR, write: bool = False,
        proposed_dir: Optional[str] = None) -> Dict[str, Any]:
    if sessions is None:
        from sessions import scan_all_projects
        scan = scan_all_projects(cap=8)
        if not scan["success"]:
            return {"tool_name": "derive", "success": False, "data": {},
                    "metadata": {}, "errors": scan["errors"]}
        sessions = scan["data"]["sessions"]
    cands = candidates(sessions, goals_dir)
    written = write_proposals(cands, proposed_dir or PROPOSED_DIR) if write else []
    return {"tool_name": "derive", "success": True,
            "data": {"candidates": cands, "written": written,
                     "existing_goals": len(existing_goals(goals_dir))},
            "metadata": {"goals_dir": goals_dir,
                         "proposed_dir": proposed_dir or PROPOSED_DIR,
                         "min_turns": MIN_TURNS},
            "errors": []}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Propose goals from work that has none (never adopts)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write", action="store_true",
                    help="write proposals to goals/proposed/ (fleet cannot see them)")
    args = ap.parse_args(argv)

    env = run(write=args.write)
    if args.json:
        print(json.dumps(env, indent=2))
        return 0 if env["success"] else 1
    if not env["success"]:
        for e in env["errors"]:
            print("  ERROR: %s" % e.get("message"))
        return 1
    cands = env["data"]["candidates"]
    if not cands:
        print("  Every cluster of real work already has a goal.")
        return 0
    print("  Work with no goal (proposals — the fleet cannot see these):\n")
    print("  %-40s %6s  %s" % ("would become", "turns", "sessions"))
    for c in cands:
        print("  %-40s %6d  %s" % (c["title"][:40], c["turns"],
                                   ", ".join(c["sessions"][:4])))
    if env["data"]["written"]:
        print("\n  wrote %d proposal(s) to %s" % (
            len(env["data"]["written"]), env["metadata"]["proposed_dir"]))
        print("  adopt one:  mv <file> %s/" % env["metadata"]["goals_dir"])
    else:
        print("\n  --write to save these as proposals. Adopting is a `mv` you do.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
