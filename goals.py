"""goals — long-term goals across every project, measured not vibed.

A goal is one .md file in ~/claude-sync/goals/ (synced across machines,
human-editable, same family as the memory files):

    ---
    name: purangpt-ios-live
    title: PuranGPT iOS fully live
    project: purangpt
    cwd: /Users/badenath/projects/vedic puran
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
import time
from typing import Any, Dict, List, Optional

GOALS_DIR = os.environ.get("MEDITATE_GOALS_DIR") or os.path.expanduser(
    "~/claude-sync/goals")
HISTORY_PATH = os.path.expanduser("~/.claude/meditation/goals-history.jsonl")

_BOX = re.compile(r"^\s*-\s*\[( |x|X)\]\s*(.+?)\s*$")


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
    for line in body.splitlines():
        m = _BOX.match(line)
        if not m:
            continue
        total += 1
        if m.group(1).lower() == "x":
            done += 1
        elif nxt is None:
            nxt = m.group(2)
    if total == 0:
        return None
    name = meta.get("name") or os.path.basename(path)[:-3]
    return {"name": name, "title": meta.get("title", name),
            "project": meta.get("project", ""), "cwd": meta.get("cwd", ""),
            "status": meta.get("status", "active"),
            "model": meta.get("model", ""),
            "done": done, "total": total,
            "pct": round(100.0 * done / total, 1), "next": nxt, "file": path}


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


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Long-term goals, measured")
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
