"""brain — the whole organism, live, in the browser.

    meditate brain            # serve http://127.0.0.1:7711 and open it
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

DEFAULT_PORT = 7711


def state() -> Dict[str, Any]:
    """Every organ, one dict, all from durable stores — computed per request."""
    import goals as gl
    import report as rp
    from drive import fleet_status
    from go import repair_items
    from status import gather
    from coordination import live_sessions, done_digest

    st = gather()
    rep = rp.compute()
    fleet = fleet_status()
    return {
        "generated": time.strftime("%H:%M:%S"),
        "store": st["store"],
        "heartbeat_h": st["heartbeat_h"],
        "next": st["next"],
        "goals": [{"name": g["name"], "title": g["title"], "pct": g["pct"],
                   "done": g["done"], "total": g["total"],
                   "scope_delta": g.get("scope_delta", 0), "next": g["next"]}
                  for g in st["goals"]],
        "live_sessions": [{"sid": s.get("sid", "")[:12], "cwd": s.get("cwd", ""),
                           "age_s": s.get("_age_s"),
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
    }


PAGE = """<!doctype html><meta charset="utf-8">
<title>meditate — the brain, live</title>
<body style="background:#0b0a08;color:#d8d2c4;font:14px/1.5 -apple-system,Helvetica,sans-serif;margin:0;padding:40px 52px;max-width:1000px">
<div style="letter-spacing:.35em;font-size:11px;color:#6b6557">MEDITATE · LIVE</div>
<div style="font-size:22px;margin:6px 0 2px;color:#E3B140">the brain, breathing</div>
<div id="meta" style="font-size:12px;color:#8a8578"></div>
<div id="next" style="margin:14px 0;color:#E3B140"></div>
<div id="stats" style="display:flex;flex-wrap:wrap;gap:24px;margin:18px 0"></div>
<div style="letter-spacing:.3em;font-size:11px;color:#6b6557;margin-top:22px">LIVE SESSIONS</div>
<div id="live"></div>
<div style="letter-spacing:.3em;font-size:11px;color:#6b6557;margin-top:22px">GOALS</div>
<div id="goals"></div>
<div style="letter-spacing:.3em;font-size:11px;color:#6b6557;margin-top:22px">FLEET</div>
<div id="fleet" style="font-size:13px"></div>
<div style="letter-spacing:.3em;font-size:11px;color:#6b6557;margin-top:22px">REPAIR QUEUE</div>
<div id="repair" style="font-size:13px"></div>
<div id="digest" style="margin-top:24px;font-size:12px;color:#8a8578"></div>
<script>
const G="#E3B140", DIM="#8a8578";
function esc(s){const d=document.createElement("i");d.textContent=s||"";return d.innerHTML}
function bar(p){return `<span style="display:inline-block;width:180px;height:8px;background:#1d1a14;border-radius:4px;vertical-align:middle"><span style="display:block;width:${Math.min(100,p)}%;height:8px;background:${G};border-radius:4px"></span></span>`}
async function tick(){
  let s; try{ s = await (await fetch("/api/state")).json() }catch(e){ return }
  document.getElementById("meta").textContent =
    `refreshed ${s.generated} · every number from the graded store, not recall`;
  document.getElementById("next").textContent = "next: " + s.next;
  const v = s.store.active? (100*s.store.verified/s.store.active).toFixed(1):"0";
  const stat=(val,lab)=>`<div><div style="font-size:24px;color:${G}">${val}</div><div style="font-size:12px;color:${DIM}">${lab}</div></div>`;
  document.getElementById("stats").innerHTML =
    stat(s.store.active,"graded memories")+stat(v+"%","verified")+
    stat(s.store.formed,"self-formed")+
    stat(s.wins.caught+" / "+s.wins.repaired,"drift caught / repaired")+
    stat(s.sangama.facts_served,"facts served")+
    stat(s.stilling.sessions_archived,"sessions archived")+
    stat((s.heartbeat_h==null?"—":s.heartbeat_h+" h"),"since heartbeat");
  document.getElementById("live").innerHTML = s.live_sessions.map(x=>
    `<div style="display:flex;gap:12px;font-size:13px;margin:3px 0">
      <span style="color:${G};width:110px">${esc(x.sid)}</span>
      <span style="color:${DIM};width:70px">${x.age_s}s ago</span>
      <span style="width:170px">${esc(x.last_file)}</span>
      <span style="color:${DIM}">${esc(x.cwd.replace("/Users/badenath",""))}</span></div>`
  ).join("") || `<div style="color:${DIM};font-size:13px">no live sessions</div>`;
  document.getElementById("goals").innerHTML = s.goals.map(g=>
    `<div style="margin:8px 0"><div style="display:flex;gap:12px;align-items:center">
      <span style="width:250px">${esc(g.title.slice(0,42))}</span>${bar(g.pct)}
      <span style="color:${G}">${Math.round(g.pct)}%</span>
      <span style="color:${DIM}">${g.done}/${g.total}</span>
      ${g.scope_delta>0?`<span style="color:${G}">scope +${g.scope_delta}</span>`:""}</div>
      <div style="margin-left:262px;font-size:12px;color:${DIM}">next: ${esc(g.next||"—")}</div></div>`
  ).join("");
  document.getElementById("fleet").innerHTML = s.fleet.map(f=>
    `<div style="margin:3px 0">${esc(f.goal)} — sent ${f.dispatched_min}m ago — ${
      f.milestone_ticked?`<span style="color:${G}">milestone TICKED ✓</span>`:"open"} — ${
      f.live_session?("agent "+esc(f.live_session)+" on "+esc(f.last_file||"?")+" (presumed)"):"no live session seen"}</div>`
  ).join("") || `<div style="color:${DIM}">nothing dispatched — <code style="color:${G}">meditate go</code></div>`;
  document.getElementById("repair").innerHTML = s.repair.map((m,i)=>
    `<div style="margin:4px 0"><span style="color:${G}">${i+1}.</span> ${esc(m.statement)}
     ${m.fails.map(f=>`<div style="margin-left:18px;color:${DIM};font-size:12px">FAILS ${esc(f)}</div>`).join("")}</div>`
  ).join("") || `<div style="color:${DIM}">clean — nothing failed verification</div>`;
  document.getElementById("digest").textContent = s.digest || "";
}
tick(); setInterval(tick, 4000);
</script></body>"""


class _Handler(BaseHTTPRequestHandler):
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
    srv = make_server(args.port)
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
