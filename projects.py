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
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

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


# Directory names that are somewhere files live, not projects.
_NOT_A_PROJECT = {"projects", "downloads", "documents", "desktop", "library",
                  "claude-sync", "wt", "tmp", "src", "backups"}

_KNOWN_CACHE: Dict[str, Any] = {"at": 0.0, "names": set()}


def known_project_names(ttl_s: float = 300.0) -> set:
    """Every real repo on this machine, by name — used to recognise a project
    when a fact names one in plain words."""
    if time.time() - _KNOWN_CACHE["at"] < ttl_s and _KNOWN_CACHE["names"]:
        return _KNOWN_CACHE["names"]
    try:
        names = {k for k in _repo_dirs()
                 if len(k) >= 4 and not k.startswith(".")
                 and k not in _NOT_A_PROJECT}
    except Exception:
        names = set()
    _KNOWN_CACHE.update({"at": time.time(), "names": names})
    return names


def _usable(name: Optional[str]) -> Optional[str]:
    if not name or name.startswith(".") or len(name) < 3:
        return None
    return None if name in _NOT_A_PROJECT else name


_SHA_CACHE: Dict[str, Optional[str]] = {}
_SHA_DISK = os.path.expanduser("~/.claude/meditation/sha-repo-cache.json")
_SHA_LOADED = False
_SHA_DIRTY = False


def _sha_cache_load() -> None:
    """Which repo holds a commit never changes, so this answer keeps.

    Without it every CLI run re-spawned `git cat-file` once per sha per repo
    from scratch. Measured on this machine: 79 shas, and 20.6s of the 27s
    rollup was the process waiting on those subprocesses.

    Only POSITIVE answers are kept. A miss means "no repo here has it YET" —
    clone that repo tomorrow and the answer changes, so misses stay in memory
    for this run only.
    """
    global _SHA_LOADED
    if _SHA_LOADED:
        return
    _SHA_LOADED = True
    try:
        with open(_SHA_DISK) as f:
            for k, v in json.load(f).items():
                if v:
                    _SHA_CACHE.setdefault(k, v)
    except Exception:
        pass


def _sha_cache_save() -> None:
    global _SHA_DIRTY
    if not _SHA_DIRTY:
        return
    _SHA_DIRTY = False
    keep = {k: v for k, v in _SHA_CACHE.items() if v}
    try:
        os.makedirs(os.path.dirname(_SHA_DISK), exist_ok=True)
        tmp = _SHA_DISK + ".tmp"
        with open(tmp, "w") as f:
            json.dump(keep, f)
        os.replace(tmp, _SHA_DISK)
    except OSError:
        pass


def repo_of_commit(sha: str) -> Optional[str]:
    """Which repo on this machine contains this commit.

    A commit id is the most precise thing a fact can carry: it names one line
    of history in exactly one repo. 67 facts here had one and nobody looked.
    """
    sha = (sha or "").strip()
    if len(sha) < 7 or not re.fullmatch(r"[0-9a-fA-F]+", sha):
        return None
    _sha_cache_load()
    if sha in _SHA_CACHE:
        return _SHA_CACHE[sha]
    out = None
    for name, path in _repo_dirs().items():
        try:
            r = subprocess.run(["git", "-C", path, "cat-file", "-e",
                                sha + "^{commit}"],
                               capture_output=True, timeout=8)
            if r.returncode == 0:
                out = _usable(name)
                break
        except Exception:
            continue
    _SHA_CACHE[sha] = out
    if out:
        global _SHA_DIRTY
        _SHA_DIRTY = True
        _sha_cache_save()
    return out


def _slug_project(source: str) -> Optional[str]:
    """The workspace a memory was written in, when that IS a project.

    A session slug is normally too coarse — this workspace runs everything out
    of one directory, which is exactly how 448 facts ended up on one project.
    But a slug that points at a real repo rather than a container is a fine
    answer: -Users-x-projects-mila-english IS mila-english.
    """
    for marker in ("/memory/", "/projects/"):
        if marker not in source:
            continue
        slug = source.split(marker, 1)[1].split("/")[0]
        if not slug.startswith("-Users-"):
            return None
        guess = _clean_project_name(slug.rsplit("-", 1)[-1] if False else
                                    slug.replace("-Users-", "").split("-", 1)[-1])
        # resolve the slug back to a directory and demand it be a repo
        parts = [p for p in slug.strip("-").split("-") if p]
        cand = os.path.join("/", *parts)
        if os.path.isdir(cand) and os.path.isdir(os.path.join(cand, ".git")):
            return _usable(_clean_project_name(os.path.basename(cand)))
        # the path may contain hyphens of its own; fall back to the last
        # segment only when a repo of that exact name exists
        tail = _clean_project_name(parts[-1]) if parts else None
        if tail and tail in _repo_dirs():
            return tail
        return None
    return None


