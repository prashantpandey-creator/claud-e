"""voice — Casper: the form that decides WHAT to say and WHETHER now is the moment.

Everything the ghost would tell you already exists in the data — projects,
drift, goals, fleet, the done-digest. Casper adds exactly two honest
judgements on top:

  briefing()          the ONE highest-leverage thing worth saying right now,
                      in plain words, with the next action named. Not a list —
                      a ghost that reads you ten items is noise, not presence.

  interruptibility()  is now a good moment? Measured, NOT mind-read. The only
                      thing observable is your ACTIVITY: editing in the last
                      minute = flow (never interrupt); live but idle a few
                      minutes = a natural pause (the moment); no live session =
                      away (talk to no one). It is a proxy for receptiveness,
                      and it says so — Casper never claims to feel your mood.

Speaking is a thin delivery step, not a speech engine: on macOS `--speak`
hands the headline to the built-in `say`. Any real TTS lane plugs in there.

    meditate voice            # the brief + whether now is the moment
    meditate voice --speak    # and say it aloud IF the moment is right
    meditate voice --json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

MEDITATION_DIR = os.path.expanduser("~/.claude/meditation")
STORE_DIR = os.environ.get("MEDITATE_STORE_DIR") or os.path.join(
    MEDITATION_DIR, "nidra_store")
COORD_DIR = os.environ.get("MEDITATE_COORD_DIR") or os.path.expanduser(
    "~/.claude/coordination/sessions")

FLOW_S = 25          # hands moving within this = in flow, do NOT interrupt
PAUSE_MAX_S = 180    # hands still up to 3 min = a gap worth speaking into
AWAY_S = 900         # no key or mouse for 15 min = not at the desk
LIVE_S = 3600        # presence younger than this = the session still counts


def interruptibility(coord_dir: str = COORD_DIR,
                     sig: Optional[Dict[str, Any]] = None,
                     meditation_dir: str = MEDITATION_DIR) -> Dict[str, Any]:
    """flow | pause | settled | meeting | away — and WHY.

    This used to be inferred from how long ago a session touched a file, which
    is a guess about a human made from the timestamps of a program. It told the
    companion "no live session — no one to speak to" while two sessions were
    running and someone was typing at the keyboard, and that one wrong guess
    kept him silent all evening.

    Now it asks the machine about the PERSON: seconds since the last key or
    mouse event, and what app is in front. The file proxy is still here, but
    only for when those signals cannot be read.
    """
    if sig is None:
        try:
            import attention
            sig = attention.signals()
        except Exception:
            sig = {}
    basis = "measured from keyboard and mouse idle, and the app in front"
    idle = sig.get("idle_s")

    # Someone else already has the conversation. Nothing is worth cutting in.
    if sig.get("in_meeting"):
        return {"state": "meeting", "interrupt_ok": False,
                "since_s": int(idle) if idle is not None else None,
                "basis": basis,
                "why": "%s is in front — you're talking to someone"
                       % sig.get("frontmost", "a meeting app")}

    if idle is not None:
        if idle > AWAY_S:
            return {"state": "away", "interrupt_ok": False, "since_s": int(idle),
                    "basis": basis,
                    "why": "no key or mouse for %d min" % (idle // 60)}
        # How long he has been sitting on something. Patience shrinks the gap
        # he is willing to take: "never break flow" is right for the first ten
        # minutes and wrong forever, because someone working at a keyboard is
        # never 25 seconds idle. Held long enough, a breath between sentences
        # is a fair moment.
        held = _held_for(meditation_dir)
        gate = FLOW_S
        if held > 1800:                     # half an hour unheard
            gate = 5
        elif held > 900:
            gate = 10
        if idle < gate:
            where = sig.get("frontmost") or "something"
            return {"state": "flow", "interrupt_ok": False, "since_s": int(idle),
                    "basis": basis,
                    "why": "typing in %s right now — never break that" % where}
        if idle < PAUSE_MAX_S:
            return {"state": "pause", "interrupt_ok": True, "since_s": int(idle),
                    "basis": basis,
                    "why": "hands still for %d s — a natural gap" % int(idle)}
        return {"state": "settled", "interrupt_ok": True, "since_s": int(idle),
                "basis": basis,
                "why": "away from the keys %d min but still here" % (idle // 60)}

    # ---- fallback: no HID signal, so guess from session file activity -------
    newest = None
    now = time.time()
    if os.path.isdir(coord_dir):
        for fn in os.listdir(coord_dir):
            if not fn.endswith(".json"):
                continue
            try:
                age = now - os.path.getmtime(os.path.join(coord_dir, fn))
            except OSError:
                continue
            if age > LIVE_S:
                continue
            if newest is None or age < newest:
                newest = age
    guess = "guessed from session file activity — the keyboard could not be read"
    if newest is None:
        return {"state": "away", "interrupt_ok": False,
                "since_s": None, "basis": guess,
                "why": "no live session — no one to speak to"}
    if newest < FLOW_S:
        return {"state": "flow", "interrupt_ok": False, "since_s": int(newest),
                "basis": guess, "why": "editing seconds ago — never break flow"}
    if newest < PAUSE_MAX_S:
        return {"state": "pause", "interrupt_ok": True, "since_s": int(newest),
                "basis": guess,
                "why": "live but idle %d min — a natural pause" % (newest // 60)}
    return {"state": "settled", "interrupt_ok": True, "since_s": int(newest),
            "basis": guess, "why": "long idle — safe to surface something"}


def _as_idea(statement: str) -> str:
    """A memory is a sentence you once told me. Say it back as an IDEA.

    Statements are written for a file: emoji markers, ALL-CAPS flags, paths,
    semicolon clauses. A person hears the first clear thought, not the record.
    This strips the record and keeps the thought.
    """
    import re as _re
    s = (statement or "").strip()
    for junk in ("⚠️", "✅", "🔥", "⏳", "📐", "🧪", "⭐", "❌"):
        s = s.replace(junk, "")
    s = s.replace("\\", "").replace('"', "")
    # a spoken sentence has no file paths, no URLs, no bracketed asides
    s = _re.sub(r"\(([^)]{0,60})\)", "", s)               # short parentheticals
    s = _re.sub(r"https?://\S+", "", s)
    s = _re.sub(r"[~/][\w./-]{6,}", "", s)                # paths
    # SHOUTED words are file-notation, not emphasis in speech
    s = " ".join(w.lower() if (w.isupper() and len(w) > 2 and w.isalpha())
                 else w for w in s.split())
    # an unclosed bracket (left behind after path/URL removal) never speaks
    if s.count("(") > s.count(")"):
        s = s[:s.rfind("(")]
    if s.count("[") > s.count("]"):
        s = s[:s.rfind("[")]
    s = _re.sub(r"\s{2,}", " ", s).strip(" -—:,;")
    # keep the first COMPLETE thought
    for stop in (" — ", "; ", ". ", ", and ", " but "):
        i = s.find(stop)
        if 25 < i < 105:
            s = s[:i]
            break
    s = s.strip(" ,;:-—([")
    # never end mid-fragment
    while s and s[-1] in "([{/,-":
        s = s[:-1].rstrip()
    return s[:105].rstrip(" .,;:-—")


def _speakable(statement: str) -> int:
    """How well would this read aloud? Higher = better. Used to CHOOSE which
    broken idea to voice — a ghost that reads a path aloud isn't a companion."""
    idea = _as_idea(statement)
    if len(idea) < 25:
        return 0
    score = 40
    score -= sum(6 for ch in "/_@#" if ch in idea)       # residue of notation
    score -= 8 * sum(1 for w in idea.split() if w.isupper() and len(w) > 3)
    score -= 10 if any(c.isdigit() for c in idea[:12]) else 0
    score += 12 if idea[0].isupper() else 0
    score += 8 if 30 <= len(idea) <= 85 else 0
    return score


