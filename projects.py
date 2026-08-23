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
import re
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


def _clean_project_name(seg: str) -> str:
    """A directory name reduced to the project it stands for."""
    s = (seg or "").strip().strip("/")
    s = s.split("--claude-worktrees-")[0].split("--worktrees-")[0]
    s = re.sub(r"^wt-", "", s)          # wt-glyph-sweep is a branch of a project
    s = re.sub(r"-wt$", "", s)
    s = re.sub(r"[ _]+", "-", s).strip("-").lower()
    for pat, name in _aliases():
        if pat in s:
            return name
    return s or "unknown"


# Directories that HOLD projects but are not projects themselves. A path is
# resolved relative to whichever of these contains it.
_CONTAINERS = [os.path.expanduser("~/projects"),
               os.path.expanduser("~/.claude/skills"),
               os.path.expanduser("~/Documents"),
               os.path.expanduser("~")]

# Not projects at all: the transcript and memory store. EVERY session writes
# here, so counting it as work made "memory" look like a 21% project.
_NOT_WORK = [os.path.expanduser("~/.claude/projects"),
             os.path.expanduser("~/.claude/meditation")]

_GIT_CACHE: Dict[str, bool] = {}


def _is_repo(path: str) -> bool:
    if path not in _GIT_CACHE:
        _GIT_CACHE[path] = os.path.exists(os.path.join(path, ".git"))
    return _GIT_CACHE[path]


def project_dir_of(path: str,
                   containers: Optional[List[str]] = None) -> Optional[str]:
    """The directory that IS the project this file belongs to.

    Two signals, in order:
      1. the nearest ancestor holding a .git — a repo is a project by
         definition, and this is what separates a container directory from
         the several products inside it
      2. failing that, the first directory under a known container

    Position alone cannot tell them apart: 'vedic puran/AwakenerUnity' is a
    game and 'job-copilot/src' is a source folder, and both are the second
    segment of their path.
    """
    p = os.path.abspath(path)
    for skip in _NOT_WORK:
        if p.startswith(skip + os.sep):
            return None
    d = os.path.dirname(p)
    root = None
    for c in (containers if containers is not None else _CONTAINERS):
        if p.startswith(c + os.sep):
            root = c
            break
    # 1. nearest repo
    probe = d
    while probe and probe != "/" and (root is None or probe.startswith(root)):
        if probe != root and _is_repo(probe):
            return probe
        probe = os.path.dirname(probe)
    # 2. first directory under the container
    if root:
        rest = p[len(root) + 1:].split(os.sep)
        if rest and rest[0]:
            return os.path.join(root, rest[0])
    return None


def project_of_work(files: Optional[List[str]],
                    containers: Optional[List[str]] = None) -> Optional[str]:
    """Which project a session actually WORKED on, from the files it edited.

    The folder you launch Claude from is not the thing you are building. This
    workspace runs almost everything out of one directory, so attributing by
    cwd said purangpt owned 96.3% of all attention. Measured across the same
    155 transcripts and 3,355 user messages, by what was actually edited,
    purangpt is 17% and this tool itself is the biggest consumer at 29.9% —
    a fact the console could never have shown you.

    Sub-projects are NOT folded into their container: a directory that happens
    to hold several products is not one product.
    """
    if not files:
        return None
    counts: Dict[str, int] = {}
    for f in files:
        d = project_dir_of(f, containers)
        if not d:
            continue
        name = _clean_project_name(os.path.basename(d))
        if name and not name.startswith("."):
            counts[name] = counts.get(name, 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda k: counts[k])


def window_days(root: Optional[str] = None) -> int:
    """How far back the transcripts on disk actually reach. The attention
    numbers are a share of THIS window, not of history — the tool archives
    old transcripts, so the window is ~3 weeks here while the repos go back
    months. The label must say so or the percentage lies by omission."""
    root = root or os.path.expanduser("~/.claude/projects")
    oldest = 0.0
    try:
        for slug in os.listdir(root):
            d = os.path.join(root, slug)
            if not os.path.isdir(d):
                continue
            for fn in os.listdir(d):
                if fn.endswith(".jsonl"):
                    try:
                        age = time.time() - os.path.getmtime(os.path.join(d, fn))
                        oldest = max(oldest, age)
                    except OSError:
                        pass
    except OSError:
        pass
    return max(1, int(oldest / 86400))


