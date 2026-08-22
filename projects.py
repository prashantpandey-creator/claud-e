"""projects — attention and progress, per project, then per task.

The question this answers: which projects actually get your time, and what
is open inside each. Everything else in meditate is global; this is the
same data cut the way work is actually organised.

Per project it joins, deterministically:
  attention  — sessions, your messages, days since last touched
  knowledge  — facts known, how many failed verification (repair items)
  goals      — % done, and every open milestone (the task-level view)

Project identity: session slugs fragment the same project across worktrees
and subdirectories (-vedic-puran, -vedic-puran-purangpt,
-vedic-puran-purangpt--claude-worktrees-xyz are ONE project). normalize()
folds them to a single name, so "how much attention" isn't split six ways.

    meditate projects            # ranked by attention, open tasks under each
    meditate projects --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

STORE_DIR = os.environ.get("MEDITATE_STORE_DIR") or os.path.expanduser(
    "~/.claude/meditation/nidra_store")

# Optional user-supplied aliases, colon-list of glob=name (MEDITATE_PROJECT_ALIASES),
# so anyone can fold their own worktree spellings. No owner names baked in.
ALIAS_FILE = os.path.expanduser("~/.claude/meditation/project-aliases.txt")


def _aliases():
    """User-tunable project folding. Two sources, both optional:
      - ~/.claude/meditation/project-aliases.txt  (one `pattern=name` per line)
      - env MEDITATE_PROJECT_ALIASES  (colon-list of pattern=name)
    Neither is required; without them normalize() is fully algorithmic."""
    out = []
    try:
        with open(ALIAS_FILE) as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip():
                        out.append((k.strip().lower(), v.strip()))
    except OSError:
        pass
    for pair in (os.environ.get("MEDITATE_PROJECT_ALIASES") or "").split(":"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            if k.strip():
                out.append((k.strip().lower(), v.strip()))
    return out


# Noise segments that are NEVER a project name — generic to any machine.
_NOISE = {"users", "home", "projects", "documents", "code", "dev", "src",
          "work", "repos", "claude", "worktrees", "worktree", "scratch"}


def normalize(slug_or_path: str) -> str:
    """Fold a session slug / cwd / tag to one project name — algorithmically.

    A project = the FIRST meaningful path segment after the home/projects
    prefix, with git-worktree noise ('--claude-worktrees-xyz', trailing
    subdirs) stripped. No hardcoded project list: the same rule works on any
    user's machine. Optional MEDITATE_PROJECT_ALIASES tunes edge cases.
    """
    s = (slug_or_path or "").replace("/", "-").replace("_", "-").replace(" ", "-").lower()
    # worktree suffix carries no project identity
    s = s.split("--claude-worktrees-")[0].split("--worktrees-")[0]
    for pat, name in _aliases():
        if pat in s:
            return name
    parts = [p for p in s.split("-") if p]
    # skip the leading machine noise, then take the first two real segments
    # joined (so a-b-purangpt-next keeps 'purangpt-next', but a monorepo's
    # first real dir wins). Drop the username (segment right after 'users').
    real = []
    skip_next = False
    for i, p in enumerate(parts):
        if skip_next:
            skip_next = False
            continue
        if p == "users" or p == "home":
            skip_next = True            # drop the username that follows
            continue
        if p in _NOISE:
            continue
        real.append(p)
    if not real:
        return "other"
    # first segment is the project; keep a "-next"/"-web"/"-api" suffix if present
    name = real[0]
    if len(real) > 1 and real[1] in ("next", "web", "api", "app", "ios", "android",
                                     "server", "client", "mobile"):
        name = real[0] + "-" + real[1]
    return name


def _age_days(ts: str) -> Optional[float]:
    try:
        import calendar
        t = calendar.timegm(time.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S"))
        return (time.time() - t) / 86400
    except Exception:
        return None


def rollup(sessions: Optional[List[Dict]] = None,
           store_dir: str = STORE_DIR,
           goals_dir: Optional[str] = None,
           history_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """One row per project: attention, knowledge, goals, open tasks."""
    import goals as gl

    if sessions is None:
        try:
            from sessions import scan_all_projects
            sessions = scan_all_projects(cap=500)["data"]["sessions"]
        except Exception:
            sessions = []

    proj: Dict[str, Dict[str, Any]] = {}

    def row(name: str) -> Dict[str, Any]:
        return proj.setdefault(name, {
            "project": name, "sessions": 0, "messages": 0,
            "last_touched_days": None, "facts": 0, "repair_items": 0,
            "goals": 0, "milestones_done": 0, "milestones_total": 0,
            "open_tasks": [],
        })

    # attention
    for s in sessions:
        r = row(normalize(s.get("_project_slug") or s.get("cwd", "")))
        r["sessions"] += 1
        r["messages"] += (s.get("counts") or {}).get("user", 0)
        age = _age_days(s.get("ts_end") or "")
        if age is not None:
            if r["last_touched_days"] is None or age < r["last_touched_days"]:
                r["last_touched_days"] = round(age, 1)

    # knowledge
    mp = os.path.join(store_dir, "memories.jsonl")
    if os.path.exists(mp):
        with open(mp, errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    m = json.loads(line)
                except Exception:
                    continue
                if not m.get("active"):
                    continue
                names = {normalize(t[8:]) for t in m.get("tags", [])
                         if t.startswith("project:")}
                for ev in m.get("evidence", []):
                    src = str(ev.get("source", ""))
                    if src:
                        names.add(normalize(src))
                for n in (names or {"other"}):
                    r = row(n)
                    r["facts"] += 1
                    if m.get("epistemic", {}).get("evidence_status") == "unverified" \
                            and (m.get("evidence") or m.get("flags")):
                        r["repair_items"] += 1

    # goals + the task-level view
    kw = {}
    if goals_dir:
        kw["goals_dir"] = goals_dir
    if history_path:
        kw["history_path"] = history_path
    for g in gl.scan(**kw):
        r = row(normalize(g.get("project") or g.get("cwd", "")))
        r["goals"] += 1
        r["milestones_done"] += g["done"]
        r["milestones_total"] += g["total"]
        if g["next"]:
            r["open_tasks"].append({"goal": g["name"], "task": g["next"],
                                    "pct": g["pct"]})

    out = list(proj.values())
    for r in out:
        r["pct"] = round(100.0 * r["milestones_done"] / r["milestones_total"], 1) \
            if r["milestones_total"] else None
    # rank by attention actually spent — messages, then sessions
    out.sort(key=lambda r: (-r["messages"], -r["sessions"]))
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Per-project attention and tasks")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rows = rollup()
    if args.json:
        print(json.dumps({"tool_name": "meditate_projects", "success": True,
                          "data": {"count": len(rows), "projects": rows},
                          "metadata": {"store_dir": STORE_DIR}, "errors": []},
                         indent=2))
        return 0
    total_msgs = sum(r["messages"] for r in rows) or 1
    print("Projects — where your attention actually went")
    print("=" * 72)
    print("  %-14s %6s %7s %7s %6s %7s  %s"
          % ("project", "share", "msgs", "chats", "facts", "goals", "last"))
    for r in rows:
        share = 100.0 * r["messages"] / total_msgs
        if share < 0.5 and not r["goals"]:
            continue                      # noise floor: unnamed one-offs
        last = ("%.0fd" % r["last_touched_days"]) if r["last_touched_days"] is not None else "—"
        gp = ("%d (%.0f%%)" % (r["goals"], r["pct"])) if r["goals"] else "—"
        print("  %-14s %5.1f%% %7d %7d %6d %7s  %s"
              % (r["project"][:14], share, r["messages"], r["sessions"],
                 r["facts"], gp, last))
        for t in r["open_tasks"]:
            print("       ↳ %s" % t["task"][:64])
        if r["repair_items"]:
            print("       ! %d fact(s) failed verification" % r["repair_items"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