def _idea_of_broken(store_dir: str) -> Optional[Dict[str, str]]:
    """The single most speakable broken idea, with what broke under it."""
    try:
        from go import repair_items
        items = repair_items(store_dir=store_dir)
    except Exception:
        items = []
    items = sorted(items, key=lambda m: -_speakable(m.get("statement", "")))
    for m in items:
        idea = _as_idea(m.get("statement", ""))
        if len(idea) < 25 or _speakable(m.get("statement", "")) <= 0:
            continue
        broke = ""
        for f in (m.get("failing") or []):
            claim = str(f.get("claim", ""))
            if claim.startswith("path:"):
                broke = os.path.basename(claim[5:].rstrip("/")) or claim[5:]
                break
        return {"idea": idea, "broke": broke, "n": str(len(items))}
    return None


def _pick(options, seed_text: str) -> str:
    """Deterministic phrasing: the same thing always gets the same words, but
    different things get different words.

    Random variation was the obvious move and the wrong one — he would rephrase
    an identical fact on every poll, which reads as instability rather than
    personality. Keyed to content, he sounds like someone with a range who
    means the same thing each time he says it.
    """
    import hashlib
    h = int(hashlib.sha256(seed_text.encode("utf-8", "replace")).hexdigest()[:8], 16)
    return options[h % len(options)]


