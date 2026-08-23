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

import ast
import builtins
import json
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

# Env overrides exist so tests (and the hook's own test suite) can isolate —
# the live presence dir and graded store are shared state, never a scratchpad.
COORD_DIR = os.environ.get("MEDITATE_COORD_DIR") or os.path.expanduser(
    "~/.claude/coordination/sessions")
STORE_DIR = os.environ.get("MEDITATE_STORE_DIR") or os.path.expanduser(
    "~/.claude/meditation/nidra_store")

LIVE_WINDOW = 1800        # s — a session still open; longer and it is history
WORKING_S = 180           # s — moved this recently = someone is at it RIGHT NOW
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

def _log_event(coord_dir: str, etype: str, sid: str, path: str,
               mem_id: str = "") -> None:
    """Durable one-line record per serve/warn — the efficacy report reads this.
    Presence files self-prune in 24h; without this log the wins are unmeasurable."""
    try:
        with open(events_path(coord_dir), "a") as f:
            row = {"type": etype, "sid": sid[:16], "path": path,
                   "ts": _iso(time.time())}
            if mem_id:
                row["mem_id"] = mem_id
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass                                   # fail-open: never break the hook


def _pfile(sid: str, coord_dir: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", sid)[:64] or "unknown"
    return os.path.join(coord_dir, safe + ".json")


def events_path(coord_dir: str = COORD_DIR) -> str:
    """The single place the activity log's path is decided.

    Every writer goes through here so that setting MEDITATE_COORD_DIR
    redirects ALL of them at once. Two separate files used to derive this
    path by hand; both leaked test traffic into the owner's real trail.
    """
    return os.path.join(os.path.dirname(coord_dir.rstrip("/")), "events.jsonl")


def last_file(session) -> Optional[str]:
    """The most recently touched file in a presence record, or None.

    A session that has registered but not yet edited anything has files={} —
    and `{}` does not trigger a dict-default, so the inline expression this
    replaces did sorted([])[-1] and raised IndexError. That is every
    brand-new session. It was written out by hand in three places; two of
    them crashed, and fixing them one at a time is how the third survived to
    take down /api/state.
    """
    files = (session or {}).get("files") or {}
    if not files:
        return None
    return os.path.basename(sorted(files, key=files.get)[-1])


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
            # "live" was anything inside an hour, which counted 74 sessions
            # while 10 claude processes existed and 6 transcripts had moved in
            # five minutes. A list that is 7x wrong is a wall, not a console.
            p["_state"] = "working" if age <= WORKING_S else "idle"
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
        out.append((key, e.get("statement", "").strip(), e.get("id", "")))
        if len(out) >= FACT_CAP:
            break
    return out


# ---- the edit-event handler -------------------------------------------------

# Measured 2026-08-23 across 89 real sessions: 24% ran to the context wall
# and compacted (64 events; >=4 sessions bumped the identical 965,923 ceiling)
# instead of splitting. A compaction summary loses thread fidelity; a split
# does not. This is the ONE session-behavior signal that cleared the bar —
# corrections (3.7%) and one-off tool errors (4.0%) are healthy and get no
# harness. Baseline lives in memory `session-behavior-baseline`; KPI is the
# compaction rate, re-measured against that file.
# Two warn bands, because windows differ and a fixed line misses most events:
# re-measured with the falsifying case first — 40 of 56 real compactions (71%)
# happened at ~160k, on standard 200k-window models, far BELOW a fixed 700k
# line. Observed ceilings in this corpus: ~166k (200k window) and ~966k (1M).
# The band is inferred from the LIVE value alone — no model table to go stale:
# 145k-200k means "if this window is 200k you are about to hit it"; crossing
# 200k without compacting proves the window is bigger, so silence resumes
# until the 1M line at 600k (floor set by data: unique compaction events
# dedupe to 50, and one real wall sat at 645k — a 700k floor missed it; 600k
# covers 48/50 = 96%). Literature agrees quality rots before the wall
# (arXiv 2608.01326; production caps below advertised windows), so the early
# line in a 1M session is information, not noise — and it fires once.
CEILING_BANDS = (
    (145_000, 200_000, 20_000,
     "if this model runs a standard 200k window it will compact within ~%dk "
     "tokens; on a 1M window ignore until 600k"),
    (600_000, 966_000, 100_000,
     "~%dk from the 1M ceiling"),
)
_CEILING_TAIL = 262_144        # transcripts reach 25MB — read the tail only


def ceiling_check(payload: Dict[str, Any], me: Dict[str, Any]) -> str:
    """'' unless this session's live context crossed the warn line.

    Reads the LAST usage row from the transcript tail — that is the context
    the next turn will pay for. Debounced via the caller's presence record
    (warn at 700k, again each further 100k, never in between). Unmeasurable
    is silent: no transcript is not the same as over the ceiling.
    """
    tp = str(payload.get("transcript_path") or "")
    if not tp or not os.path.isfile(tp):
        return ""
    try:
        size = os.path.getsize(tp)
        with open(tp, "rb") as fh:
            if size > _CEILING_TAIL:
                fh.seek(-_CEILING_TAIL, 2)
            chunk = fh.read().decode("utf-8", "replace")
    except OSError:
        return ""
    ctx = 0
    for line in reversed(chunk.splitlines()):
        if '"usage"' not in line:
            continue
        try:
            u = (json.loads(line).get("message") or {}).get("usage") or {}
        except Exception:
            continue
        c = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
             + u.get("cache_creation_input_tokens", 0))
        if c:
            ctx = c
            break
    last = int(me.get("ceiling_warned") or 0)
    if last and ctx < last // 2:
        # Context collapsed to under half the high-water mark: a compaction
        # happened. Reset, or the stale mark gags the next climb's warning.
        last = 0
    # Highest band whose floor is crossed. The 200k band also has a hard lid:
    # ctx past 200k without a compaction PROVES the window is bigger, so that
    # band stops applying and silence holds until the 1M floor.
    band = None
    for floor, ceil, step, note in CEILING_BANDS:
        if ctx >= floor and (ctx < ceil or ceil > 200_000):
            band = (floor, ceil, step, note)
    if band is None:
        me["ceiling_warned"] = last
        return ""
    floor, ceil, step, note = band
    if last and ctx < last + step:
        me["ceiling_warned"] = last
        return ""
    me["ceiling_warned"] = ctx
    sid = str(payload.get("session_id") or "unknown")[:8]
    return ("Context %dk — %s. A compaction summary loses threads; a split does "
            "not. Split now: run /meditate %s — it writes per-thread "
            "continuation chats."
            % (ctx // 1000, note % max(1, (ceil - ctx) // 1000), sid))


def check_edit(path: str) -> str:
    """The red squiggly: what is wrong with this file, right now. '' = fine.

    A human editing code gets told about a bad reference while their hand is
    still on it. An agent gets raw text and finds out at compile time, in a
    later turn, after the file is committed — by which point the mistake is
    archaeology instead of a typo.

    Measured today, in this repo: `brain.py` gained `paths.goals_dir()`
    without `import paths`. `ast.parse` said "parses OK" because the syntax
    WAS fine, so the failure surfaced later as a NameError at import. Syntax
    checking is not the check that was needed.

    Two rules only, both stdlib, both high-precision:
      1. it does not parse
      2. a name is read at module level that the module never binds

    Deliberately NOT a linter. It runs after every single edit, so a false
    squiggle costs more than a missed one: an agent that learns to ignore
    this line is worse off than one that never had it. Unused imports, style,
    shadowing, anything inside a function body — all skipped, because module
    scope is where "never bound anywhere" is decidable without inference.
    """
    if not path.endswith(".py") or not os.path.isfile(path):
        return ""                     # only Python; silence about the rest

    # ruff first when it is installed: it sees inside function bodies, where
    # the stdlib pass below cannot go and where most real undefined-name bugs
    # live. Deliberately a THREE-RULE selection, not ruff's opinions --
    # E9 (syntax), F821 (undefined name), F822 (undefined name in __all__).
    # Measured on this codebase, 102 files: that selection finds 0 issues,
    # so it stays silent on working code exactly like the fallback does.
    # Zero dependencies is not worth a worse tool; it is just the floor for
    # anyone who has not installed one.
    ruff = shutil.which("ruff")
    if ruff:
        try:
            r = subprocess.run(
                [ruff, "check", "--select", "E9,F821,F822", "--no-cache",
                 "--quiet", "--output-format", "concise", path],
                capture_output=True, text=True, timeout=5)
            line = (r.stdout or "").strip().splitlines()
            if line:
                first = line[0]
                # ".../brain.py:4:8: F821 Undefined name `paths`"
                return os.path.basename(first) if first.startswith("/") else first
            if r.returncode in (0, 1):
                return ""              # ruff ran and found nothing
        except (OSError, subprocess.SubprocessError):
            pass                       # fall through to the stdlib pass
    try:
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
    except (OSError, UnicodeDecodeError):
        return ""
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError as e:
        return "%s:%s %s" % (os.path.basename(path), e.lineno or "?",
                             (e.msg or "invalid syntax"))
    except (ValueError, RecursionError):
        return ""

    bound = set(dir(builtins)) | {
        "__file__", "__name__", "__doc__", "__package__", "__spec__",
        "__loader__", "__builtins__", "__path__", "__all__", "__debug__",
    }
    for node in ast.walk(tree):                 # anything bound ANYWHERE counts
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)

    # Only module-level reads: inside a function a name may legitimately be
    # defined later, or come from a scope this cheap pass cannot see.
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for node in ast.walk(stmt):
            if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                    and node.id not in bound):
                return "%s:%s '%s' is used but never imported or defined" % (
                    os.path.basename(path), node.lineno, node.id)
    return ""


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
    try:
        # the hook exports its own $PPID = the claude process of THIS session;
        # Pulse uses it for the guarded stop button
        pid = int(os.environ.get("MEDITATE_CLAUDE_PID") or 0)
        if pid > 1:
            me["pid"] = pid
    except ValueError:
        pass
    me.setdefault("files", {})[path] = now
    if len(me["files"]) > FILE_CAP:                       # bound the record
        for k in sorted(me["files"], key=me["files"].get)[:len(me["files"]) - FILE_CAP]:
            del me["files"][k]
    me.setdefault("served", [])
    me.setdefault("warned", [])

    lines: List[str] = []

    # 0. context ceiling — the one measured session-behavior defect (24% of
    # sessions compact instead of splitting). Piggybacks on the presence
    # record `me` for its debounce; saved with everything else below.
    cl = ceiling_check(payload, me)
    if cl:
        lines.append(cl)

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

    # 2. graded facts about this file, once per session per file.
    # The memory id rides in the event log — reinforcement (which knowledge
    # actually gets USED) is computable only if serves are attributable.
    for key, stmt, mem_id in facts_for(path, me["served"], store_dir):
        lines.append("GRADED FACT (machine-checked) about this file: %s" % stmt)
        me["served"].append(key)
        _log_event(coord_dir, "fact_served", sid, path, mem_id=mem_id)

    # 3. mail from other agents — delivered once, at the agent's next action
    try:
        from inbox import fetch as _mail, render as _render_mail
        mail = _render_mail(_mail(sid, cwd))
        if mail:
            lines.append(mail)
    except Exception:
        pass

    # 4. guard rules
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


