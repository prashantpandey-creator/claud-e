"""tree — everything you have going, as ONE tree you can open a branch at a time.

The problem this solves: the tool knew all of it and showed none of it
together. Goals were a table, dormant repos a list, live sessions a roster,
the repair queue a file. Six flat surfaces, so "where is my work" was a
question you answered by reading six things and holding them in your head.

A tree is the right shape because the work IS nested — a product has goals, a
goal has milestones, a milestone has the evidence that says whether it is
done. Flattening that loses the only thing that makes it readable: what
belongs under what.

Two rules, both of which cost something to keep:

  EVERY NODE CARRIES ITS MEANING. Not a number — a sentence saying what the
  number means for you. "6 of 8" is a readout; "waiting on Apple, nothing you
  can do today" is the answer. Where no honest meaning exists the node says
  so rather than padding.

  NOTHING IS SYNTHESISED. Every line is a field that already exists or a
  quote from disk. The tree arranges; it never decides. This is the same law
  the revival cards follow, for the same reason — a plausible sentence with a
  tree node around it is still a made-up sentence.

    meditate tree                # collapsed: the branches and their counts
    meditate tree --open         # everything expanded
    meditate tree --json
    meditate tree --html         # write it into the dashboard page
"""
from __future__ import annotations

import argparse
import html as _html
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)


def _node(label: str, meaning: str = "", children: Optional[List] = None,
          count: Optional[int] = None, action: str = "",
          kind: str = "") -> Dict[str, Any]:
    return {"label": label, "meaning": meaning, "children": children or [],
            "count": count if count is not None else len(children or []),
            "action": action, "kind": kind}


