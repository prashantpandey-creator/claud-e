#!/usr/bin/env python3
"""sessions — parse Claude Code session transcripts into a COMPACT map.

This is the deterministic core of /meditate's session mode (Rule 0). A single
session transcript can be 35 MB; this tool streams it line-by-line and emits a
small, capped record per session so the orchestrator reasons over the *map*, not
the raw transcript. The judgment half (splitting a session into per-thread
continuation chats) consumes only this `data`.

Transcript shape (line-delimited JSON): each line has a `type`
(user/assistant/system/ai-title/summary/queue-operation/…). Conversation lines
carry `message: {role, content}` where content is a string OR a list of parts
({type:text}, {type:tool_use, name, input}, {type:tool_result}). Files touched =
Edit/Write/NotebookEdit tool_use `input.file_path`. Thread boundaries = `mark_chapter`
tool_use `input.title`. Titles = the last `ai-title` line.

Descriptor:
  { "tool_name": "sessions",
    "input_schema":  { "project_dir": "path", "cap": "int" },
    "output_schema": { "count": "int", "sessions": [ {
        "session_id","title","cwd","git_branch","size_bytes","line_count",
        "ts_start","ts_end","counts":{user,assistant},
        "first_user","last_user","last_assistant_text",
        "user_messages":[{ts,text}], "chapter_marks":[{ts,title}],
        "files_touched":[...], "projects":[...], "top_tools":[[name,n]],
        "sprawl_score" } ] } }
Envelope: { "success", "data", "metadata", "errors" }
"""
import argparse
import glob
import json
import os
import sys

SNIPPET = 220          # max chars per captured message
DEFAULT_CAP = 40       # max user intents kept per session (true count still reported)
MAX_FILES = 60
MAX_CHAPTERS = 60


def _iter_objs(path):
    with open(path, "r", errors="replace") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                yield json.loads(ln)
            except (ValueError, TypeError):
                continue


def _trunc(s, n=SNIPPET):
    if not isinstance(s, str):
        return s
    s = s.strip()
    return s if len(s) <= n else s[:n] + "…"