def project_of_fact(mem: Dict[str, Any],
                    known: Optional[set] = None) -> Tuple[set, str]:
    """Which project a graded fact is ABOUT, and how we know.

    It used to be decided by each memory's `evidence.source` — which is the
    path of the memory FILE, inside the store, under the session slug of
    wherever it was written. On this machine that is one directory for almost
    everything, so 448 of 495 facts were filed under purangpt and every other
    project showed zero. The console said one project held all the knowledge
    in the workspace; it held the memory files, which is not the same claim.

    Three signals, best first, and no guessing after them:
      path   a path: locator, resolved to its repo the same way work is
      tag    an explicit project: tag that names a project, not a session slug
      named  the fact's own words naming a repo that exists on this machine

    A fact with none of those is returned unattributed. 272 of 495 here are,
    and that is the honest answer — better than a number that points every
    question at the wrong project.
    """
    known = known_project_names() if known is None else known
    names = set()
    for e in mem.get("evidence", []) or []:
        loc = str(e.get("locator", ""))
        if loc.startswith("path:"):
            d = project_dir_of(loc[5:])
            if d:
                n = _usable(_clean_project_name(os.path.basename(d)))
                if n:
                    names.add(n)
    if names:
        return names, "path"

    for t in mem.get("tags", []) or []:
        if t.startswith("project:"):
            v = t[8:]
            # a session slug is where you were, not what it is about
            if not v.startswith("-Users-") and not v.startswith("."):
                n = _usable(normalize(v))
                if n:
                    names.add(n)
    if names:
        return names, "tag"

    for e in mem.get("evidence", []) or []:
        loc = str(e.get("locator", ""))
        if loc.startswith("commit:"):
            n = repo_of_commit(loc[7:])
            if n:
                names.add(n)
    if names:
        return names, "commit"

    if known:
        text = (str(mem.get("statement", "")) + " "
                + " ".join(mem.get("tags", []) or [])).lower()
        hit = {k for k in known if re.search(r"\b%s\b" % re.escape(k), text)}
        if hit:
            return hit, "named"

    for e in mem.get("evidence", []) or []:
        n = _slug_project(str(e.get("source", "")))
        if n:
            names.add(n)
    if names:
        return names, "slug"
    return set(), "none"


def _memory_slug(mem: Dict[str, Any]) -> Optional[str]:
    """The memory file this fact was written from, e.g. prod-moved-to-mumbai."""
    for e in mem.get("evidence", []) or []:
        src = str(e.get("source", ""))
        if src.endswith(".md"):
            return os.path.basename(src)[:-3]
    return None