def _utc_epoch(ts: str) -> float:
    import calendar
    try:
        return calendar.timegm(time.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 0.0


def done_digest(store_dir: str = STORE_DIR, coord_dir: str = COORD_DIR,
                window_s: int = 24 * 3600) -> str:
    """One line of WHAT THE SILENT MACHINERY DID in the last day.

    The hooks mostly nudge about what is owed; the owner asked for the other
    half — the tool telling the user what it has done and recorded. Reads only
    durable logs; empty day = empty string, never invented activity.
    """
    cutoff = time.time() - window_s
    beats = formed = 0
    jp = os.path.join(store_dir, "journal.jsonl")
    if os.path.exists(jp):
        try:
            with open(jp, errors="replace") as f:
                for line in f:
                    if '"sleep.completed"' not in line and '"formation.commit_facts"' not in line:
                        continue
                    try:
                        e = json.loads(line)
                    except Exception:
                        continue
                    if _utc_epoch(e.get("ts", "")) < cutoff:
                        continue
                    if e.get("event") == "sleep.completed":
                        beats += 1
                    elif e.get("event") == "formation.commit_facts":
                        formed += int(e.get("formed") or 0)
        except OSError:
            pass
    archived = 0
    # archive lives beside the store (meditation dir) — NEVER a hardcoded live
    # path, or isolated tests read the real machine's history (they did).
    ai = os.path.join(os.path.dirname(store_dir.rstrip("/")),
                      "archive", "ARCHIVE-INDEX.jsonl")
    if os.path.exists(ai):
        try:
            with open(ai, errors="replace") as f:
                for line in f:
                    try:
                        if _utc_epoch(json.loads(line).get("archived_at", "")) >= cutoff:
                            archived += 1
                    except Exception:
                        continue
        except OSError:
            pass
    served = warned = clicks = 0
    ev = os.path.join(os.path.dirname(coord_dir.rstrip("/")), "events.jsonl")
    if os.path.exists(ev):
        try:
            with open(ev, errors="replace") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if _utc_epoch(r.get("ts", "")) < cutoff:
                        continue
                    t = r.get("type")
                    # count each KIND — lumping clicks in as "served" reported
                    # 2,114 fact/warn events when only 8 were real
                    if t == "fact_served":
                        served += 1
                    elif t == "collision_warned":
                        warned += 1
                    elif t == "brain_action":
                        clicks += 1
        except OSError:
            pass
    parts = []
    if beats:
        parts.append("graded %dx" % beats)
    if formed:
        parts.append("formed %d commit-fact%s" % (formed, "s" if formed != 1 else ""))
    if archived:
        parts.append("archived %d session%s" % (archived, "s" if archived != 1 else ""))
    if served:
        parts.append("served %d fact%s" % (served, "s" if served != 1 else ""))
    if warned:
        parts.append("warned %d collision%s" % (warned, "s" if warned != 1 else ""))
    if clicks:
        parts.append("%d dashboard action%s" % (clicks, "s" if clicks != 1 else ""))
    if not parts:
        return ""
    return "Done silently (24h): " + ", ".join(parts) + "."


def session_start(payload: Dict[str, Any],
                  coord_dir: str = COORD_DIR, store_dir: str = STORE_DIR) -> str:
    """Extra SessionStart lines: live sessions + fresh drift. '' when quiet."""
    sid = str(payload.get("session_id") or "unknown")
    cwd = str(payload.get("cwd") or "")
    lines: List[str] = []

    # Register presence the moment the session opens. Presence used to be
    # created only on the first Write/Edit, so a session doing shell work
    # never existed as far as the rest of the tool was concerned — which is
    # how the timing layer came to report "no live session" with two live
    # sessions running, silencing the companion permanently.
    if sid != "unknown":
        try:
            p = load_presence(sid, coord_dir)
            p["cwd"] = cwd or p.get("cwd", "")
            # A RESUMED fat session should hear the ceiling warning at open,
            # not after its first edit — by then it may already be compacting.
            cl = ceiling_check(payload, p)
            if cl:
                lines.append(cl)
            save_presence(p, coord_dir)
        except Exception:
            pass

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

    # What the silent machinery DID — the other half of communication.
    dg = done_digest(store_dir, coord_dir)
    if dg:
        lines.append(dg)

    # Mail waiting from other agents — the first thing a new session should know.
    try:
        from inbox import fetch as _mail, render as _render_mail
        m = _render_mail(_mail(sid, cwd))
        if m:
            lines.append(m)
    except Exception:
        pass

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
                # "not checked yet" is NOT "broken". Every newly written memory
                # is `unverified` until its first review comes due, so reporting
                # status alone put every new memory a user writes into the repair
                # queue — and the repair queue dispatches agents. Only a claim
                # that actually fails, or a real drifted flag, is drift.
                if not failing and "drifted" not in flagged:
                    continue
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
    if cmd == "post-edit":
        # The squiggly. PostToolUse fires AFTER the write, which is the only
        # place a checker can look at the result — PreToolUse sees an intent,
        # not a file. additionalContext is shown in the transcript for this
        # event, so the correction lands in the same turn as the mistake
        # instead of surfacing as a traceback several turns later.
        try:
            payload = json.load(sys.stdin)
            if not isinstance(payload, dict):
                payload = {}
        except Exception:
            payload = {}
        msg = ""
        try:
            ti = payload.get("tool_input") or {}
            for k in ("file_path", "notebook_path", "path"):
                if isinstance(ti, dict) and ti.get(k):
                    problem = check_edit(str(ti[k]))
                    if problem:
                        msg = problem
                    break
        except Exception:
            msg = ""                       # never break the loop over a check
        _emit_hook_json("PostToolUse", msg)
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