def _user_text(content):
    """Return the human text of a user message, or None if it's tool noise."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text":
                return p.get("text")
    return None


def _is_noise(text):
    if not text or not text.strip():
        return True
    t = text.strip()
    # command wrappers, system reminders, tool-result echoes
    if t.startswith("<"):
        return True
    if t.startswith("[Request interrupted"):
        return True
    return False


def _project_of(file_path):
    """Top-level project dir under .../vedic puran/ (or the parent dir)."""
    marker = "/vedic puran/"
    if marker in file_path:
        rest = file_path.split(marker, 1)[1]
        return rest.split("/", 1)[0] if "/" in rest else rest
    return None


def extract_file(path, cap=DEFAULT_CAP, snippet=SNIPPET):
    title = None
    ai_title = None
    cwd = None
    git_branch = None
    ts_start = ts_end = None
    n_user = n_asst = 0
    user_all = []          # all real user intents (for first/last + capping)
    chapters = []
    files_touched = set()
    projects = set()
    tools = {}
    last_assistant_text = None

    for o in _iter_objs(path):
        t = o.get("type")
        # NOTE: the in-file sessionId is NOT read here. A resumed or compacted
        # transcript carries its ANCESTOR's sessionId in its opening rows, so
        # trusting it collapsed 12 of 132 real transcripts onto 8 ids in the
        # graded store. The filename is the id (set below).
        if t == "custom-title" and o.get("customTitle"):
            title = o["customTitle"]        # keep the LAST title seen
        elif t == "ai-title" and o.get("aiTitle"):
            ai_title = o["aiTitle"]         # fallback only
        if cwd is None and o.get("cwd"):
            cwd = o["cwd"]
        if git_branch is None and o.get("gitBranch"):
            git_branch = o["gitBranch"]
        ts = o.get("timestamp")
        if ts:
            if ts_start is None:
                ts_start = ts
            ts_end = ts

        msg = o.get("message")
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")

        if role == "user":
            text = _user_text(content)
            if not _is_noise(text):
                n_user += 1
                user_all.append({"ts": ts, "text": _trunc(text, snippet)})
        elif role == "assistant":
            n_asst += 1
            if isinstance(content, list):
                for p in content:
                    if not isinstance(p, dict):
                        continue
                    if p.get("type") == "text" and p.get("text", "").strip():
                        last_assistant_text = _trunc(p["text"], snippet)
                    elif p.get("type") == "tool_use":
                        name = p.get("name", "?")
                        tools[name] = tools.get(name, 0) + 1
                        inp = p.get("input") or {}
                        if name in ("Edit", "Write", "NotebookEdit"):
                            fp = inp.get("file_path")
                            if fp:
                                files_touched.add(fp)
                                pr = _project_of(fp)
                                if pr:
                                    projects.add(pr)
                        if name.endswith("mark_chapter") and inp.get("title"):
                            chapters.append({"ts": ts, "title": inp["title"]})

    # cap user intents: keep head + tail, note the elision
    first_user = user_all[0]["text"] if user_all else None
    last_user = user_all[-1]["text"] if user_all else None
    kept = user_all
    if len(user_all) > cap:
        half = cap // 2
        kept = user_all[:half] + [{"ts": None, "text": f"… ({len(user_all) - cap} intents elided) …"}] + user_all[-(cap - half - 1):]

    files_list = sorted(files_touched)[:MAX_FILES]
    chapters = chapters[:MAX_CHAPTERS]
    top_tools = sorted(tools.items(), key=lambda kv: -kv[1])[:12]

    size = os.path.getsize(path)
    # sprawl: how tangled / in-need-of-splitting (deterministic, for ranking)
    sprawl = round(
        (size / 1_000_000) * 1.0           # megabytes
        + len(chapters) * 2.0              # explicit phase shifts
        + max(0, len(projects) - 1) * 3.0  # touched >1 distinct project
        + n_user / 25.0,                   # sheer number of human turns
        1,
    )

    return {
        "session_id": os.path.splitext(os.path.basename(path))[0],
        "file": os.path.basename(path),
        "title": title or ai_title,
        "cwd": cwd,
        "git_branch": git_branch,
        "size_bytes": size,
        "line_count": None,                # filled lazily only if asked (cost)
        "ts_start": ts_start,
        "ts_end": ts_end,
        "counts": {"user": n_user, "assistant": n_asst},
        "first_user": _trunc(first_user, snippet) if first_user else None,
        "last_user": _trunc(last_user, snippet) if last_user else None,
        "last_assistant_text": last_assistant_text,
        "user_messages": kept,
        "chapter_marks": chapters,
        "files_touched": files_list,
        "projects": sorted(projects),
        "top_tools": top_tools,
        "sprawl_score": sprawl,
    }


def scan_sessions(project_dir, cap=DEFAULT_CAP):
    project_dir = os.path.abspath(os.path.expanduser(project_dir))
    errors = []
    if not os.path.isdir(project_dir):
        return {"tool_name": "sessions", "success": False, "data": {},
                "metadata": {"project_dir": project_dir},
                "errors": [{"code": "dir_missing", "message": project_dir}]}

    files = sorted(glob.glob(os.path.join(project_dir, "*.jsonl")),
                   key=os.path.getsize, reverse=True)
    sessions_out = []
    for f in files:
        try:
            sessions_out.append(extract_file(f, cap=cap))
        except OSError as e:
            errors.append({"code": "read_error", "message": f"{os.path.basename(f)}: {e}"})

    # rank most-tangled first (these most need splitting)
    sessions_out.sort(key=lambda s: -s["sprawl_score"])
    return {
        "tool_name": "sessions",
        "success": True,
        "data": {"project_dir": project_dir, "count": len(sessions_out),
                 "sessions": sessions_out},
        "metadata": {"files_scanned": len(files), "cap": cap},
        "errors": errors,
    }


def get_session(project_dir, key, cap=80):
    """Resolve ONE session by filename, session_id, or title substring.

    Returns an envelope with data.session (or success:false if no match). Cheap
    paths (filename / id) avoid parsing the whole project; a title substring
    falls back to scanning titles.
    """
    project_dir = os.path.abspath(os.path.expanduser(project_dir))
    if not os.path.isdir(project_dir):
        return {"tool_name": "sessions", "success": False, "data": {},
                "metadata": {}, "errors": [{"code": "dir_missing", "message": project_dir}]}

    k = key.strip()
    # direct: filename or <id>.jsonl (tolerate a "local_" prefix)
    for cand in (k, k + ".jsonl", k.replace("local_", "") + ".jsonl"):
        p = os.path.join(project_dir, os.path.basename(cand))
        if os.path.isfile(p):
            return {"tool_name": "sessions", "success": True,
                    "data": {"session": extract_file(p, cap=cap)},
                    "metadata": {"matched_by": "file"}, "errors": []}

    # fallback: title substring (parse all, pick highest-sprawl match)
    kl = k.lower()
    best = None
    for f in glob.glob(os.path.join(project_dir, "*.jsonl")):
        rec = extract_file(f, cap=cap)
        title = (rec.get("title") or "").lower()
        if kl in title or kl in rec["session_id"].lower():
            if best is None or rec["sprawl_score"] > best["sprawl_score"]:
                best = rec
    if best:
        return {"tool_name": "sessions", "success": True,
                "data": {"session": best},
                "metadata": {"matched_by": "title"}, "errors": []}
    return {"tool_name": "sessions", "success": False, "data": {},
            "metadata": {}, "errors": [{"code": "no_match", "message": f"no session matching {key!r}"}]}


def _human(env):
    if not env["success"]:
        return "sessions: FAILED — " + json.dumps(env["errors"])
    d = env["data"]
    lines = [f"{d['count']} sessions in {d['project_dir']}", ""]
    for s in d["sessions"][:25]:
        mb = s["size_bytes"] / 1_000_000
        title = s["title"] or "(untitled)"
        ch = len(s["chapter_marks"])
        pr = ",".join(s["projects"][:3])
        lines.append(f"  sprawl {s['sprawl_score']:>5}  {mb:5.1f}MB  "
                     f"{s['counts']['user']:>4}u  ch:{ch:<2} [{pr}]  {title[:48]}")
    return "\n".join(lines)


BASE = os.path.expanduser("~/.claude/projects")


def list_project_dirs():
    """Return all project dirs under ~/.claude/projects/ that have session files."""
    if not os.path.isdir(BASE):
        return []
    dirs = []
    for name in sorted(os.listdir(BASE)):
        full = os.path.join(BASE, name)
        if os.path.isdir(full) and glob.glob(os.path.join(full, "*.jsonl")):
            dirs.append(full)
    return dirs


def scan_all_projects(cap=DEFAULT_CAP):
    """Scan every project under ~/.claude/projects/, merge results."""
    all_sessions = []
    errors = []
    projects_scanned = []
    for pd in list_project_dirs():
        r = scan_sessions(pd, cap=cap)
        if r["success"]:
            projects_scanned.append(pd)
            for s in r["data"]["sessions"]:
                s["_project_dir"] = pd
                s["_project_slug"] = os.path.basename(pd)
                all_sessions.append(s)
        errors.extend(r.get("errors", []))
    all_sessions.sort(key=lambda s: -s["sprawl_score"])
    return {
        "tool_name": "sessions",
        "success": True,
        "data": {
            "projects_scanned": projects_scanned,
            "total_projects": len(projects_scanned),
            "total_sessions": len(all_sessions),
            "sessions": all_sessions,
        },
        "metadata": {"cap": cap},
        "errors": errors,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(prog="meditate sessions", description="Parse Claude Code sessions into a compact map")
    ap.add_argument("--project-dir", default=None,
                    help="Scan one project dir (default: all projects)")
    ap.add_argument("--session", default=None,
                    help="resolve ONE session by file / session_id / title substring")
    ap.add_argument("--cap", type=int, default=DEFAULT_CAP)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list-projects", action="store_true",
                    help="List available project directories and exit")
    ap.add_argument("--all", dest="scan_all", action="store_true", default=True,
                    help="Scan all projects (default)")
    ap.add_argument("--no-all", dest="scan_all", action="store_false",
                    help="Require --project-dir")
    args = ap.parse_args(argv)

    if args.list_projects:
        dirs = list_project_dirs()
        if not dirs:
            print("No project directories found under", BASE)
            return 1
        for d in dirs:
            files = glob.glob(os.path.join(d, "*.jsonl"))
            total_mb = sum(os.path.getsize(f) for f in files) / 1_000_000
            print(f"  {os.path.basename(d):<55s}  {len(files):>3} sessions  {total_mb:>7.1f} MB")
        return 0

    if args.session:
        if not args.project_dir:
            # Search all projects for the session
            for pd in list_project_dirs():
                env = get_session(pd, args.session, cap=max(args.cap, 80))
                if env["success"]:
                    if args.json:
                        print(json.dumps(env, indent=2, ensure_ascii=False))
                    else:
                        s = env["data"]["session"]
                        print(f"{s['title'] or '(untitled)'}  [{s['session_id']}]")
                        print(f"  project: {os.path.basename(pd)}")
                        print(f"  {s['size_bytes']/1_000_000:.1f}MB  {s['counts']['user']}u/"
                              f"{s['counts']['assistant']}a  sprawl {s['sprawl_score']}  "
                              f"chapters {len(s['chapter_marks'])}  projects {s['projects']}")
                    return 0
            print(f"No session matching {args.session!r} in any project", file=sys.stderr)
            return 1
        env = get_session(args.project_dir, args.session, cap=max(args.cap, 80))
        if args.json:
            print(json.dumps(env, indent=2, ensure_ascii=False))
        elif env["success"]:
            s = env["data"]["session"]
            print(f"{s['title'] or '(untitled)'}  [{s['session_id']}]")
            print(f"  {s['size_bytes']/1_000_000:.1f}MB  {s['counts']['user']}u/"
                  f"{s['counts']['assistant']}a  sprawl {s['sprawl_score']}  "
                  f"chapters {len(s['chapter_marks'])}  projects {s['projects']}")
        else:
            print(_human(env))
        return 0 if env["success"] else 1

    if args.project_dir:
        env = scan_sessions(args.project_dir, cap=args.cap)
    else:
        env = scan_all_projects(cap=args.cap)

    if args.json:
        print(json.dumps(env, indent=2, ensure_ascii=False))
    else:
        d = env["data"]
        if "projects_scanned" in d:
            print(f"{d['total_sessions']} sessions across {d['total_projects']} projects\n")
            for s in d["sessions"][:30]:
                mb = s["size_bytes"] / 1_000_000
                title = s["title"] or "(untitled)"
                ch = len(s["chapter_marks"])
                pr = s.get("_project_slug", "")
                print(f"  sprawl {s['sprawl_score']:>5.0f}  {mb:5.1f}MB  "
                      f"{s['counts']['user']:>4}u  ch:{ch:<2}  [{pr[:40]}]  {title[:48]}")
        else:
            print(_human(env))
    return 0 if env["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