def _held_for(meditation_dir: str = MEDITATION_DIR) -> float:
    """Seconds since he last actually said anything out loud."""
    try:
        return time.time() - os.path.getmtime(
            os.path.join(meditation_dir, "last-spoke"))
    except OSError:
        return 1e9          # never spoken: he has been holding it forever


def mark_spoke(meditation_dir: str = MEDITATION_DIR) -> None:
    """Stamp the clock when he actually SPEAKS.

    This used to be stamped inside _greeting(), which every poll calls — so the
    dashboard refreshing in the background reset the clock and he could never
    greet anyone. Computing a line is not saying it.
    """
    try:
        with open(os.path.join(meditation_dir, "last-spoke"), "w") as f:
            f.write(str(int(time.time())))
    except OSError:
        pass


def _greeting(meditation_dir: str = MEDITATION_DIR) -> str:
    """A short hello, but only when you have actually been away. Greeting
    someone who never left is the most generic thing a companion can do."""
    now = time.time()
    gap = None
    try:
        gap = now - os.path.getmtime(os.path.join(meditation_dir, "last-spoke"))
    except OSError:
        pass
    if gap is not None and gap < 5400:      # spoke within 90 min — no hello
        return ""
    hour = time.localtime(now).tm_hour
    if hour < 5:   return "Still up? "
    if hour < 12:  return "Morning. "
    if hour < 17:  return ""
    if hour < 22:  return "Evening. "
    return "Late one. "