def attribute_all(memories: List[Dict[str, Any]],
                  known: Optional[set] = None) -> Dict[str, Any]:
    """Place every fact, in two passes.

    The second pass is why this is not per-memory: a fact that names nothing
    may still LINK to facts that do. 923 wikilinks sit in this store and
    nobody followed one.

    Inheritance is deliberately the weakest signal and is labelled `linked`.
    Linking to a fact about a project is not the same as being about it, and
    65 of the 92 it places here go to the one project that was already
    over-counted — so it demands an unambiguous winner among a fact's links,
    and it never overrides direct evidence.
    """
    known = known_project_names() if known is None else known
    placed: Dict[str, Any] = {}
    owner: Dict[str, set] = {}
    unplaced: List[Dict[str, Any]] = []

    for m in memories:
        names, how = project_of_fact(m, known)
        mid = str(m.get("id", id(m)))
        if names:
            placed[mid] = (names, how)
            slug = _memory_slug(m)
            if slug:
                owner[slug] = names
        else:
            unplaced.append(m)

    for m in unplaced:
        votes: Dict[str, int] = {}
        for e in m.get("evidence", []) or []:
            loc = str(e.get("locator", ""))
            if not loc.startswith("wikilink:"):
                continue
            target = loc[9:].strip().strip("[]")
            for n in owner.get(target, ()):  # only follow links we can resolve
                votes[n] = votes.get(n, 0) + 1
        if not votes:
            continue
        ranked = sorted(votes.items(), key=lambda kv: -kv[1])
        if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
            continue                      # a tie is not an answer
        placed[str(m.get("id", id(m)))] = ({ranked[0][0]}, "linked")
    return placed


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
    known = known_project_names()
    unattributed = 0
    if os.path.exists(mp):
        active_mems = []
        with open(mp, errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    m = json.loads(line)
                except Exception:
                    continue
                if m.get("active"):
                    active_mems.append(m)
        placed = attribute_all(active_mems, known)
        for m in active_mems:
            names = (placed.get(str(m.get("id", id(m)))) or (set(), ""))[0]
            if not names:
                unattributed += 1
                continue              # never invent an owner for a fact
            for n in names:
                r = row(n)
                r["facts"] += 1
                if m.get("epistemic", {}).get("evidence_status") == "unverified" \
                        and (m.get("evidence") or m.get("flags")):
                    r["repair_items"] += 1

    # How many facts nobody can place. Shown, not hidden: a project column
    # that silently drops half the store is the same lie in a quieter voice.
    rollup.facts_unattributed = unattributed

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


# A name normalize() produced from a path component rather than a product.
# These are what you get when a session is opened inside a container directory
# (~/.claude/skills, ~/claude-sync) — the first real segment becomes the
# "project". Measured 2026-08-25: `skills` showed 37 sessions and `claude-sync`
# 27, more than most actual products, and `other` — normalize()'s own
# "I could not name this" fallback — sat in the table looking like one.
_NOT_PRODUCTS = {"skills", "claude-sync", "claude", "private", "web", "other",
                 "meditation", "backups", "tmp", "var", "scratch", "hooks",
                 "plugins", "sync", "desktop", "downloads", "library"}

# A git worktree directory: two dictionary words plus a short hex tag, e.g.
# `amazing-bartik-bd7fe3`. Carries no product identity.
_WORKTREE_NAME = re.compile(r"^[a-z]+-[a-z]+-[0-9a-f]{6,}$")


def _is_product(name: str) -> bool:
    return not (name in _NOT_PRODUCTS or _WORKTREE_NAME.match(name or ""))


def assessment_gaps(rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Where is meditate silent about your work, and why?

    Two different gaps that need two different fixes, which is why they are
    reported separately:

      not_projects — the name came from a path component, not a product. Needs
                     a line in project-aliases.txt, NOT a goal file. Telling
                     someone to write goals for a directory name is worse than
                     saying nothing.
      unassessed   — a real product you actively work in that has no goal, so
                     nothing can say whether it is going well. Measured
                     2026-08-25: 79 of 83 tracked entries had zero goals; six
                     goal files cover three products. Where a goal DOES exist
                     the judgement is good (purangpt-mobile-live reads 6/8 with
                     live-verified evidence per box) — the defect is coverage,
                     not quality, and silence about the other ~20 products
                     reads as health.

    Dormant projects are never reported. Zero sessions means you are not
    working there, and manufacturing work out of silence is the same
    can't-say-I-don't-know defect this tool keeps finding in itself.
    """
    if rows is None:
        rows = rollup()          # returns a LIST of rows, not an envelope
    not_products, unassessed, assessed = [], [], 0
    for p in rows:
        name = p.get("project") or ""
        sessions = p.get("sessions") or 0
        if not _is_product(name):
            if sessions:
                not_products.append({"project": name, "sessions": sessions})
            continue
        if p.get("goals"):
            assessed += 1
        elif sessions:
            unassessed.append({"project": name, "sessions": sessions,
                               "facts": p.get("facts") or 0})
    unassessed.sort(key=lambda g: -g["sessions"])
    not_products.sort(key=lambda g: -g["sessions"])
    return {
        "tracked": len(rows),
        "real_projects": sum(1 for p in rows if _is_product(p.get("project") or "")),
        "assessed": assessed,
        "unassessed": unassessed,
        "not_projects": not_products,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="meditate projects", description="Per-project attention and tasks")
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
