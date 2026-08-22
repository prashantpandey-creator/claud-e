"""coordination — the sangama (confluence) layer for multi-session work.

Three jobs, all deterministic, all served through the hook's additionalContext
channel at the moment they matter — never as prompt bulk:

1. PRESENCE   every Write/Edit records (session, cwd, file, time) in
              ~/.claude/coordination/sessions/<sid>.json. Heartbeat = mtime.
2. COLLISION  a session editing a file another LIVE session touched recently
              gets one calm warning naming the session and the age. A session
              never collides with itself.
3. FACTS      editing a path that graded memories make machine-checked claims
              about serves those claims — once per file per session, capped,
              never unverified ones. Wrong beliefs get corrected exactly when
              the agent is about to act on them.

Plus the correction workflow:
  drift  — list memories whose evidence failed (which claim, which line)
  who    — list live sessions and their recent files

CLI (all exit 0 always; hook-edit always prints valid hook JSON):
  python3 coordination.py hook-edit        # stdin: raw hook payload
  python3 coordination.py session-start    # stdin: raw hook payload; prints text
  python3 coordination.py drift [--json]
  python3 coordination.py who [--json]
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any, Dict, List

# Env overrides exist so tests (and the hook's own test suite) can isolate —
# the live presence dir and graded store are shared state, never a scratchpad.
COORD_DIR = os.environ.get("MEDITATE_COORD_DIR") or os.path.expanduser(
    "~/.claude/coordination/sessions")
STORE_DIR = os.environ.get("MEDITATE_STORE_DIR") or os.path.expanduser(
    "~/.claude/meditation/nidra_store")

LIVE_WINDOW = 3600        # s — heartbeat younger than this = session is live
TOUCH_WINDOW = 7200       # s — file touch younger than this = collision-relevant
DRIFT_WINDOW = 48 * 3600  # s — journal downgrades this recent are "new drift"
PRUNE_AGE = 24 * 3600     # s — presence files older than this get deleted
FACT_CAP = 2              # max facts served per edit
FILE_CAP = 200            # max files remembered per session presence

# Guard rules for file edits (moved here from the hook's bash so file events
# have exactly one decision point; bash keeps the command rules).
PIPELINE_RE = re.compile(r"backend/main\.py|query_processor|deep_research", re.I)
NATIVE_RE = re.compile(r"\.swift|/ios/|native", re.I)
PIPELINE_RULE = ("RULE (fires editing the live chat pipeline): do not wire a NEW "
                 "subsystem into production code without asking first. Propose the "
                 "component and get explicit confirmation before deploying it.")
NATIVE_RULE = ("RULE (fires editing iOS/native): prove the web app source was "
               "untouched (git show --stat) — the owner is highly protective of "
               "the web app.")


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(ts))


def _envelope(tool, success, data, errors=None):
    return {"tool_name": tool, "success": success, "data": data,
            "metadata": {"coord_dir": COORD_DIR, "store_dir": STORE_DIR},
            "errors": errors or []}


# ---- presence ---------------------------------------------------------------

def _log_event(coord_dir: str, etype: str, sid: str, path: str) -> None:
    """Durable one-line record per serve/warn — the efficacy report reads this.
    Presence files self-prune in 24h; without this log the wins are unmeasurable."""
    try:
        root = os.path.dirname(coord_dir.rstrip("/")) or coord_dir
        with open(os.path.join(root, "events.jsonl"), "a") as f:
            f.write(json.dumps({"type": etype, "sid": sid[:16], "path": path,
                                "ts": _iso(time.time())}) + "\n")
    except OSError:
        pass                                   # fail-open: never break the hook


def _pfile(sid: str, coord_dir: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", sid)[:64] or "unknown"
    return os.path.join(coord_dir, safe + ".json")


def load_presence(sid: str, coord_dir: str = COORD_DIR) -> Dict[str, Any]:
    try:
        with open(_pfile(sid, coord_dir)) as f:
            return json.load(f)
    except Exception:
        return {"sid": sid, "cwd": "", "files": {}, "served": []}


def save_presence(p: Dict[str, Any], coord_dir: str = COORD_DIR) -> None:
    os.makedirs(coord_dir, exist_ok=True)
    tmp = _pfile(p["sid"], coord_dir) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(p, f)
    os.replace(tmp, _pfile(p["sid"], coord_dir))


def live_sessions(coord_dir: str = COORD_DIR, exclude: str = "") -> List[Dict[str, Any]]:
    """Sessions with a heartbeat inside LIVE_WINDOW, newest first."""
    out = []
    now = time.time()
    if not os.path.isdir(coord_dir):
        return out
    for fn in os.listdir(coord_dir):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(coord_dir, fn)
        try:
            age = now - os.path.getmtime(path)
            if age > PRUNE_AGE:
                os.unlink(path)          # housekeeping: subtract dead state
                continue
            if age > LIVE_WINDOW:
                continue
            with open(path) as f:
                p = json.load(f)
            if p.get("sid") == exclude:
                continue
            p["_age_s"] = int(age)
            out.append(p)
        except Exception:
            continue
    return sorted(out, key=lambda p: p["_age_s"])


# ---- fact index -------------------------------------------------------------

def _load_index(store_dir: str) -> Dict[str, List[Dict[str, str]]]:
    try:
        with open(os.path.join(store_dir, "path_index.json")) as f:
            return json.load(f)
    except Exception:
        return {}


def facts_for(path: str, served: List[str], store_dir: str = STORE_DIR) -> List[str]:
    """Machine-checked statements about this exact path, minus already-served."""
    idx = _load_index(store_dir)
    entries = idx.get(path) or idx.get(os.path.expanduser(path)) or []
    out = []
    for e in entries:
        if e.get("status") != "machine_checked":
            continue
        key = path + "|" + e.get("statement", "")[:60]
        if key in served:
            continue
        out.append((key, e.get("statement", "").strip()))
        if len(out) >= FACT_CAP:
            break
    return out


# ---- the edit-event handler -------------------------------------------------

def hook_edit(payload: Dict[str, Any],
              coord_dir: str = COORD_DIR, store_dir: str = STORE_DIR) -> str:
    """Record presence; return additionalContext text ('' = stay silent)."""
    ti = payload.get("tool_input") or {}
    path = ""
    for k in ("file_path", "notebook_path", "path"):
        if isinstance(ti, dict) and ti.get(k):
            path = str(ti[k])
            break
    if not path:
        return ""
    sid = str(payload.get("session_id") or "unknown")
    cwd = str(payload.get("cwd") or "")
    now = time.time()

    me = load_presence(sid, coord_dir)
    me.update({"sid": sid, "cwd": cwd})
    me.setdefault("files", {})[path] = now
    if len(me["files"]) > FILE_CAP:                       # bound the record
        for k in sorted(me["files"], key=me["files"].get)[:len(me["files"]) - FILE_CAP]:
            del me["files"][k]
    me.setdefault("served", [])
    me.setdefault("warned", [])

    lines: List[str] = []

    # 1. collision — another live session touched this exact file recently.
    # Warn ONCE per (peer, file) per session: a warning repeated on every edit
    # is pressure, and pressure is the thing this layer exists to remove.
    for other in live_sessions(coord_dir, exclude=sid):
        ts = other.get("files", {}).get(path)
        if ts and (now - ts) < TOUCH_WINDOW:
            wkey = str(other.get("sid", "?"))[:16] + "|" + path
            if wkey in me["warned"]:
                break
            me["warned"].append(wkey)
            mins = max(1, int((now - ts) / 60))
            lines.append(
                "SANGAMA (two rivers, one bed): session %s touched %s %d min ago "
                "and is still live. Check `git status` for their uncommitted work "
                "before overwriting — if you are both mid-change, take a worktree."
                % (other.get("sid", "?")[:8], os.path.basename(path), mins))
            _log_event(coord_dir, "collision_warned", sid, path)
            break                                          # one warning is enough

    # 2. graded facts about this file, once per session per file
    for key, stmt in facts_for(path, me["served"], store_dir):
        lines.append("GRADED FACT (machine-checked) about this file: %s" % stmt)
        me["served"].append(key)
        _log_event(coord_dir, "fact_served", sid, path)

    # 3. guard rules
    if PIPELINE_RE.search(path):
        lines.append(PIPELINE_RULE)
    elif NATIVE_RE.search(path):
        lines.append(NATIVE_RULE)

    save_presence(me, coord_dir)
    return "\n".join(lines)


# ---- session start ----------------------------------------------------------

def _recent_drift(store_dir: str) -> List[str]:
    """Names of memories downgraded to unverified inside DRIFT_WINDOW."""
    jp = os.path.join(store_dir, "journal.jsonl")
    if not os.path.exists(jp):
        return []
    cutoff = time.time() - DRIFT_WINDOW
    hits = {}
    try:
        with open(jp) as f:
            for line in f:
                if "-> unverified" not in line:
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e.get("event") != "sleep.regraded":
                    continue
                ts = e.get("ts", "")
                try:
                    t = time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
                except Exception:
                    continue
                if t >= cutoff:
                    hits[e.get("id", "?")] = e.get("detail", "")
    except Exception:
        return []
    return list(hits)


def session_start(payload: Dict[str, Any],
                  coord_dir: str = COORD_DIR, store_dir: str = STORE_DIR) -> str:
    """Extra SessionStart lines: live sessions + fresh drift. '' when quiet."""
    sid = str(payload.get("session_id") or "unknown")
    cwd = str(payload.get("cwd") or "")
    lines: List[str] = []

    peers = [p for p in live_sessions(coord_dir, exclude=sid) if p.get("cwd") == cwd]
    if peers:
        newest = peers[0]
        recent = sorted(newest.get("files", {}).items(), key=lambda kv: -kv[1])[:2]
        fl = ", ".join(os.path.basename(f) for f, _ in recent) or "no files yet"
        plural = "s" if len(peers) > 1 else ""
        lines.append(
            "Sangama: %d other live session%s in this repo (recent: %s). "
            "Do not stomp their uncommitted work — worktree if you must overlap."
            % (len(peers), plural, fl))

    drifted = _recent_drift(store_dir)
    if drifted:
        lines.append(
            "Drift: %d memor%s downgraded in the last 48h (%s) — the world moved; "
            "run `meditate drift` to see the failing claims before trusting them."
            % (len(drifted), "y" if len(drifted) == 1 else "ies",
               ", ".join(drifted[:3])))

    # Repair queue: caught drift is standing work until a grade pass clears it.
    qp = os.path.join(os.path.dirname(store_dir.rstrip("/")), "repair-queue.md")
    if os.path.exists(qp):
        lines.append("Repair queue: knowledge failed verification — see %s "
                     "(fix the .md, then `meditate grade` to clear)." % qp)

    # North-star nudge: the long-term goal governing this directory, one line.
    # Fail-open — a broken goals file must never cost the session its rules.
    try:
        from goals import goal_for_cwd
        gline = goal_for_cwd(cwd)
        if gline:
            lines.append(gline)
    except Exception:
        pass
    return "\n".join(lines)


# ---- correction workflow ----------------------------------------------------

def drift_report(store_dir: str = STORE_DIR) -> Dict[str, Any]:
    """Every active memory whose evidence currently fails, with the exact claim."""
    mp = os.path.join(store_dir, "memories.jsonl")
    out = []
    if os.path.exists(mp):
        with open(mp) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    m = json.loads(line)
                except Exception:
                    continue
                if not m.get("active"):
                    continue
                status = m.get("epistemic", {}).get("evidence_status")
                flagged = m.get("flags") or []
                if status != "unverified" and not flagged:
                    continue
                failing = []
                for ev in m.get("evidence", []):
                    loc = str(ev.get("locator", ""))
                    if loc.startswith("path:") and not os.path.exists(
                            os.path.expanduser(loc[5:])):
                        failing.append({"claim": loc, "line": ev.get("excerpt", "")})
                    elif loc.startswith("wikilink:") and ev.get("source") and \
                            not os.path.exists(ev["source"]):
                        failing.append({"claim": loc, "line": ev.get("excerpt", "")})
                out.append({"id": m.get("id"), "statement": m.get("statement", "")[:160],
                            "status": status, "flags": flagged, "failing": failing})
    return {"count": len(out), "memories": out}


# ---- CLI --------------------------------------------------------------------

def _emit_hook_json(event: str, msg: str) -> None:
    if msg:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": event, "additionalContext": msg}}))
    else:
        print("{}")


def main(argv: List[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "hook-edit":
        try:
            payload = json.load(sys.stdin)
            if not isinstance(payload, dict):
                payload = {}
        except Exception:
            payload = {}
        try:
            msg = hook_edit(payload)
        except Exception:
            msg = ""
        _emit_hook_json("PreToolUse", msg)
        return 0
    if cmd == "session-start":
        try:
            payload = json.load(sys.stdin)
            if not isinstance(payload, dict):
                payload = {}
        except Exception:
            payload = {}
        try:
            print(session_start(payload), end="")
        except Exception:
            pass
        return 0
    if cmd == "drift":
        rep = drift_report()
        env = _envelope("meditate_drift", True, rep)
        if "--json" in argv:
            print(json.dumps(env, indent=2))
        else:
            print("Drifted / contested memories: %d" % rep["count"])
            for m in rep["memories"]:
                print("  %s  [%s]%s" % (m["id"], m["status"],
                                        " flags:" + ",".join(m["flags"]) if m["flags"] else ""))
                print("     %s" % m["statement"])
                for fchip in m["failing"]:
                    print("     FAILS %s" % fchip["claim"])
                    if fchip["line"]:
                        print("       line: %s" % fchip["line"][:120])
        return 0
    if cmd == "who":
        peers = live_sessions()
        data = {"live": [{"sid": p.get("sid", "")[:12], "cwd": p.get("cwd", ""),
                          "age_s": p.get("_age_s"),
                          "recent_files": sorted(p.get("files", {}),
                                                 key=p.get("files", {}).get)[-3:]}
                         for p in peers]}
        env = _envelope("meditate_who", True, data)
        if "--json" in argv:
            print(json.dumps(env, indent=2))
        else:
            if not data["live"]:
                print("No live sessions.")
            for s in data["live"]:
                print("  %s  %s  (%ss ago)  %s" %
                      (s["sid"], s["cwd"], s["age_s"], ", ".join(
                          os.path.basename(f) for f in s["recent_files"])))
        return 0
    print("usage: coordination.py hook-edit|session-start|drift|who [--json]",
          file=sys.stderr)
    return 0  # never break a hook chain


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