def briefing(meditation_dir: str = MEDITATION_DIR, store_dir: str = STORE_DIR,
             goals_dir: Optional[str] = None,
             history_path: Optional[str] = None) -> Dict[str, Any]:
    """The single most important thing to say — highest leverage first.

    Priority mirrors the tool's own law: broken knowledge before new work,
    then the fleet, then the most-attended project's next task, then quiet.
    """
    import status as st

    d = st.gather(meditation_dir=meditation_dir, store_dir=store_dir,
                  goals_dir=goals_dir, history_path=history_path)

    # 1. knowledge broke — say WHICH IDEA, in the owner's own words.
    # "23 facts failed" is a metric; "the thing you told me about CarryMate
    # points at a folder that's gone" is an idea. Only the idea is speakable.
    if d.get("repair_open"):
        it = _idea_of_broken(store_dir)
        if it:
            tail = (" — the %s it points to is gone" % it["broke"]) if it["broke"] \
                   else " — what it points to isn't there anymore"
            more = ""
            try:
                n = int(it["n"])
                if n > 1:
                    more = " There are %d like it." % n
            except ValueError:
                pass
            shapes = [
                "Remember telling me %s? That's not true anymore \u2014 %s.%s",
                "Something's slipped \u2014 you told me %s, but %s.%s",
                "%s, you said. Not now, though \u2014 %s.%s",
            ]
            because = tail.lstrip().lstrip("\u2014").strip()
            body = _pick(shapes, it["idea"]) % (it["idea"], because, more)
            offer = _pick([" Want me to work through them?",
                           " Shall I go clean those up?",
                           " I can sort those out if you want."], it["idea"])
            return {"headline": _greeting(meditation_dir) + body + offer,
                    "action": "meditate fix", "kind": "repair",
                    "next": d.get("next", "")}
        return {"headline": _greeting(meditation_dir) +
                            "Something I know about your work stopped being "
                            "true. Want me to find out what?",
                "action": "meditate fix", "kind": "repair",
                "next": d.get("next", "")}

    # 2. dispatchable work waiting
    if d.get("dispatchable"):
        n = len(d["dispatchable"])
        g = d["dispatchable"][0]
        nxt = (g.get("next") or "").strip().rstrip(".")
        rest = (" Two others are waiting too." if n == 3 else
                (" %d others are waiting too." % (n - 1)) if n > 1 else "")
        title = g.get("title", g.get("name", ""))
        step = nxt or "the open milestone"
        shapes = [
            "%s is close — %s is what's left. Want me to start it?%s",
            "The next move on %s is %s. Say the word and I'll get someone on it.%s",
            "%s needs %s next. I can kick that off now.%s",
        ]
        return {"headline": _greeting(meditation_dir) +
                            _pick(shapes, title + step) % (title, step, rest),
                "action": "meditate go", "kind": "goals", "next": nxt}

    # 3. the portfolio, DISTILLED — imbalance and staleness are the insight,
    # not a per-goal percentage read-out.
    try:
        import projects as pj
        from distill_speech import distill_portfolio
        rows = pj.rollup(**{k: v for k, v in
                            dict(store_dir=store_dir, goals_dir=goals_dir,
                                 history_path=history_path).items() if v})
        line = distill_portfolio(rows)
        if line and "no imbalance" not in line.lower():
            return {"headline": line, "action": "meditate projects",
                    "kind": "task", "next": ""}
    except Exception:
        pass
    goals = d.get("goals", [])
    live = [g for g in goals if g.get("next") and g["done"] < g["total"]]
    if live:
        g = live[0]
        from distill_speech import distill_project
        return {"headline": distill_project(
                    {"project": g["title"], "pct": g["pct"],
                     "open_tasks": [{"task": g["next"]}],
                     "scope_delta": g.get("scope_delta", 0),
                     "last_touched_days": None, "repair_items": 0}),
                "action": "meditate projects", "kind": "task",
                "next": g["next"]}

    # 4. stilling overdue
    days = d.get("still_days")
    if days is None or days > 3:
        how_long = ("%.0f days" % days) if days else "a while"
        return {"headline": _greeting(meditation_dir) +
                            "We haven't cleared the decks in %s — there's a "
                            "pile of sessions waiting to settle. Worth doing "
                            "before the next big push." % how_long,
                "action": "/meditate", "kind": "still", "next": ""}

    return {"headline": _greeting(meditation_dir) +
                        "Nothing needs you. What I know holds up, the work's "
                        "moving, and I've got nothing to flag.",
            "action": "", "kind": "clear", "next": ""}


def agenda(meditation_dir: str = MEDITATION_DIR, store_dir: str = STORE_DIR,
           goals_dir: Optional[str] = None,
           history_path: Optional[str] = None) -> List[Dict[str, str]]:
    """The two-to-four things worth someone's attention, as spoken sentences.

    The briefing picks the single highest-leverage thing to interrupt with.
    This is the other half: when a person ASKS what's on the list, they want
    the list — short, ordered, and in words they would use themselves.
    """
    import status as st
    d = st.gather(meditation_dir=meditation_dir, store_dir=store_dir,
                  goals_dir=goals_dir, history_path=history_path)
    items: List[Dict[str, str]] = []

    if d.get("repair_open"):
        it = _idea_of_broken(store_dir)
        what = _as_idea(it["idea"]) if it else ""
        n = ""
        try:
            if it and int(it["n"]) > 1:
                n = " There are %d of them." % int(it["n"])
        except (ValueError, TypeError, KeyError):
            pass
        items.append({
            "say": ("Some of what you told me has stopped being true"
                    + ((" — starting with: " + what) if len(what) > 20 else "")
                    + "." + n),
            "action": "meditate fix"})

    for g in (d.get("dispatchable") or [])[:2]:
        # Goal titles are written for a file: a short name, an em-dash, then
        # the whole description. Said aloud in full they bury the actual point.
        title = g.get("title", g.get("name", "a goal")).split("\u2014")[0].strip()
        nxt = _as_idea((g.get("next") or "").strip()) or "the open milestone"
        items.append({
            "say": "On %s, the next step is %s." % (title, nxt),
            "action": "meditate go"})

    days = d.get("still_days")
    if days is None or days > 3:
        items.append({
            "say": "We haven't cleared the decks in a while — the sessions "
                   "are piling up.",
            "action": "/meditate"})

    if not items:
        items.append({"say": "Nothing's broken and nothing's waiting on you.",
                      "action": ""})
    return items[:4]


