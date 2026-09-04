"""goals — long-term goals across every project, measured not vibed.

A goal is one .md file in ~/claude-sync/goals/ (synced across machines,
human-editable, same family as the memory files):

    ---
    name: purangpt-ios-live
    title: PuranGPT iOS fully live
    project: purangpt
    cwd: ~/code/your-project
    status: evolving          # active | evolving | done | paused
    ---
    Why this matters, links, context — free text.

    ## Milestones
    - [x] StoreKit payments live
    - [ ] subscriptions approved

Percentage = checked / total milestones. Deterministic — an agent or the
owner ticks a box; nothing self-reports progress.

EVOLVING is first-class: projects grow, goals widen. Every scan snapshots
(done, total) to goals-history.jsonl; when the total grows the report shows
"scope +N" beside the honest (possibly LOWER) percentage — widening is
visible progress of ambition, never silent dilution.

Orchestration: `meditate goals launch <name>` builds a kickoff prompt from
the goal's open milestones and either prints the `claude` command or opens a
Terminal on it (--open, reusing launch.py). meditate ARRANGES the agents;
the work stays with them.

CLI:
  meditate goals                    # table: pct, scope drift, next milestone
  meditate goals show <name>
  meditate goals launch <name> [--open]
  meditate goals --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import paths
import time
from typing import Any, Dict, List, Optional, Optional

GOALS_DIR = paths.goals_dir()
HISTORY_PATH = os.path.expanduser("~/.claude/meditation/goals-history.jsonl")

_BOX = re.compile(r"^\s*-\s*\[( |x|X)\]\s*(.+?)\s*$")


def _headline(text: str) -> str:
    """The speakable part of a milestone.

    Milestones accumulate research notes — dates, states, build numbers, file
    names, a paragraph of what was checked and by whom. All of that earns its
    place in the file. None of it belongs in a sentence read aloud as "your
    next step is...". The headline is whatever comes before the first em dash
    or bold marker, which is where the note reliably starts.

        "iOS subscriptions approved — **CORRECTED 2026-08-25: ...**  Live
         `submit.py status`: version 1.2 state = REJECTED, ..."
      -> "iOS subscriptions approved"

    Only those markers end it. Cutting at a parenthesis as well looked tidier
    and was wrong: "mitigation in place (scheduled prune / storage fix) and
    survives one week" became "mitigation in place", which drops the very
    condition that decides whether the milestone is met.
    """
    # the twin appends `<!-- predicted …; check: … -->` to lines it adds; the
    # comment is provenance, not the milestone — it stays out of titles
    text = re.sub(r"<!--.*?-->", "", text or "", flags=re.S).strip()
    cut = len(text)
    for sep in (" — ", " – ", " -- ", "**"):
        i = text.find(sep)
        if 3 < i < cut:
            cut = i
    out = text[:cut].strip().rstrip("—–-:,;").strip()
    # A milestone with no note marker at all can still be a paragraph. Read
    # aloud, that is a monologue; cut it at a word.
    if len(out) > 110:
        out = out[:110].rsplit(" ", 1)[0] + "…"
    return out


def _parse(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    meta: Dict[str, str] = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            body = parts[2]
    done = total = 0
    nxt = None
    milestones = []          # kept, so a goal can be opened and read
    lines = body.splitlines()
    for i, line in enumerate(lines):
        m = _BOX.match(line)
        if not m:
            continue
        total += 1
        is_done = m.group(1).lower() == "x"
        # A milestone can run over several lines: the checkbox, then indented
        # continuation under it. Reading only the first line cut this one off
        # mid-sentence, unclosed bold and all —
        #
        #   "iOS subscriptions approved — **CORRECTED 2026-08-25: was NOT a
        #    queue wait, that"
        #
        # and that string is not just displayed, it is what the companion
        # SAYS OUT LOUD as your next step.
        text = m.group(2).strip()
        for cont in lines[i + 1:]:
            if not cont.strip() or _BOX.match(cont):
                break
            if not cont.startswith((" ", "\t")):
                break
            text += " " + cont.strip()
        milestones.append({"text": text, "headline": _headline(text),
                           "done": is_done})
        if is_done:
            done += 1
        elif nxt is None:
            # The HEADLINE, not the whole note. The full text stays in
            # milestones for anything that wants to read it.
            nxt = _headline(text)
    if total == 0:
        return None
    name = meta.get("name") or os.path.basename(path)[:-3]
    return {"name": name, "title": meta.get("title", name),
            "project": meta.get("project", ""), "cwd": meta.get("cwd", ""),
            "status": meta.get("status", "active"),
            "model": meta.get("model", ""),
            "done": done, "total": total,
            "pct": round(100.0 * done / total, 1), "next": nxt, "file": path,
            "milestones": milestones,
            "note": "\n".join(l for l in body.splitlines()
                               if l.strip() and not _BOX.match(l)
                               and not l.startswith("#"))[:400]}


def _last_snapshot(history_path: str) -> Dict[str, Dict[str, int]]:
    last: Dict[str, Dict[str, int]] = {}
    if os.path.exists(history_path):
        with open(history_path, errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    last[r["name"]] = r
                except Exception:
                    continue
    return last


# Days without a milestone ticking before a goal counts as stuck.
STALLED_DAYS = 5.0


def _rank(g: Dict[str, Any]) -> tuple:
    """Order goals by what they need, not by filename.

    They came back in `sorted(os.listdir())` order — alphabetical by file,
    which is no order at all. It put "Production stable — payments whole"
    (0%, real money not moving) fifth, under three goals that were merely
    further along.

    Stuck first, then closest to done. Finishing something beats starting
    something, but a goal that has not moved in days outranks both — that is
    the one nobody is going to notice on their own.
    """
    return (0 if g.get("stalled") else 1, -(g.get("pct") or 0), g.get("title", ""))


def _last_progress(name: str, history_path: str) -> Optional[float]:
    """Epoch seconds when this goal's `done` count last went UP.

    File mtime cannot answer this: ticking a box edits the file, so does
    rewording a milestone, and so does adding one. Only the snapshot history
    records movement, which is the thing that means someone is working on it.
    """
    prev = None
    last = None
    try:
        with open(history_path, errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("name") != name:
                    continue
                d = r.get("done")
                if prev is not None and isinstance(d, int) and d > prev:
                    last = r.get("ts")
                prev = d if isinstance(d, int) else prev
    except OSError:
        return None
    if not last:
        return None
    try:
        return time.mktime(time.strptime(last[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def _stalled(g: Dict[str, Any], now: float, history_path: str) -> bool:
    """Has this goal stopped moving? Says so only with evidence of movement
    to compare against — a goal with no history yet is not accused."""
    if g.get("done", 0) >= g.get("total", 0) > 0:
        return False                     # finished is not stuck
    moved = _last_progress(g.get("name", ""), history_path)
    if moved is None:
        g["idle_days"] = None
        g["idle_basis"] = "no movement recorded yet"
        return False
    g["idle_days"] = round((now - moved) / 86400.0, 1)
    g["idle_basis"] = "since a milestone last ticked"
    return g["idle_days"] > STALLED_DAYS


def scan(goals_dir: str = GOALS_DIR,
         history_path: str = HISTORY_PATH) -> List[Dict[str, Any]]:
    """Parse every goal; snapshot changes; annotate scope drift."""
    out = []
    if not os.path.isdir(goals_dir):
        return out
    last = _last_snapshot(history_path)
    new_rows = []
    for fn in sorted(os.listdir(goals_dir)):
        if not fn.endswith(".md") or fn == "README.md":
            continue
        g = _parse(os.path.join(goals_dir, fn))
        if not g:
            continue
        prev = last.get(g["name"])
        g["scope_delta"] = (g["total"] - prev["total"]) if prev else 0
        g["done_delta"] = (g["done"] - prev["done"]) if prev else 0
        if not prev or prev["total"] != g["total"] or prev["done"] != g["done"]:
            new_rows.append({"name": g["name"], "done": g["done"],
                             "total": g["total"],
                             "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
        out.append(g)
    now = time.time()
    for g in out:
        g["stalled"] = _stalled(g, now, history_path)
        g.setdefault("idle_days", None)
        g.setdefault("idle_basis", "")
    out.sort(key=_rank)
    if new_rows:
        try:
            os.makedirs(os.path.dirname(history_path), exist_ok=True)
            with open(history_path, "a") as f:
                for r in new_rows:
                    f.write(json.dumps(r) + "\n")
        except OSError:
            pass
    return out


def goal_for_cwd(cwd: str, goals_dir: str = GOALS_DIR,
                 history_path: str = HISTORY_PATH) -> str:
    """One-line SessionStart nudge for the goal governing this directory."""
    if not cwd:
        return ""
    best = None
    for g in scan(goals_dir, history_path):
        gc = g.get("cwd", "")
        if not gc or g["done"] >= g["total"] or g["status"] in ("done", "paused"):
            continue
        if cwd == gc or cwd.startswith(gc.rstrip("/") + "/"):
            if best is None or len(gc) > len(best.get("cwd", "")):
                best = g
    if not best:
        return ""
    widen = " (scope +%d)" % best["scope_delta"] if best["scope_delta"] > 0 else ""
    return ("Goal: %s — %.0f%% (%d/%d)%s. Next milestone: %s."
            % (best["title"], best["pct"], best["done"], best["total"],
               widen, best["next"]))


def detail(name: str, goals_dir: str = GOALS_DIR,
           history_path: str = HISTORY_PATH) -> Optional[Dict[str, Any]]:
    """Everything about ONE goal, for opening it up.

    The bar could only ever say "37%, next: X". Every other question — which
    milestones are done, what the world says about the open ones, who is
    working on it, when it last moved — meant opening a markdown file by hand.
    """
    rows = scan(goals_dir=goals_dir, history_path=history_path)
    g = next((r for r in rows if r.get("name") == name), None)
    if not g:
        return None
    g = dict(g)

    # what the world says about each open milestone
    try:
        from milestones import check_milestone, stale_wording, _facts
        f = _facts()
        for m in g.get("milestones", []):
            if m["done"]:
                continue
            res = check_milestone(m["text"], g, f)
            m["verdict"] = res["verdict"]
            m["evidence"] = res["evidence"]
            m["stale_wording"] = stale_wording(m["text"])
    except Exception:
        pass

    # who is on it right now
    try:
        from beacon import latest as _beacons
        b = (_beacons() or {}).get(name)
        if b:
            g["agent"] = {"message": b.get("message", "")[:400],
                          "ts": b.get("ts", ""), "done": bool(b.get("done"))}
    except Exception:
        pass
    return g


def tick(name: str, milestone: Optional[str] = None,
         goals_dir: str = GOALS_DIR) -> Dict[str, Any]:
    """Close a milestone from wherever you happen to be.

    Until this existed, the companion could tell you a thing was your next
    step and then had no way to let you say it was finished — you had to go
    and find a markdown file. Being told about work you cannot act on is the
    definition of nagging.

    Flips exactly one checkbox and says which. Deliberately narrow:

    - By default it closes the FIRST OPEN milestone, which is the one the
      companion just named as your next step. That is the only one it could
      have been talking about.
    - Given text, it closes the open milestone that text uniquely identifies,
      and refuses when the text matches more than one. A tick on the wrong
      line is silent and looks exactly like a tick on the right one, so
      guessing is not an acceptable failure mode.
    - Only the checkbox line is touched. A milestone can carry indented
      continuation lines beneath it and those are somebody's notes.

    Reversible by hand: it changes one character in a file the owner owns.
    """
    rows = scan(goals_dir=goals_dir)
    g = next((r for r in rows if r.get("name") == name), None)
    if not g:
        return {"ok": False, "why": "no goal called %s" % name}
    path = g["file"]
    try:
        lines = open(path).read().splitlines(keepends=True)
    except OSError as e:
        return {"ok": False, "why": str(e)}

    open_idx = [i for i, l in enumerate(lines)
                if (m := _BOX.match(l)) and m.group(1).lower() != "x"]
    if not open_idx:
        return {"ok": False, "why": "everything on %s is already done" % g["title"]}

    if milestone:
        want = milestone.strip().lower()
        hits = [i for i in open_idx
                if want in _BOX.match(lines[i]).group(2).strip().lower()]
        if not hits:
            return {"ok": False, "why": "nothing open matches %r" % milestone}
        if len(hits) > 1:
            return {"ok": False,
                    "why": "%r matches %d open milestones — say which"
                           % (milestone, len(hits))}
        i = hits[0]
    else:
        i = open_idx[0]

    text = _headline(_BOX.match(lines[i]).group(2).strip())
    lines[i] = lines[i].replace("[ ]", "[x]", 1)
    try:
        with open(path, "w") as f:
            f.writelines(lines)
    except OSError as e:
        return {"ok": False, "why": str(e)}
    left = len(open_idx) - 1
    return {"ok": True, "goal": g["title"], "closed": text,
            "remaining": left, "file": path}


def kickoff(name: str, goals_dir: str = GOALS_DIR,
            history_path: str = HISTORY_PATH) -> Optional[Dict[str, str]]:
    """Agent-orchestration payload: prompt + cwd for one goal."""
    for g in scan(goals_dir, history_path):
        if g["name"] != name:
            continue
        opens = []
        try:
            with open(g["file"], errors="replace") as f:
                for line in f:
                    m = _BOX.match(line)
                    if m and m.group(1) == " ":
                        opens.append(m.group(2))
        except OSError:
            pass
        prompt = ("Long-term goal: %s (%d/%d milestones done, %.0f%%).\n"
                  "Open milestones, in order:\n%s\n"
                  "Take the FIRST open milestone and drive it to done. When it is "
                  "verifiably complete, tick its checkbox in %s and stop.\n"
                  "Report progress back so the dashboard shows what you are doing: "
                  "run `meditate progress %s \"<one line: what you are doing now>\"` "
                  "when you start, at each real step, and `meditate progress %s "
                  "--done \"<result>\"` at the end.\n"
                  "This workspace already holds verified facts about this "
                  "project — ask before you rediscover: "
                  "`meditate recall \"<your question>\"` returns graded "
                  "memories with the file and line each came from. Prefer them "
                  "over guessing; if one contradicts what you find, that fact "
                  "is stale — say so in your progress line.\n"
                  "Ship discipline: commit to a LOCAL branch and stop — do NOT "
                  "push or deploy. ONE exception: if this milestone's own text "
                  "names a push/deploy, that exact push is pre-authorized by the "
                  "owner, for that milestone only."
                  % (g["title"], g["done"], g["total"], g["pct"],
                     "\n".join("  - " + o for o in opens), g["file"],
                     name, name))
        return {"name": name, "cwd": g["cwd"] or os.path.expanduser("~"),
                "prompt": prompt, "model": g.get("model", "")}
    return None


# ---------------------------------------------------------------------------
# mined goals: work no goal file carries
# ---------------------------------------------------------------------------
#
# 2026-09-03: 49 sessions touched the Meta ads campaign, two memory files
# recorded its state and its blockers, and the all-goals run could not see
# any of it — it builds from goal files alone. A candidate is a `type:
# project` memory whose words appear in no goal file, ranked by an open
# marker in its description and by how many recent sessions touch it.
# Accepting one writes a goal file. Deterministic, no model call.

_STOP = {"the", "and", "for", "are", "was", "not", "but", "you", "all", "can",
         "has", "had", "its", "our", "out", "new", "one", "two", "use", "via",
         "per", "now", "see", "set", "get", "run", "did", "who", "how", "why",
         "with", "from", "that", "this", "into", "live", "project", "memory",
         "open", "next", "fixed", "done", "working", "shipped", "setup",
         "system", "goal", "goals", "purangpt", "meditate", "claude", "session",
         "sessions", "still", "have", "been", "were", "just", "only", "then",
         "when", "what", "which", "also", "over", "under", "after", "before"}
_OPEN_MARKERS = ("⚠", "⏳", "blocked", "waiting", "open:", "next:", "todo",
                 "unbuilt", "dormant", "pending", "not yet", "needs ")
# A memory whose description says the thing is over is a record, not work.
# "BOX DEAD", "SUPERSEDED TWICE" and "torn down" all carried an open marker
# and ranked above live threads the first time this ran.
_CLOSED_MARKERS = ("dead", "superseded", "torn down", "reverted", "tombstone",
                   "abandoned", "decommissioned", "retired", "archived")
# Postmortems and gotchas are lessons, not goals.
_NOT_A_GOAL = ("bug", "gotcha", "incident", "outage", "postmortem", "trap",
               "blind-spot", "hijack")


def _tokens(text: str) -> set:
    # three-letter words count: "ads" is the word that names the Meta ads
    # work, and a four-letter floor made 49 sessions invisible to it
    return {w for w in re.findall(r"[a-z][a-z0-9]{2,}", (text or "").lower())
            if w not in _STOP}


def _read_memory(path: str) -> Optional[Dict[str, Any]]:
    try:
        raw = open(path, errors="replace").read()
    except OSError:
        return None
    if not raw.startswith("---"):
        return None
    end = raw.find("\n---", 3)
    if end < 0:
        return None
    fm, body = raw[3:end], raw[end + 4:]
    name = desc = mtype = ""
    for ln in fm.splitlines():
        k, _, v = ln.partition(":")
        k, v = k.strip(), v.strip().strip('"')
        if k == "name":
            name = v
        elif k == "description":
            desc = v
        elif k == "type":
            mtype = v
    if not mtype and "type: project" in fm:
        mtype = "project"
    return {"name": name or os.path.basename(path)[:-3], "description": desc,
            "type": mtype, "body": body, "path": path}


DISCARD_LEDGER = os.path.expanduser("~/.claude/meditation/discarded.jsonl")


def discarded_names(ledger: Optional[str] = None) -> set:
    """Everything the owner said no to. Read first, so nothing resurfaces."""
    out: set = set()
    try:
        with open(ledger or DISCARD_LEDGER) as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if r.get("restored"):
                    out.discard(r.get("name"))
                elif r.get("name"):
                    out.add(r["name"])
    except OSError:
        pass
    return out


def mine(memory_dir: Optional[str] = None, goals_dir: str = GOALS_DIR,
         sessions: Optional[List[Dict[str, Any]]] = None,
         now: Optional[str] = None, limit: int = 8,
         ledger: Optional[str] = None) -> List[Dict[str, Any]]:
    """Project memories that no goal file covers, with evidence."""
    import glob as _g
    gone = discarded_names(ledger)
    if memory_dir is None:
        # paths.py is the one file allowed to name a conventional location;
        # a fallback literal here tripped the packaging gate the same hour
        memory_dir = paths.memory_root()
    # every word any goal already uses — name, title, project, milestones
    covered: set = set()
    for gp in _g.glob(os.path.join(goals_dir, "*.md")):
        try:
            covered |= _tokens(open(gp, errors="replace").read())
        except OSError:
            pass
    # sessions in the last 30 days, by the words in their titles and intents
    if sessions is None:
        # the full session scan reads every transcript (~30 s over 360);
        # callers that cannot afford it pass sessions=[] and lose only the
        # "N sessions in 30 days" evidence, never a candidate
        try:
            import sessions as _s
            r = _s.scan_all_projects()
            # the JSON contract: {tool_name, success, data, ...}; rows under data
            d = r.get("data") if isinstance(r, dict) else r
            if isinstance(d, dict):
                d = d.get("sessions") or d.get("rows") or []
            sessions = [x for x in (d or []) if isinstance(x, dict)]
        except Exception:
            sessions = []
    now_ts = now or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    cutoff = now_ts[:10]
    try:
        import datetime as _dt
        cutoff = (_dt.datetime.strptime(now_ts[:10], "%Y-%m-%d") - _dt.timedelta(days=30)).strftime("%Y-%m-%d")
    except ValueError:
        pass
    recent = []
    for srow in sessions or []:
        if (srow.get("ts_end") or "")[:10] >= cutoff:
            words = _tokens(" ".join([str(srow.get("title") or "")]
                                     + [str(u) for u in (srow.get("user_messages") or [])[:40]]))
            recent.append((words, (srow.get("ts_end") or "")[:10]))
    out: List[Dict[str, Any]] = []
    # the memory root holds one directory per project slug (276 files under
    # -Users-badenath-projects-vedic-puran on the day this was written);
    # a flat dir is what the tests hand in. Read both shapes.
    paths_ = sorted(_g.glob(os.path.join(memory_dir, "*.md"))
                    + _g.glob(os.path.join(memory_dir, "*", "*.md")))
    for mp in paths_:
        if os.path.basename(mp) == "MEMORY.md":
            continue
        if ".discarded" in mp.split(os.sep):
            continue
        m = _read_memory(mp)
        if not m or m["type"] != "project" or m["name"] in gone:
            continue
        name_words = _tokens(m["name"].replace("-", " "))
        if not name_words:
            continue
        # covered = most of the memory's NAME words already live in a goal
        hit = len(name_words & covered) / float(len(name_words))
        if hit >= 0.5:
            continue
        if any(w in m["name"] for w in _NOT_A_GOAL):
            continue
        desc_l = m["description"].lower()
        closed = any(mk in desc_l for mk in _CLOSED_MARKERS)
        open_signal = (not closed) and any(mk in desc_l for mk in _OPEN_MARKERS)
        touched = [d for words, d in recent if len(words & name_words) >= max(1, min(2, len(name_words)))]
        cwd = ""
        mm = re.search(r"(/Users/[^\s`'\")]+|~/[^\s`'\")]+)", m["body"])
        if mm:
            cwd = os.path.expanduser(mm.group(1).rstrip(".,;:"))
        # milestones: the description's sentences that read as work
        sugg = []
        for sent in re.split(r"[.;—]\s+|\s—\s", m["description"]):
            sent = sent.strip(" \"'✅⚠️⏳⭐")
            if len(sent) > 12 and any(mk.strip(":") in sent.lower() for mk in _OPEN_MARKERS):
                sugg.append(sent[:140])
        if not sugg:
            sugg = ["Define the milestones for this work (mined from memory, not yet scoped)"]
        # with session evidence in hand, a closed or untouched memory is not
        # a proposal — only when there is no evidence at all does open_signal
        # carry it alone
        if closed or (sessions and not touched and not open_signal):
            continue
        title = re.split(r"[.—]\s", m["description"].strip(" \"'"))[0].strip(" ✅⚠️⏳⭐")[:90] or m["name"]
        out.append({"name": m["name"], "title": title, "cwd": cwd,
                    "suggested_milestones": sugg[:5],
                    "evidence": {"memory": mp, "sessions_30d": len(touched),
                                 "last_active": max(touched) if touched else "",
                                 "open_signal": open_signal,
                                 "goal_word_overlap": round(hit, 2)}})
    # SESSION THREADS with no memory and no goal. Organic capture while
    # searching: the tool must not depend on somebody having written a
    # memory first. Two or more recent sessions sharing two subject words
    # that no goal and no candidate memory names is a thread; one session
    # is a visit.
    named = set()
    for c in out:
        named |= _tokens(c["name"].replace("-", " "))
    named |= covered
    pairs: Dict[tuple, List[tuple]] = {}
    for words, d in recent:
        subj = sorted(w for w in words if w not in named)
        # anchor on the two rarest-looking subject words a session carries:
        # every pair the session has, so two sessions sharing any pair meet
        for i in range(len(subj)):
            for j in range(i + 1, min(len(subj), i + 6)):
                pairs.setdefault((subj[i], subj[j]), []).append(d)
    seen_pairs: set = set()
    threads: List[Dict[str, Any]] = []
    for (a, b), days in sorted(pairs.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(days) < 2 or a in seen_pairs or b in seen_pairs:
            continue
        seen_pairs |= {a, b}
        name = "%s-%s" % (a, b)
        if name in gone:
            continue
        threads.append({"name": name, "title": "%s %s — a thread across %d sessions, no goal and no memory yet"
                        % (a, b, len(days)), "cwd": "",
                        "suggested_milestones": ["Define the milestones for this thread (mined from sessions)"],
                        "evidence": {"memory": "", "source": "sessions", "sessions_30d": len(days),
                                     "last_active": max(days), "open_signal": False,
                                     "goal_word_overlap": 0.0}})
        if len(threads) >= 4:
            break
    for c in out:
        c["evidence"].setdefault("source", "memory")
    out.extend(threads)
    out.sort(key=lambda c: (-int(c["evidence"]["open_signal"]), -c["evidence"]["sessions_30d"],
                            c["evidence"]["last_active"] and -int(c["evidence"]["last_active"].replace("-", "") or 0)))
    return out[:limit]


def _ledger_write(ledger: Optional[str], row: Dict[str, Any]) -> None:
    p = ledger or DISCARD_LEDGER
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps(row) + "\n")


def _tombstone_store(store_dir: Optional[str], sources: List[str]) -> int:
    """Rows in the graded store whose evidence cites a discarded memory go
    inactive with a `discarded` flag. The store never hard-deletes; neither
    does this."""
    if not store_dir or not sources:
        return 0
    path = os.path.join(store_dir, "memories.jsonl")
    if not os.path.exists(path):
        return 0
    names = {os.path.basename(x) for x in sources}
    rows, n = [], 0
    with open(path, errors="replace") as f:
        for ln in f:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            hit = any(os.path.basename(str(e.get("source") or "")) in names
                      for e in (r.get("evidence") or []) if isinstance(e, dict))
            if hit and r.get("active"):
                r["active"] = False
                r.setdefault("flags", [])
                if "discarded" not in r["flags"]:
                    r["flags"].append("discarded")
                n += 1
            rows.append(r)
    if n:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        os.replace(tmp, path)
    return n


def _move_memory(src: str, memory_dir: str) -> str:
    """Move a memory file under .discarded (reversible) and drop its line
    from the MEMORY.md index beside it."""
    if not src or not os.path.exists(src):
        return ""
    dst_dir = os.path.join(memory_dir, ".discarded")
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, os.path.basename(src))
    os.replace(src, dst)
    idx = os.path.join(os.path.dirname(src), "MEMORY.md")
    if os.path.exists(idx):
        try:
            base = os.path.basename(src)
            lines = open(idx, errors="replace").read().splitlines(True)
            keep = [l for l in lines if base not in l]
            if len(keep) != len(lines):
                open(idx, "w").write("".join(keep))
        except OSError:
            pass
    return dst


def discard_mined(cand: Dict[str, Any], memory_dir: Optional[str] = None,
                  ledger: Optional[str] = None, store_dir: Optional[str] = "",
                  reason: str = "") -> Dict[str, Any]:
    """The owner says no to a proposed goal. Its memory moves to .discarded
    (never deleted), the store rows citing it go inactive, and the ledger
    keeps it from coming back — by name, so a session thread with no memory
    is discarded the same way."""
    name = cand.get("name") or ""
    if not name:
        return {"ok": False, "why": "no name"}
    if memory_dir is None:
        memory_dir = paths.memory_root()
    if store_dir == "":
        store_dir = paths.store_dir()
    src = (cand.get("evidence") or {}).get("memory") or ""
    moved = _move_memory(src, memory_dir) if src else ""
    tomb = _tombstone_store(store_dir, [src] if src else [])
    _ledger_write(ledger, {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                           "name": name, "kind": "mined", "memory": src, "moved": moved,
                           "tombstoned": tomb, "reason": reason})
    return {"ok": True, "name": name, "moved": moved, "tombstoned": tomb}


def discard_goal(name: str, goals_dir: str = GOALS_DIR, memory_dir: Optional[str] = None,
                 ledger: Optional[str] = None, store_dir: Optional[str] = "",
                 reason: str = "") -> Dict[str, Any]:
    """The owner drops a goal. Its file moves to goals/.discarded; the
    memories its Note cites by name (and any memory named like the goal)
    move with it and their store rows go inactive."""
    gp = os.path.join(goals_dir, name + ".md")
    if not os.path.exists(gp):
        return {"ok": False, "why": "no goal file %s" % gp}
    if memory_dir is None:
        memory_dir = paths.memory_root()
    if store_dir == "":
        store_dir = paths.store_dir()
    text = open(gp, errors="replace").read()
    cited = set(re.findall(r"\b([a-z0-9]+(?:-[a-z0-9]+){1,6})\b", text.split("## Note")[-1])) if "## Note" in text else set()
    cited.add(name)
    import glob as _g
    moved: List[str] = []
    for mp in _g.glob(os.path.join(memory_dir, "*.md")) + _g.glob(os.path.join(memory_dir, "*", "*.md")):
        if ".discarded" in mp.split(os.sep) or os.path.basename(mp) == "MEMORY.md":
            continue
        if os.path.basename(mp)[:-3] in cited:
            d = _move_memory(mp, memory_dir)
            if d:
                moved.append(d)
    tomb = _tombstone_store(store_dir, moved)
    dst_dir = os.path.join(goals_dir, ".discarded")
    os.makedirs(dst_dir, exist_ok=True)
    os.replace(gp, os.path.join(dst_dir, name + ".md"))
    _ledger_write(ledger, {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                           "name": name, "kind": "goal", "memories_moved": moved,
                           "tombstoned": tomb, "reason": reason})
    return {"ok": True, "name": name, "memories_moved": moved, "tombstoned": tomb,
            "goal_moved_to": os.path.join(dst_dir, name + ".md")}


def restore_discarded(name: str, memory_dir: Optional[str] = None, goals_dir: str = GOALS_DIR,
                      ledger: Optional[str] = None) -> Dict[str, Any]:
    """Put a discarded memory or goal back. The store rows stay flagged
    until the next grade pass re-imports the file."""
    if memory_dir is None:
        memory_dir = paths.memory_root()
    back: List[str] = []
    import glob as _g
    for src in _g.glob(os.path.join(memory_dir, ".discarded", "*.md")):
        if os.path.basename(src)[:-3] == name:
            dst = os.path.join(memory_dir, os.path.basename(src))
            os.replace(src, dst)
            back.append(dst)
    gsrc = os.path.join(goals_dir, ".discarded", name + ".md")
    if os.path.exists(gsrc):
        os.replace(gsrc, os.path.join(goals_dir, name + ".md"))
        back.append(os.path.join(goals_dir, name + ".md"))
    _ledger_write(ledger, {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                           "name": name, "restored": True, "back": back})
    return {"ok": True, "name": name, "back": back}


def accept_mined(cand: Dict[str, Any], goals_dir: str = GOALS_DIR) -> Dict[str, Any]:
    """Write the goal file for a mined candidate. Never overwrites: a goal
    file the owner has touched is his, and a second accept must not clobber
    it."""
    name = re.sub(r"[^a-z0-9-]+", "-", (cand.get("name") or "").lower()).strip("-")
    if not name:
        return {"ok": False, "why": "candidate has no name"}
    path = os.path.join(goals_dir, name + ".md")
    if os.path.exists(path):
        return {"ok": False, "why": "goal file already exists: %s" % path, "path": path}
    project = name.split("-")[0]
    lines = ["---", "name: %s" % name, "title: %s" % (cand.get("title") or name),
             "project: %s" % project,
             "cwd: %s" % (cand.get("cwd") or os.path.expanduser("~")),
             "status: active", "---", "## Milestones"]
    for m in cand.get("suggested_milestones") or ["Define the milestones"]:
        lines.append("- [ ] %s" % m)
    ev = cand.get("evidence") or {}
    lines += ["", "## Note",
              "Mined on %s from memory %s — %d sessions touched this in the last 30 days"
              " and no goal file carried it. Edit freely; the checkboxes are the measurement."
              % (time.strftime("%Y-%m-%d"), os.path.basename(ev.get("memory", "?")),
                 int(ev.get("sessions_30d") or 0))]
    try:
        os.makedirs(goals_dir, exist_ok=True)
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        return {"ok": False, "why": str(e)[:120]}
    return {"ok": True, "path": path, "name": name}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="meditate goals", description="Long-term goals, measured")
    ap.add_argument("cmd", nargs="?", default="list",
                    help="list | show <name> | launch <name>")
    ap.add_argument("name", nargs="?")
    ap.add_argument("--open", action="store_true",
                    help="with launch: open a Terminal running claude on it")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    gs = scan()
    env = {"tool_name": "meditate_goals", "success": True,
           "data": {"count": len(gs), "goals": gs},
           "metadata": {"goals_dir": GOALS_DIR, "history": HISTORY_PATH},
           "errors": []}

    if args.cmd == "launch" and args.name:
        k = kickoff(args.name)
        if not k:
            print("no such goal: %s" % args.name)
            return 0
        if args.open:
            try:
                from launch import launch_claude
                ok = launch_claude(k["cwd"], k["prompt"], k["name"])
                print("opened Terminal on goal %s" % k["name"] if ok
                      else "could not open Terminal — command below")
            except Exception as e:
                print("launch unavailable (%s) — command below" % e)
                ok = False
        else:
            ok = False
        if not args.open or not ok:
            print("\ncd %r && claude %r\n" % (k["cwd"], k["prompt"]))
        return 0

    if args.cmd == "show" and args.name:
        for g in gs:
            if g["name"] == args.name:
                print(json.dumps(g, indent=2))
                return 0
        print("no such goal: %s" % args.name)
        return 0

    if args.json:
        print(json.dumps(env, indent=2))
        return 0

    if not gs:
        print("No goals yet. Add .md files with '## Milestones' checkboxes to %s"
              % GOALS_DIR)
        return 0
    print("Goals — measured from milestone checkboxes")
    print("=" * 56)
    for g in gs:
        bar_n = int(g["pct"] / 5)
        bar = "█" * bar_n + "░" * (20 - bar_n)
        widen = "  scope +%d" % g["scope_delta"] if g["scope_delta"] > 0 else ""
        print("  %-24s %s %5.1f%%  %d/%d%s" %
              (g["name"][:24], bar, g["pct"], g["done"], g["total"], widen))
        if g["next"]:
            print("      next: %s" % g["next"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
