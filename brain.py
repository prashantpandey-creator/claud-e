"""pulse — the whole organism, live, in the browser.

    meditate pulse            # serve http://127.0.0.1:7711 and open it
    meditate brain --port N

One page, auto-refreshing every 4 s: live sessions (who is working, on what,
right now), the goal fleet, the repair queue, memory census, wins, and what
ran silently. Everything the hooks know, made visible.

Stdlib only. Binds 127.0.0.1 ONLY and refuses anything else — this page IS
the owner's memory and sessions; a brain never faces a network by default.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
import time
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

DEFAULT_PORT = 7711

ACTIONS = {
    "go":    lambda arg: ["python3", os.path.join(SKILL_DIR, "go.py")] + ([arg] if arg else []),
    "fix":   lambda arg: ["python3", os.path.join(SKILL_DIR, "go.py"), "--repair-only"] + ([arg] if arg else []),
    "grade": lambda arg: ["python3", os.path.join(SKILL_DIR, "nidra_bridge.py"), "--sleep"],
}


def _default_runner(action: str, arg: str) -> Dict[str, Any]:
    """Run the same code the CLI runs and RETURN ITS REAL OUTPUT — a click
    that hides what it did is the opposite of intuitive. go/fix finish in a
    couple seconds (they open Terminal agents and report); grade is slow, so
    it detaches and says so. Never push/deploy: those gates stay with the
    owner in the terminal."""
    cmd = ACTIONS[action](arg)
    if action == "grade":
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return {"started": True,
                "output": "grading in background — numbers refresh as it lands"}
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
        out = (r.stdout or r.stderr or "").strip() or "(no output)"
    except subprocess.TimeoutExpired:
        out = "still running after 25s — check `meditate fleet`"
    return {"started": True, "output": out[:600]}


ACT_RUNNER = _default_runner   # tests monkeypatch this

NAMES_PATH = os.path.expanduser("~/.claude/coordination/session-names.json")
LABELS_CACHE = os.path.expanduser("~/.claude/coordination/session-labels.json")
PROJECTS_DIR = os.path.expanduser("~/.claude/projects")

import re as _re
_CHAPTER_RE = _re.compile(r'mark_chapter.{0,400}?\\?"title\\?":\s*\\?"([^"\\]{4,70})')
_ASK_RE = _re.compile(r'"type":\s*"user".{0,2000}?"(?:text|content)":\s*"((?:[^"\\]|\\.){8,160})')


def _derive_label(full_sid: str, cwd: str) -> str:
    """WHAT is this session doing — from its own transcript, precisely.

    Priority: the session's LAST chapter mark (it names its own phase), else
    its last user ask (the owner's words), else the project dir. Reads only
    the transcript tail (300 KB) and recomputes at most once per 60 s per
    session — active transcripts change every few seconds and a full re-read
    per tick would burn the 4 s budget.
    """
    try:
        with open(LABELS_CACHE) as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    ent = cache.get(full_sid)
    now = time.time()
    if ent and now - ent.get("ts", 0) < 60:
        return ent.get("label", "")
    label = ""
    try:
        import glob as _glob
        cands = _glob.glob(os.path.join(PROJECTS_DIR, "*", full_sid + ".jsonl"))
        if cands:
            tp = max(cands, key=os.path.getmtime)
            size = os.path.getsize(tp)
            with open(tp, "rb") as f:
                if size > 300_000:
                    f.seek(size - 300_000)
                tail = f.read().decode("utf-8", errors="replace")
            chapters = _CHAPTER_RE.findall(tail)
            if chapters:
                label = chapters[-1]
            else:
                # transcripts wrap TOOL RESULTS inside "type":"user" rows —
                # trusting the type alone labeled sessions with "Exit code
                # 143..." garbage, live. A human ask is a user row WITHOUT
                # tool_result, and it must read like words.
                for line in tail.splitlines():
                    if '"type":"user"' not in line or "tool_result" in line                             or "toolUseResult" in line:
                        continue
                    m = _ASK_RE.search(line)
                    if not m:
                        continue
                    a = m.group(1).encode().decode("unicode_escape", errors="replace")
                    a = _re.sub(r"\s+", " ", a).strip()
                    if a.startswith(("{", "<", "Exit code", "[")) or " " not in a:
                        continue
                    if sum(c.isalpha() for c in a) < len(a) * 0.5:
                        continue                     # numbers/log spew
                    label = a[:64]                   # last good one wins
    except Exception:
        label = ""
    cache[full_sid] = {"label": label, "ts": now}
    try:
        os.makedirs(os.path.dirname(LABELS_CACHE), exist_ok=True)
        with open(LABELS_CACHE + ".tmp", "w") as f:
            json.dump(cache, f)
        os.replace(LABELS_CACHE + ".tmp", LABELS_CACHE)
    except OSError:
        pass
    return label


def _names() -> Dict[str, str]:
    try:
        with open(NAMES_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def set_name(sid: str, name: str) -> None:
    names = _names()
    names[sid[:12]] = name[:60]
    os.makedirs(os.path.dirname(NAMES_PATH), exist_ok=True)
    with open(NAMES_PATH + ".tmp", "w") as f:
        json.dump(names, f)
    os.replace(NAMES_PATH + ".tmp", NAMES_PATH)


def _pid_is_claude(pid: int) -> bool:
    """Refuse to signal anything that is not verifiably a claude process."""
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                             capture_output=True, text=True, timeout=5).stdout
        return "claude" in out.lower()
    except Exception:
        return False


def stop_session(sid: str, kill=os.kill) -> Dict[str, Any]:
    """SIGTERM the session's claude process — the button equivalent of
    closing its window. Guarded: pid must come from presence AND still be a
    claude process, or we refuse."""
    import coordination as co
    # resolve the dir at CALL time — default-arg binding ate a test once already
    for s in co.live_sessions(co.COORD_DIR):
        if s.get("sid", "").startswith(sid[:8]):
            pid = int(s.get("pid") or 0)
            if pid <= 1:
                return {"started": False, "output": "no pid recorded for that session yet (it appears after its next file edit)"}
            if not _pid_is_claude(pid):
                return {"started": False, "output": "refused: pid %d is not a claude process" % pid}
            try:
                kill(pid, 15)
                return {"started": True, "output": "sent stop (SIGTERM) to session %s (pid %d)" % (sid[:8], pid)}
            except ProcessLookupError:
                return {"started": False, "output": "already gone"}
            except PermissionError:
                return {"started": False, "output": "permission denied"}
    return {"started": False, "output": "session not found among the living"}


def _log_brain_action(action: str, arg: str) -> None:
    """Every click leaves a durable record — the page's ACTIVITY section and
    the efficacy report both read this."""
    try:
        ev = os.path.expanduser("~/.claude/coordination/events.jsonl")
        with open(ev, "a") as f:
            f.write(json.dumps({"type": "brain_action", "path": action +
                                ((" " + arg) if arg else ""), "sid": "brain",
                                "ts": time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                                                    time.gmtime())}) + "\n")
    except OSError:
        pass


def _dispatch_label(s, dispatched) -> str:
    for r in dispatched or []:
        if r.get("live_session") and s.get("sid", "").startswith(r["live_session"][:8]):
            return "goal: " + (r.get("milestone") or "")[:56]
    return ""


def state() -> Dict[str, Any]:
    """Every organ, one dict, all from durable stores — computed per request."""
    import goals as gl
    import report as rp
    from drive import fleet_status
    from go import repair_items
    from status import gather
    from coordination import live_sessions, done_digest
    from beacon import latest as beacon_latest

    st = gather()
    rep = rp.compute()
    fleet = fleet_status()
    beacons = beacon_latest()
    for r in fleet["dispatched"]:
        bd = beacons.get(r.get("goal"))
        if bd:
            r["says"] = bd.get("message", "")
            r["says_done"] = bd.get("done", False)
            r["says_ts"] = bd.get("ts", "")[11:19]
    d = {
        "generated": time.strftime("%H:%M:%S"),
        "store": st["store"],
        "heartbeat_h": st["heartbeat_h"],
        "next": st["next"],
        "goals": [{"name": g["name"], "title": g["title"], "pct": g["pct"],
                   "done": g["done"], "total": g["total"], "cwd": g.get("cwd", ""),
                   "scope_delta": g.get("scope_delta", 0), "next": g["next"]}
                  for g in st["goals"]],
        "live_sessions": [{"sid": s.get("sid", "")[:12], "cwd": s.get("cwd", ""),
                           "age_s": s.get("_age_s"),
                           "pid": s.get("pid"),
                           # precision ladder: owner's name > goal milestone >
                           # the session's own chapter/ask > project dir
                           "label": _names().get(s.get("sid", "")[:12])
                                    or _dispatch_label(s, fleet["dispatched"])
                                    or _derive_label(s.get("sid", ""), s.get("cwd", ""))
                                    or (os.path.basename(s.get("cwd", "").rstrip("/")) or "~"),
                           "last_file": os.path.basename(
                               sorted(s.get("files", {"": 0}),
                                      key=s.get("files", {"": 0}).get)[-1])}
                          for s in live_sessions()],
        "fleet": fleet["dispatched"],
        "repair": [{"id": m["id"], "statement": m["statement"][:140],
                    "fails": [f["claim"] for f in m.get("failing", [])]}
                   for m in repair_items()],
        "queues": {"repair_open": st["repair_open"],
                   "cooling": st["cooling"],
                   "dispatchable": len(st["dispatchable"])},
        "wins": rep["drift"],
        "stilling": rep["stilling"],
        "sangama": rep["sangama"],
        "digest": done_digest(),
        "activity": _recent_events(),
    }
    try:
        from insights import insights as _ins
        d["insights"] = _ins(d)
    except Exception:
        d["insights"] = {"headline": "", "projects": [], "needs_you": [], "moving": []}
    return d


def _recent_events(n: int = 10) -> List[Dict[str, str]]:
    ev = os.path.expanduser("~/.claude/coordination/events.jsonl")
    rows: List[Dict[str, str]] = []
    if os.path.exists(ev):
        try:
            with open(ev, errors="replace") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                        rows.append({"type": r.get("type", "?"),
                                     "what": os.path.basename(str(r.get("path", ""))),
                                     "ts": str(r.get("ts", ""))[11:19]})
                    except Exception:
                        continue
        except OSError:
            pass
    return rows[-n:][::-1]


PAGE = """<!doctype html><meta charset="utf-8">
<title>Pulse — your Claude, live</title>
<body style="background:#0b0a08;color:#d8d2c4;font:14px/1.5 -apple-system,Helvetica,sans-serif;margin:0;padding:40px 52px;max-width:1000px">
<div style="letter-spacing:.35em;font-size:11px;color:#6b6557">MEDITATE · PULSE</div>
<div style="font-size:22px;margin:6px 0 2px;color:#E3B140">Pulse <span style="font-size:13px;color:#8a8578">· your sessions, goals, memory and fleet — live. One click runs, and shows what ran.</span></div>
<div id="meta" style="font-size:12px;color:#8a8578"></div>
<div id="headline" style="margin:12px 0 4px;font-size:15px;color:#d8d2c4"></div>
<div id="next" style="margin:4px 0 14px;color:#E3B140"></div>
<div style="display:flex;gap:36px;flex-wrap:wrap;margin:4px 0 8px">
  <div style="min-width:280px"><div style="letter-spacing:.3em;font-size:11px;color:#c96442">NEEDS YOU</div><div id="needs" style="font-size:12.5px;margin-top:6px"></div></div>
  <div style="min-width:280px"><div style="letter-spacing:.3em;font-size:11px;color:#6b6557">MOVING BY ITSELF</div><div id="moving" style="font-size:12.5px;margin-top:6px"></div></div>
</div>
<div style="display:flex;gap:18px;flex-wrap:wrap;align-items:flex-start">
  <div style="max-width:180px"><button onclick="act('go','')" class="b" title="Opens one Terminal window per goal below, each with an agent working that goal's next milestone. Repair goes first if the queue is open.">launch fleet</button>
    <div class="cap">opens one Terminal agent per goal below — repair first if open</div></div>
  <div style="max-width:180px"><button onclick="act('fix','')" class="b" title="Opens one Terminal agent that re-checks and fixes the failed memories listed under REPAIR QUEUE.">repair all</button>
    <div class="cap">one agent to fix every failed memory in the repair queue</div></div>
  <div style="max-width:180px"><button onclick="act('grade','')" class="b" title="Re-verifies every memory against reality in the background. Nothing opens; the numbers refresh as it finishes.">grade now</button>
    <div class="cap">re-check all memories against reality (background, ~1 min)</div></div>
  <span id="toast" style="color:#8a8578;font-size:12px;align-self:center"></span>
</div>
<pre id="out" style="display:none;background:#12100c;border:1px solid #2a2620;border-radius:8px;padding:10px 14px;font-size:12px;color:#d8d2c4;white-space:pre-wrap;margin:10px 0 0"></pre>
<style>.b{cursor:pointer;border:1px solid #2a2620;background:transparent;color:#E3B140;border-radius:7px;padding:6px 13px;font-size:13px}.b:hover{background:#1d1a14}
.cap{font-size:10.5px;color:#6b6557;margin-top:4px;line-height:1.35}</style>
<div id="stats" style="display:flex;flex-wrap:wrap;gap:24px;margin:18px 0"></div>
<div style="font-size:11.5px;color:#6b6557;max-width:760px;margin:-4px 0 4px">what "a memory" means here: one fact about your work, saved with a receipt — the exact file and line it came from. Facts are re-checked against reality; a fact that stops matching goes to the repair queue instead of being trusted. Hover any number for its meaning.</div>
<div style="letter-spacing:.3em;font-size:11px;color:#6b6557;margin-top:26px">LIVE SESSIONS <span style="letter-spacing:0;color:#4a463c">— each orb beats with its session: fast = working right now, dim ember = gone quiet (prāṇa, the breath)</span></div>
<div id="live" style="display:flex;flex-wrap:wrap;gap:26px;margin-top:14px"></div>
<style>
@keyframes prana {
  0%,100% { transform:scale(1);    box-shadow:0 0 6px 1px rgba(227,177,64,.25); }
  50%     { transform:scale(1.18); box-shadow:0 0 22px 6px rgba(227,177,64,.55); }
}
.orb { width:44px;height:44px;border-radius:50%;
       background:radial-gradient(circle at 35% 35%, #f5d68a, #E3B140 55%, #6b4e12);
       animation:prana 2s ease-in-out infinite; margin:0 auto 8px; }
</style>
<div style="letter-spacing:.3em;font-size:11px;color:#6b6557;margin-top:22px">GOALS</div>
<div id="goals"></div>
<div style="letter-spacing:.3em;font-size:11px;color:#6b6557;margin-top:22px">FLEET</div>
<div id="fleet" style="font-size:13px"></div>
<div style="letter-spacing:.3em;font-size:11px;color:#6b6557;margin-top:22px">REPAIR QUEUE <span style="letter-spacing:0;color:#4a463c">— facts whose receipts stopped matching reality; not trusted until fixed</span></div>
<div id="repair" style="font-size:13px"></div>
<div style="letter-spacing:.3em;font-size:11px;color:#6b6557;margin-top:22px">ACTIVITY</div>
<div id="activity" style="font-size:12px;color:#8a8578"></div>
<div id="digest" style="margin-top:24px;font-size:12px;color:#8a8578"></div>
<div style="margin-top:6px;font-size:11px;color:#6b6557">agents run in Terminal windows on this Mac; they appear in LIVE SESSIONS as they work, and milestones tick only when their work verifies</div>
<script>
const G="#E3B140", DIM="#8a8578";
async function act(action, arg, value){
  const t=document.getElementById("toast"), o=document.getElementById("out");
  t.textContent = "running " + action + " " + (arg||"") + "…";
  try{
    const r=await fetch("/api/act",{method:"POST",
      headers:{"Content-Type":"application/json","X-Meditate":"1"},
      body:JSON.stringify({action,arg,value})});
    const j=await r.json();
    t.textContent = j.started ? "done: "+action+" "+(arg||"") : "refused";
    o.style.display="block"; o.textContent = j.output || "(no output)";
  }catch(e){ t.textContent="failed: "+e }
  setTimeout(tick, 1200);
}
function rename(sid, cur){
  const v = prompt("Name this session (what is it working on?)", cur||"");
  if(v!==null) act("name", sid, v);
}
function stopSess(sid, label){
  if(confirm(`Stop session "${label}"? Same as closing its window — unsaved chat context ends.`))
    act("stop", sid);
}
function esc(s){const d=document.createElement("i");d.textContent=s||"";return d.innerHTML}
function bar(p){return `<span style="display:inline-block;width:180px;height:8px;background:#1d1a14;border-radius:4px;vertical-align:middle"><span style="display:block;width:${Math.min(100,p)}%;height:8px;background:${G};border-radius:4px"></span></span>`}
async function tick(){
  let s; try{ s = await (await fetch("/api/state")).json() }catch(e){ return }
  document.getElementById("meta").textContent =
    `refreshed ${s.generated} · every number from the graded store, not recall`;
  document.getElementById("next").textContent = "next: " + s.next;
  const ins = s.insights||{headline:"",needs_you:[],moving:[]};
  document.getElementById("headline").textContent = ins.headline||"";
  document.getElementById("needs").innerHTML = (ins.needs_you||[]).map(x=>
    `<div style="margin:2px 0">• ${esc(x)}</div>`).join("") || `<div style="color:${DIM}">nothing — all clear</div>`;
  document.getElementById("moving").innerHTML = (ins.moving||[]).map(x=>
    `<div style="margin:2px 0;color:#d8d2c4">▸ ${esc(x)}</div>`).join("") || `<div style="color:${DIM}">no agents reporting</div>`;
  const v = s.store.active? (100*s.store.verified/s.store.active).toFixed(1):"0";
  const stat=(val,lab,tip)=>`<div title="${tip||""}" style="cursor:default"><div style="font-size:24px;color:${G}">${val}</div><div style="font-size:12px;color:${DIM}">${lab}</div></div>`;
  document.getElementById("stats").innerHTML =
    stat(s.store.active,"facts it knows",
      "A memory here = one fact about your work, saved with a receipt: the exact file and line it came from, so it can be re-checked forever.")+
    stat(v+"%","still true when re-checked",
      "Every fact's receipt is re-checked against the real files. This is the share that still matches reality right now.")+
    stat(s.store.formed,"learned by itself",
      "Facts the system wrote on its own from your git commits — your commit messages become memory automatically.")+
    stat(s.wins.caught+" / "+s.wins.repaired,"broken by reality / fixed",
      "When the world changes under a fact (a file moves, a claim goes stale), it is caught and stops being trusted. Fixed = someone repaired it and the re-check passed.")+
    stat(s.sangama.facts_served,"facts handed to sessions",
      "When any session edits a file this system knows facts about, those facts are handed to it at that exact moment.")+
    stat(s.stilling.sessions_archived,"old chats tidied away",
      "Empty or finished sessions moved out of your session list — reversible, nothing is ever deleted.")+
    stat((s.heartbeat_h==null?"—":s.heartbeat_h+" h"),"since last self-check",
      "Every 6 hours the system re-checks all facts, learns from new commits, and tidies up — without being asked.");
  document.getElementById("live").innerHTML = s.live_sessions.map(x=>{
    // the beat IS the recency: <60s -> ~1.1s fast pulse; slows with age;
    // >30 min -> a still ember (no animation, dim)
    const beat = Math.min(6, Math.max(1.1, x.age_s/45));
    const ember = x.age_s > 1800;
    const glow = ember ? "animation:none;opacity:.35;filter:saturate(.5)"
                       : `animation-duration:${beat.toFixed(1)}s`;
    return `<div style="width:140px;text-align:center">
      <div class="orb" style="${glow}" title="session ${esc(x.sid)} · ${esc(x.cwd)}"></div>
      <div style="font-size:12.5px;color:${G};overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(x.label)}</div>
      <div style="font-size:11px;color:${DIM};overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(x.last_file)}</div>
      <div style="font-size:10px;color:#4a463c">${x.age_s<60?x.age_s+"s":Math.round(x.age_s/60)+"m"} ago
        · <a href="#" style="color:${DIM}" title="Rename this session in your own words — the new name sticks everywhere." onclick="rename('${esc(x.sid)}','${esc(x.label)}');return false">name</a>
        · <a href="#" style="color:${DIM}" title="End this session — same as closing its window. Its unsaved chat context ends." onclick="stopSess('${esc(x.sid)}','${esc(x.label)}');return false">stop</a></div>
    </div>`}).join("") || `<div style="color:${DIM};font-size:13px">no living sessions — the field is still</div>`;
  document.getElementById("goals").innerHTML = s.goals.map(g=>
    `<div style="margin:8px 0"><div style="display:flex;gap:12px;align-items:center">
      <span style="width:250px">${esc(g.title.slice(0,42))}</span>${bar(g.pct)}
      <span style="color:${G}">${Math.round(g.pct)}%</span>
      <span style="color:${DIM}">${g.done}/${g.total}</span>
      ${g.scope_delta>0?`<span style="color:${G}">scope +${g.scope_delta}</span>`:""}
      <button class="b" style="padding:2px 9px;font-size:11px" title="Opens ONE Terminal agent working only this goal's next milestone: ${esc(g.next||'')}" onclick="act('go','${g.name}')">dispatch</button></div>
      <div style="margin-left:262px;font-size:12px;color:${DIM}">next: ${esc(g.next||"—")}</div></div>`
  ).join("");
  document.getElementById("fleet").innerHTML = s.fleet.map(f=>{
    const status = f.says
      ? `<span style="color:${f.says_done?G:'#d8d2c4'}">${f.says_done?'✓ ':'▸ '}${esc(f.says)}</span> <span style="color:#4a463c">(${esc(f.says_ts||'')})</span>`
      : (f.milestone_ticked?`<span style="color:${G}">milestone done ✓</span>`
         : (f.live_session?`working — ${esc(f.last_file||'')}`:`<span style="color:${DIM}">launched, no report yet</span>`));
    return `<div style="margin:5px 0"><span style="color:${G}">${esc(f.goal)}</span>
      <span style="color:#4a463c">· sent ${f.dispatched_min}m ago ·</span> ${status}</div>`;
  }).join("") || `<div style="color:${DIM}">nothing dispatched — press a goal's <b>dispatch</b>, or <code style="color:${G}">meditate go</code></div>`;
  document.getElementById("repair").innerHTML = s.repair.map((m,i)=>
    `<div style="margin:4px 0"><span style="color:${G}">${i+1}.</span> ${esc(m.statement)}
     <button class="b" style="padding:1px 8px;font-size:11px" title="Opens ONE Terminal agent scoped to only this memory: it checks reality, fixes the .md, and re-grades." onclick="act('fix',String(${i+1}))">fix this</button>
     ${m.fails.map(f=>`<div style="margin-left:18px;color:${DIM};font-size:12px">FAILS ${esc(f)}</div>`).join("")}</div>`
  ).join("") || `<div style="color:${DIM}">clean — nothing failed verification</div>`;
  document.getElementById("activity").innerHTML = (s.activity||[]).map(a=>
    `<div>${esc(a.ts)} · ${esc(a.type)} · ${esc(a.what)}</div>`).join("") ||
    "<div>no recorded activity yet</div>";
  document.getElementById("digest").textContent = s.digest || "";
}
tick(); setInterval(tick, 4000);
</script></body>"""


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            # CSRF guard: any web page can POST to localhost, but a custom
            # header forces a CORS preflight we never answer. No header = 403.
            if self.path != "/api/act":
                self.send_error(404)
                return
            if self.headers.get("X-Meditate") != "1":
                self.send_error(403, "missing X-Meditate header")
                return
            n = int(self.headers.get("Content-Length") or 0)
            try:
                req = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                req = {}
            action = str(req.get("action") or "")
            arg = str(req.get("arg") or "")
            if action == "name":
                set_name(arg, str(req.get("value") or ""))
                res = {"started": True, "output": "named"}
            elif action == "stop":
                res = stop_session(arg)
            elif action in ACTIONS:
                res = ACT_RUNNER(action, arg)
            else:
                self.send_error(400, "unknown action")
                return
            if not isinstance(res, dict):
                res = {"started": bool(res), "output": ""}
            _log_brain_action(action, arg)
            body = json.dumps({"started": bool(res.get("started")),
                               "action": action, "arg": arg,
                               "output": res.get("output", "")}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def do_GET(self):
        try:
            if self.path == "/api/state":
                body = json.dumps(state()).encode()
                ctype = "application/json"
            elif self.path == "/":
                body = PAGE.encode()
                ctype = "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except BrokenPipeError:
            pass
        except Exception as e:
            try:
                err = json.dumps({"error": str(e)[:200]}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(err)
            except Exception:
                pass

    def log_message(self, *a):                       # quiet server
        pass


def make_server(port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), _Handler)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Live brain server (localhost only)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args(argv)
    try:
        srv = make_server(args.port)
    except OSError:
        url = "http://127.0.0.1:%d" % args.port
        print("pulse already running at %s — opening it" % url)
        if not args.no_open:
            os.system("open '%s' 2>/dev/null" % url)
        return 0
    url = "http://127.0.0.1:%d" % srv.server_address[1]
    print("brain live at %s  (localhost only — Ctrl-C to stop)" % url)
    if not args.no_open:
        os.system("open '%s' 2>/dev/null" % url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstilled.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