def _speak(text: str) -> bool:
    try:
        subprocess.Popen(["say", text[:400]], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        mark_spoke()
        return True
    except Exception:
        return False


LAST_SAID = os.path.join(MEDITATION_DIR, ".casper-last.txt")


def _already_said(headline: str) -> bool:
    try:
        return open(LAST_SAID).read().strip() == headline.strip()
    except OSError:
        return False


def _remember_said(headline: str) -> None:
    try:
        os.makedirs(MEDITATION_DIR, exist_ok=True)
        with open(LAST_SAID, "w") as f:
            f.write(headline.strip())
    except OSError:
        pass


def _notify(headline: str, action: str) -> bool:
    """A native, dismissable macOS notification — the gentle delivery. Visual,
    not a voice barging in. Gated by the caller on interruptibility."""
    title = "\U0001F47B Casper"
    sub = action or "meditate"
    body = headline.replace('"', "'")[:200]
    script = ('display notification "%s" with title "%s" subtitle "%s"'
              % (body, title, sub))
    try:
        r = subprocess.run(["osascript", "-e", script], timeout=8,
                           capture_output=True)
        return r.returncode == 0
    except Exception:
        return False


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Casper — what to say, and when")
    ap.add_argument("--speak", action="store_true",
                    help="say the headline aloud IF the moment is right")
    ap.add_argument("--notify", action="store_true",
                    help="post a native notification IF the moment is right")
    ap.add_argument("--force", action="store_true",
                    help="speak even if you're in flow (override the gate)")
    ap.add_argument("--quiet", action="store_true",
                    help="heartbeat mode: only deliver, print nothing")
    ap.add_argument("--agenda", action="store_true",
                    help="the short list of things worth attention, one a line")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.agenda:
        rows = agenda()
        if args.json:
            print(json.dumps({"tool_name": "meditate_agenda", "success": True,
                              "data": {"items": rows}, "metadata": {},
                              "errors": []}, indent=2))
        else:
            for r in rows:
                print(r["say"] + "\t" + r["action"])
        return 0

    b = briefing()
    t = interruptibility()
    data = {"briefing": b, "timing": t}

    moment_ok = t["interrupt_ok"] or args.force
    # on the heartbeat path (--quiet), suppress a headline already delivered —
    # don't re-nag the same thing every pause. --force always re-says.
    fresh = args.force or not _already_said(b["headline"])
    spoke = notified = False
    if b["headline"] and moment_ok and b["kind"] != "clear" and fresh:
        if args.speak:
            spoke = _speak(b["headline"])
        if args.notify:
            notified = _notify(b["headline"], b["action"])
        if spoke or notified:
            _remember_said(b["headline"])
    data["spoke"] = spoke
    data["notified"] = notified

    if args.json:
        print(json.dumps({"tool_name": "meditate_voice", "success": True,
                          "data": data, "metadata": {}, "errors": []}, indent=2))
        return 0

    if args.quiet:
        return 0
    print("👻 Casper")
    print("  \"%s\"" % b["headline"])
    if b["action"]:
        print("   → %s" % b["action"])
    print("  [%s] %s" % (t["state"], t["why"]))
    if args.speak and not spoke and not t["interrupt_ok"]:
        print("  (held my tongue — you're in flow. `--force` to override.)")
    elif spoke:
        print("  (spoke it aloud)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