def _ago(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    m = int(seconds // 60)
    if m < 1:
        return "just now"
    if m < 60:
        return "%d min ago" % m
    h = m // 60
    if h < 24:
        return "%d hour%s ago" % (h, "" if h == 1 else "s")
    return "%d days ago" % (h // 24)


# ---------------------------------------------------------------------------
# the branches
# ---------------------------------------------------------------------------

def _moving(d: Dict[str, Any]) -> Dict[str, Any]:
    """Goals with a milestone still open — the work that has a yardstick."""
    kids = []
    for g in d.get("goals") or []:
        done, total = g.get("done") or 0, g.get("total") or 0
        if total and done >= total:
            continue
        nxt = (g.get("next") or "").strip()
        # scope_delta is the honest half of a percentage: a goal can go DOWN
        # because it grew, and hiding that turns widening into fake regression.
        drift = g.get("scope_delta") or 0
        grew = (" · scope grew by %d since you started" % drift) if drift else ""
        ms = []
        try:
            from goals import detail as _detail
            det = _detail(g.get("name") or "") or {}
            from goals import _headline
            for m in det.get("milestones", []):
                mark = "done" if m.get("done") else "open"
                raw = m.get("text") or m.get("title") or "?"
                # Milestones here accumulate the whole research note inline —
                # one of them runs 3,400 characters. goals._headline already
                # cuts at the first em-dash or bold marker, which is exactly
                # where the note starts; reuse it rather than writing a second
                # trimmer that drifts from the first.
                # The notes also arrive as HTML comments (`<!-- 2026-08-22: … -->`),
                # which _headline does not know about — it cuts on em-dash and
                # bold. Strip the comment first so the label is the milestone
                # and the note becomes the meaning line under it.
                import re as _re
                body = _re.sub(r"<!--.*?-->", "", raw, flags=_re.S).strip()
                inline = " ".join(_re.findall(r"<!--(.*?)-->", raw, flags=_re.S)).strip()
                label = _headline(body) or body[:90]
                note = (body[len(label):] + " " + inline).strip(" —-*:")
                why = (str(m.get("evidence") or m.get("verdict") or "")).strip() or note
                ms.append(_node(label, why[:200], kind="milestone:" + mark))
        except Exception:
            pass
        kids.append(_node(
            g.get("title") or g.get("name") or "?",
            "%d of %d done%s%s" % (done, total, grew,
                                   (" · next: " + nxt) if nxt else ""),
            ms, count=total, action="go " + (g.get("name") or ""),
            kind="goal"))
    return _node("MOVING", "work with a yardstick on it", kids, kind="moving")


def _live(d: Dict[str, Any]) -> Dict[str, Any]:
    """Sessions open right now, and what each is actually touching.

    A session with no file touched is reported as idle rather than dropped —
    10 of 16 read that way on 2026-08-26, and calling them all "live" was the
    thing that made the roster untrustworthy.
    """
    rows = d.get("live_sessions") or []
    # working = touched something inside 180s. Sorting by it puts the ones
    # actually moving at the top; before this the branch was in age order and
    # a person read the first five as "what is happening", when four of them
    # had done nothing for twenty minutes.
    rows = sorted(rows, key=lambda s: (s.get("state") != "working",
                                       s.get("age_s") or 0))
    kids = []
    for s in rows:
        f = (s.get("last_file") or "").strip()
        working = s.get("state") == "working"
        if working:
            meaning = ("in %s" % os.path.basename(f)) if f else "moving"
        else:
            meaning = "idle" + ((" · last in %s" % os.path.basename(f)) if f else "")
        age = _ago(s.get("age_s"))
        if age:
            meaning += " · " + age
        kids.append(_node(s.get("label") or s.get("sid") or "?", meaning,
                          kind="session:" + ("working" if working else "idle"),
                          action="tell " + (s.get("sid") or "")))
    n_work = sum(1 for s in rows if s.get("state") == "working")
    return _node("OPEN SESSIONS",
                 "%d moving, %d idle" % (n_work, len(rows) - n_work)
                 if rows else "nothing open",
                 kids, kind="live")


def _left(d: Dict[str, Any]) -> Dict[str, Any]:
    """Started and left. Every line here is a quote from the repo itself."""
    kids = []
    for c in d.get("dormant") or []:
        # through _spoken_commit: a subject reads `fix(security): gate trust
        # verify behind admin role` in git and is unspeakable as-is.
        try:
            from distill_speech import _spoken_commit as _sc
        except Exception:
            _sc = lambda x: x
        bits = ["untouched %s" % (c.get("idle") or "a while"),
                "stopped at: %s" % (_sc(c.get("last_commit") or "") or "?")]
        if c.get("what"):
            bits.append("it calls itself: %s" % c["what"])
        kids.append(_node(c.get("project") or "?", " · ".join(bits),
                          kind="dormant",
                          action="revive " + (c.get("project") or "")))
    return _node("STARTED AND LEFT", "real history, nothing in 30 days", kids,
                 kind="dormant")


def _broken(d: Dict[str, Any]) -> Dict[str, Any]:
    """What you were told that stopped being true."""
    kids = []
    for r in d.get("repair") or []:
        fails = r.get("fails") or []
        # A failing claim reads as `path:/Users/.../thing.md`. Said aloud that
        # became "no longer true: path." — _as_idea strips the path and leaves
        # the bare word behind. Name what actually broke instead; voice.py
        # already says it this way for the same data.
        bits = []
        for f in fails[:2]:
            f = str(f)
            if f.startswith("path:"):
                bits.append("the %s it points to is gone"
                            % os.path.basename(f[5:].rstrip("/")))
            else:
                bits.append(f[:90])
        why = ("no longer true — " + "; ".join(bits)) if bits else "failed its own check"
        kids.append(_node((r.get("statement") or "?")[:110], why, kind="repair"))
    return _node("BROKEN", "knowledge that failed its own check", kids,
                 kind="repair", action="fix" if kids else "")


def _unmeasured(d: Dict[str, Any]) -> Dict[str, Any]:
    """Products you work in that nothing can judge.

    This branch is the one that says what the tree CANNOT tell you, and it is
    deliberately not hidden: four goals cover 76 products, so most of the work
    on this machine has no yardstick and the tool's silence about it reads as
    health. A branch that admits its own blind spot is worth more than four
    that quietly imply coverage.
    """
    kids = []
    try:
        import projects as pj
        gaps = pj.assessment_gaps()
        un = gaps.get("unassessed", [])
        for u in un[:8]:
            kids.append(_node(u["project"],
                              "%d sessions of your attention, no goal — "
                              "nothing here can say if it is going well"
                              % u["sessions"], kind="unassessed"))
        if len(un) > 8:
            # SAY THE CAP. This branch showed 8 and reported its count as 8
            # while the truth was 29 — a silent truncation inside the one
            # branch whose whole job is to admit the blind spot. A node that
            # under-reports its own size is the same defect as a checker that
            # cannot say "I do not know".
            kids.append(_node("… and %d more" % (len(un) - 8),
                              "showing the 8 you work in most",
                              kind="unassessed"))
        head = ("%d products, %d with a goal, %d worked in with none" %
                (gaps.get("real_projects", 0), gaps.get("assessed", 0), len(un)))
    except Exception:
        head = "could not read the project table"
    return _node("NOT MEASURED", head, kids, kind="unassessed")


def _itself(d: Dict[str, Any]) -> Dict[str, Any]:
    """The tool's own failures, in the same tree as everything else.

    The loop it runs for your memories — check, notice the break, queue the
    repair — it did not run on itself. Measured 2026-08-29: heartbeat.log held
    45 identical `osascript error` lines, one class of failure, every one
    after a 75-second timeout, seen by nobody in a log nothing reads. The
    cause was already written in dispatch_one's own docstring: the auto gate
    only fires when you are 20+ minutes away, away means the display is off,
    and osascript cannot drive Terminal without an awake display. So every
    unattended dispatch ran in exactly the state documented to fail.

    A tool that reports on your work and not on itself is asking to be trusted
    on the one subject it has never checked.
    """
    kids = []
    try:
        import doctor as _doc
        f = _doc._check_fleet()
        if f.get("checked") and f.get("dispatched"):
            kids.append(_node(
                "fleet",
                "%d dispatched · %d opened a window · %d of %d headless agents "
                "produced output" % (f["dispatched"], f["with_window"],
                                     f["headless_with_output"], f["headless_logs"]),
                kind="self"))
        if f.get("headless_empty"):
            kids.append(_node("%d agents produced nothing" % f["headless_empty"],
                              "started, wrote a header, and returned no work",
                              kind="self", action="fix"))
    except Exception:
        pass
    # Doctor's own last word — and when it last got one.
    try:
        import json as _json
        led = os.path.expanduser("~/.claude/meditation/doctor.jsonl")
        last = None
        with open(led) as fh:
            for ln in fh:
                try:
                    last = _json.loads(ln)
                except ValueError:
                    continue
        if last:
            age_h = (time.time() - (last.get("ts_epoch") or 0)) / 3600.0
            if last.get("issues"):
                kids.append(_node("self-check: " + ", ".join(last["issues"][:3]),
                                  "doctor's last verdict, %s" % _ago(age_h * 3600),
                                  kind="self"))
            elif age_h > 24:
                # NOT "healthy". A stale green is not a green — this machine
                # sleeps, and the periodic pass fired 60 times in 7 days where
                # hourly would be 168.
                kids.append(_node("self-check has not run in %.0f hours" % age_h,
                                  "the last verdict is too old to trust",
                                  kind="self"))
    except OSError:
        pass

    # Repeated failures nobody has read. Counting the CLASS, not the lines —
    # 45 lines of one error is one problem, and reporting 45 makes it look
    # like 45.
    try:
        log = os.path.expanduser("~/.claude/meditation/heartbeat.log")
        classes: Dict[str, int] = {}
        with open(log, errors="replace") as fh:
            for ln in fh:
                for mark in ("osascript error", "Traceback", "Error:"):
                    if mark in ln:
                        classes[mark] = classes.get(mark, 0) + 1
                        break
        for mark, n in sorted(classes.items(), key=lambda kv: -kv[1]):
            kids.append(_node("%s × %d" % (mark, n),
                              "same failure, repeated, in a log nothing reads",
                              kind="self"))
    except OSError:
        pass
    if not kids:
        return _node("THE TOOL ITSELF", "nothing of its own is failing", [],
                     kind="self")
    return _node("THE TOOL ITSELF", "what it got wrong, on its own work",
                 kids, kind="self")


def build(d: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The whole tree. Branch order is leverage order, same as the briefing:
    broken knowledge first (it corrupts everything downstream), then what is
    moving, then who is working, then what was left, then the blind spot."""
    if d is None:
        import brain
        d = brain.state()
    branches = [_broken(d), _moving(d), _live(d), _left(d), _unmeasured(d),
                _itself(d)]
    branches = [b for b in branches
                if b["children"] or b["kind"] in ("unassessed", "self")]
    headline = ((d.get("insights") or {}).get("headline")
                or (d.get("briefing") or {}).get("headline") or "")
    return _node("YOUR WORK", headline.strip(), branches, kind="root")


# ---------------------------------------------------------------------------
# renderers
# ---------------------------------------------------------------------------

def to_text(n: Dict[str, Any], expand: bool = False, depth: int = 0,
            last: bool = True, prefix: str = "") -> List[str]:
    out = []
    if depth == 0:
        out.append(n["label"] + ("  —  " + n["meaning"] if n["meaning"] else ""))
    else:
        stem = "└── " if last else "├── "
        head = "%s%s%s" % (prefix, stem, n["label"])
        if n["children"] and not expand:
            head += "  (%d)" % n["count"]
        out.append(head)
        if n["meaning"]:
            pad = prefix + ("    " if last else "│   ")
            out.append("%s%s" % (pad, n["meaning"]))
    kids = n["children"] if (expand or depth == 0) else []
    pad = "" if depth == 0 else prefix + ("    " if last else "│   ")
    for i, k in enumerate(kids):
        out.extend(to_text(k, expand, depth + 1, i == len(kids) - 1, pad))
    return out


def to_html(n: Dict[str, Any], depth: int = 0) -> str:
    """<details>/<summary> — one click opens a branch, and NO javascript.

    The page already had a JS renderer for its own lists; another one here
    would be a second thing to keep in sync for a behaviour the browser
    ships. Native <details> also keeps its open/closed state through a reload
    and works with find-in-page, which a JS accordion has to be taught.
    """
    g, dim = "#E3B140", "#8a8a8a"
    lab = _html.escape(n["label"])
    mean = _html.escape(n["meaning"])
    if not n["children"]:
        # Indented like a branch. Without this a leaf sits at the page margin
        # and stops looking like it belongs to the thing above it — which is
        # the one job a tree has.
        return ('<div style="margin:2px 0 8px 14px">%s%s</div>'
                % (lab, ('<div style="color:%s;font-size:12px">%s</div>' % (dim, mean))
                   if mean else ""))
    head = ('<summary style="cursor:pointer;color:%s">%s <span style="color:%s">'
            '(%d)</span></summary>' % (g if depth < 2 else "inherit", lab, dim, n["count"]))
    body = "".join(to_html(k, depth + 1) for k in n["children"])
    note = ('<div style="color:%s;font-size:12px;margin:2px 0 6px 0">%s</div>'
            % (dim, mean)) if mean else ""
    return ('<details%s style="margin-left:%dpx">%s%s%s</details>'
            % (" open" if depth == 0 else "", 14 if depth else 0, head, note, body))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="meditate tree",
                                 description="everything you have going, as one tree")
    ap.add_argument("--open", action="store_true", help="expand every branch")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--html", action="store_true", help="print the HTML fragment")
    a = ap.parse_args(argv)
    t = build()
    if a.json:
        print(json.dumps({"tool_name": "meditate_tree", "success": True,
                          "data": t, "metadata": {}, "errors": []}, indent=2))
        return 0
    if a.html:
        print(to_html(t))
        return 0
    print("\n".join(to_text(t, expand=a.open)))
    if not a.open:
        print("\n  meditate tree --open   for everything under these")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