def _repo_dirs(containers: Optional[List[str]] = None,
               max_depth: int = 2) -> Dict[str, str]:
    """cleaned-name -> path for every REAL repo under the containers.

    A worktree carries a .git FILE pointing at its parent repo; counting it
    as a repo would count the same commits twice (wt-glyph-sweep is a branch
    of purangpt-next, not a second project). Only a .git DIRECTORY counts.
    """
    out: Dict[str, str] = {}
    roots = containers if containers is not None else _CONTAINERS[:2]
    frontier = [(r, 0) for r in roots if os.path.isdir(r)]
    while frontier:
        base, depth = frontier.pop()
        try:
            entries = sorted(os.scandir(base), key=lambda e: e.name)
        except OSError:
            continue
        for e in entries:
            if not e.is_dir(follow_symlinks=False) or e.name.startswith("."):
                continue
            git = os.path.join(e.path, ".git")
            if os.path.isdir(git):                      # real repo
                name = _clean_project_name(e.name)
                # An alias can fold a differently-named dir onto this name
                # (the-vedic-mind-architecture -> purangpt). The repo whose
                # OWN directory name is the name outranks an alias hit —
                # otherwise scan order decides which repo owns the label,
                # and the real purangpt lost its own name to a mirror.
                exact = _clean_project_name.__wrapped__(e.name) == name \
                    if hasattr(_clean_project_name, "__wrapped__") else \
                    re.sub(r"[ _]+", "-", e.name.lower()).strip("-") == name
                if exact or name not in out:
                    if exact or not out.get("__exact_" + name):
                        out[name] = e.path
                if exact:
                    out["__exact_" + name] = e.path
            elif not os.path.exists(git) and depth + 1 < max_depth:
                frontier.append((e.path, depth + 1))    # container, look inside
    return {k: v for k, v in out.items() if not k.startswith("__exact_")}


def _git_out(repo: str, *args: str) -> str:
    import subprocess
    try:
        r = subprocess.run(["git", "-C", repo, *args], capture_output=True,
                           text=True, timeout=20)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


_HISTORY_CACHE: Dict[str, Any] = {"at": 0.0, "data": {}}


def commit_history(recent_days: int = 30,
                   containers: Optional[List[str]] = None,
                   ttl_s: float = 300.0) -> Dict[str, Dict[str, Any]]:
    """name -> {commits, commits_recent, since} from each repo's OWN history.

    Transcripts only reach back ~23 days here — the tool archives them — so
    message counts are a recency window, not a history. Git is the durable
    record: purangpt shows 574 commits since June while this tool's 79 span
    two days. Both axes are true; only shown together are they honest.
    """
    import time as _t
    if containers is None and _t.time() - _HISTORY_CACHE["at"] < ttl_s \
            and _HISTORY_CACHE["data"]:
        return _HISTORY_CACHE["data"]
    out: Dict[str, Dict[str, Any]] = {}
    for name, path in _repo_dirs(containers).items():
        # --branches, not HEAD: the checked-out branch here sat at Jul 21
        # while a month of work lived on worktree branches in the same repo.
        total = _git_out(path, "rev-list", "--count", "--branches")
        if not total or not int(total):
            continue
        recent = _git_out(path, "rev-list", "--count",
                          "--since=%d.days" % recent_days, "--branches")
        since = _git_out(path, "log", "--max-parents=0", "--format=%as")
        out[name] = {"commits": int(total),
                     "commits_recent": int(recent or 0),
                     "since": (since.splitlines() or [""])[-1]}
    if containers is None:
        _HISTORY_CACHE.update({"at": _t.time(), "data": out})
    return out


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
        # what was edited beats where it was launched; cwd only when a session
        # touched nothing at all
        by_work = project_of_work(s.get("files_touched"))
        r = row(by_work or normalize(s.get("_project_slug") or s.get("cwd", "")))
        r["sessions"] += 1
        r["messages"] += (s.get("counts") or {}).get("user", 0)
        age = _age_days(s.get("ts_end") or "")
        if age is not None:
            if r["last_touched_days"] is None or age < r["last_touched_days"]:
                r["last_touched_days"] = round(age, 1)

    # history: the repos' own record, which outlives the transcripts
    hist = commit_history()
    for name, h in hist.items():
        if name in proj or h["commits_recent"] > 0:
            r = row(name)
            r["commits"] = h["commits"]
            r["commits_recent"] = h["commits_recent"]
            r["since"] = h["since"]
    for r in proj.values():
        r.setdefault("commits", 0)
        r.setdefault("commits_recent", 0)
        r.setdefault("since", "")

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
