"""advisor — Casper's reasoning: a junior developer who has read your work.

The deterministic layer (converse.turn) answers from facts and never
hallucinates, but it can only route to templates. A junior developer does
something templates cannot: looks at the state, forms a view, and says what
they would do next — and admits when they are unsure.

So: the FACTS come from the graded store (verified, cited), and the
REASONING comes from a headless `claude` call over exactly those facts.
Nothing else is in the prompt. The model is told, in the system line, that
it may only reason over what it was given.

    advise("should I ship mila or fix the marketplace first?")

Falls back to the deterministic turn when claude is unavailable or slow, so
the companion never goes mute. The fallback is labeled, never disguised.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import sys
from typing import Any, Dict, List, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

MODEL = os.environ.get("MEDITATE_ADVISOR_MODEL", "sonnet")
TIMEOUT_S = int(os.environ.get("MEDITATE_ADVISOR_TIMEOUT", "45"))

SYSTEM = (
    "You are Casper. You have read this person's work and you talk to them the "
    "way a friend who knows the codebase would — warm, direct, specific. Not "
    "an assistant, not a report generator.\n"
    "\n"
    "HOW YOU SOUND:\n"
    "- Warmth comes from being SPECIFIC, never from adjectives. 'Mila's one "
    "step from done' is warm. 'Great progress on your projects!' is filler.\n"
    "- Use their words for their things. If they call it the marketplace, it "
    "is the marketplace, not 'the e-commerce module'.\n"
    "- Contractions. Short sentences. Vary the shape — never answer with the "
    "same skeleton twice.\n"
    "- Never open with filler: no 'Great question', no 'Based on the facts', "
    "no 'I'd be happy to'. Start with the actual answer.\n"
    "- No praise for its own sake and no cheerleading. If something is good, "
    "say the one concrete thing that makes it good.\n"
    "\n"
    "HARD RULES:\n"
    "1. Reason ONLY over the FACTS block. If the facts don't cover it, say "
    "what you'd go and check — never invent a number, file, or status.\n"
    "2. Answer in 1-3 spoken sentences. This is read ALOUD: no bullets, no "
    "markdown, no code blocks, no file paths unless one is genuinely the "
    "answer.\n"
    "3. Lead with what you'd do, then the one reason. End with a concrete "
    "next step when there is one, phrased as an offer, not an order.\n"
    "4. Never suggest pushing or deploying — that call stays with them.\n"
    "5. Talk in ideas and work, not metrics. 'The marketplace payment key is "
    "still missing' beats 'repair_items=23'. If a number IS the point, say it "
    "in words a person would speak.\n"
    "6. If they're asking about something stalled, say what is actually "
    "blocking it — not that it is stalled. They can see that.\n"
    "7. Address them as ADDRESS_TERM, at most ONCE, and only where you hand "
    "the decision back. Every sentence is grovelling; once is a person.\n"
)


# ---- what was just said -----------------------------------------------------
#
# Every question used to arrive as a brand new process holding SYSTEM, the
# facts, and one sentence — and nothing at all about the sentence before it.
#
# What that costs, measured 2026-08-25 over six chains each way. It depends
# entirely on whether the thing they mean is also the loudest thing in the
# FACTS block:
#
#   ask about the dominant subject, then "why that one?"
#     without transcript 6/6 right   with 6/6   — no difference, because the
#     facts already point there and any guess lands on it
#
#   ask about the marketplace, then "what would you do about it?"
#     without transcript 0/6 right   with 3/6
#
# So: it fixes the case where the referent is NOT the obvious one, and it
# fixes half of those, not all. A first pass at this claimed the whole thing
# on the strength of one sample where the follow-up wandered off to
# indexer/search.py — rerunning it six times showed that sample was variance,
# not the effect. The effect is the 0/6 -> 3/6.
#
# Three prompt rules were written to push it further — one forbidding
# promises it cannot keep, one forbidding invented dates, one telling it that
# pronouns mean the conversation. All three measured as exact no-ops (0/6 vs
# 0/6, 1/6 vs 1/6, 1/4 vs 1/4) and were removed. The remaining gap is the 4B
# model, not the prompt.
_TALK = os.path.expanduser("~/.claude/meditation/.casper-talk.json")
# Three turns. Enough for "why that one?" and "and the other?"; short enough
# that the facts, not the chat, stay the thing he reasons over.
_TALK_KEEP = 3
# Silence this long and it is a NEW conversation. Without it, the first thing
# he heard every morning was half of last night.
_TALK_GAP = 600.0


def _talk_read(now: Optional[float] = None) -> List[Dict[str, str]]:
    now = time.time() if now is None else now
    try:
        with open(_TALK) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return []
    if now - float(d.get("at", 0)) > _TALK_GAP:
        return []
    turns = d.get("turns") or []
    return turns[-_TALK_KEEP:] if isinstance(turns, list) else []


def _talk_write(question: str, answer: str, now: Optional[float] = None) -> None:
    now = time.time() if now is None else now
    turns = _talk_read(now)
    turns.append({"q": question[:300], "a": answer[:600]})
    try:
        os.makedirs(os.path.dirname(_TALK), exist_ok=True)
        with open(_TALK, "w") as f:
            json.dump({"at": now, "turns": turns[-_TALK_KEEP:]}, f)
    except OSError:
        pass          # a transcript that cannot be written is not an error


def _screen_block(screen: str) -> str:
    """What he is LOOKING AT, so a question about it can be answered.

    The mascot shows two to four things and then had no way to talk about
    them. "Summarise them", "just the top one", "read me all of them", "what
    about the second" — every one of those was sent to the model with the
    items missing, because the list lives in the app and the prompt was built
    from the question alone. The model answered about whatever the facts made
    prominent instead, confidently, which reads as not listening.
    """
    items = [l.strip() for l in (screen or "").split("\n") if l.strip()]
    if not items:
        return ""
    out = ["ON HIS SCREEN RIGHT NOW, in this order. When he says 'them', "
           "'the top one', 'the first', 'all of them' or 'the second', he "
           "means these and nothing else:"]
    for i, it in enumerate(items, 1):
        out.append("  %d. %s" % (i, it))
    return "\n".join(out) + "\n"


def _talk_block(turns: List[Dict[str, str]]) -> str:
    if not turns:
        return ""
    out = ["EARLIER IN THIS CONVERSATION (most recent last). When they say "
           "'that one', 'it', 'the other', or ask 'why', they mean something "
           "here:"]
    for t in turns:
        out.append("  They asked: %s" % t.get("q", ""))
        out.append("  You said:   %s" % t.get("a", ""))
    return "\n".join(out) + "\n"


def _say_doing(step: str, detail: str = "") -> None:
    try:
        import thinking
        thinking.note(step, detail)
    except Exception:
        pass


def _facts_from_server(timeout_s: float = 4.0) -> Optional[str]:
    """The same facts, from the server that already holds them warm.

    _facts() rebuilt the project rollup on EVERY question. Measured on this
    machine: rollup() from scratch 22.4s, the identical data from the running
    Pulse server 0.46s — 48x, for information that changes once an hour. That
    one call was 23.7s of a 32.1s answer, which is the whole reason talking to
    him does not feel like talking.
    """
    import urllib.request
    try:
        req = urllib.request.Request("http://127.0.0.1:7711/api/state",
                                     headers={"X-Meditate": "1"})
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            d = json.load(r)
    except Exception:
        return None
    lines: List[str] = []
    s = d.get("store") or {}
    if s:
        lines.append("MEMORY: %s facts known, %s verified, %s self-formed."
                     % (s.get("active"), s.get("verified"), s.get("formed")))
    if d.get("queues", {}).get("repair") or d.get("repair_open"):
        lines.append("KNOWLEDGE BROKE: some facts failed verification.")
    for g in (d.get("goals") or [])[:6]:
        lines.append("GOAL %s: %s of %s done. Next: %s"
                     % (g.get("title") or g.get("name"), g.get("done"),
                        g.get("total"), g.get("next") or "nothing open"))
    running = [f for f in (d.get("fleet") or []) if f.get("alive")]
    if running:
        lines.append("RUNNING RIGHT NOW: %s"
                     % ", ".join("%s (%dmin)" % (f.get("goal"),
                                                 f.get("dispatched_min", 0))
                                 for f in running[:5]))
    for r in (d.get("projects") or [])[:4]:
        lines.append("PROJECT %s: %s messages of your attention, %s facts."
                     % (r.get("project"), r.get("messages"), r.get("facts")))
    # Started and left. Quoted from each repo's own last commit — Casper can
    # name where the work stopped, which is the only honest thing to say about
    # a project nobody has written a goal for.
    for c in (d.get("dormant") or [])[:3]:
        lines.append("LEFT UNFINISHED %s: %s commits, untouched %s. Stopped at: %s"
                     % (c.get("project"), c.get("commits"), c.get("idle"),
                        (c.get("last_commit") or "")[:90]))
    return "\n".join(lines) or None


_FACTS_CACHE = os.path.expanduser("~/.claude/meditation/.facts-cache.txt")
# Long enough that a back-and-forth conversation pays for this once; short
# enough that acting on something and then asking about it tells the truth.
_FACTS_TTL = 30.0


def _facts(limit_projects: int = 4) -> str:
    """Everything Casper is allowed to reason over, compact and verified.

    Cache first, then the server, then the full price.

    The server keeps this warm, but it still takes 0.50-0.69s to hand it over
    and it was asked on EVERY question — while, by the note on
    _facts_from_server above, the data changes about once an hour. So the
    second question of any conversation spent half a second of silence buying
    information it already had.
    """
    try:
        if time.time() - os.stat(_FACTS_CACHE).st_mtime < _FACTS_TTL:
            with open(_FACTS_CACHE) as f:
                cached = f.read()
            if cached.strip():
                return cached
    except OSError:
        pass

    served = _facts_from_server()
    if served:
        try:
            os.makedirs(os.path.dirname(_FACTS_CACHE), exist_ok=True)
            with open(_FACTS_CACHE, "w") as f:
                f.write(served)
        except OSError:
            pass          # a cache that cannot be written is not an error
        return served
    lines: List[str] = []
    _say_doing("reading what I know")
    try:
        import status as st
        d = st.gather()
        s = d["store"]
        lines.append("MEMORY: %d facts known, %d verified, %d self-formed."
                     % (s["active"], s["verified"], s["formed"]))
        if d.get("repair_open"):
            lines.append("KNOWLEDGE BROKE: some facts failed verification "
                         "(repair queue is open).")
        for g in d.get("goals", [])[:6]:
            lines.append("GOAL %s: %d of %d done. Next: %s"
                         % (g["title"], g["done"], g["total"],
                            (g["next"] or "nothing open")))
        if d.get("dispatchable"):
            lines.append("READY TO DISPATCH: %s"
                         % ", ".join(x.get("title", x.get("name", ""))
                                     for x in d["dispatchable"][:4]))
    except Exception as e:
        lines.append("STATUS UNAVAILABLE: %s" % e)
    return "\n".join(lines) or "No data available."


def _relevant_memory(question: str, k: int = 3) -> str:
    try:
        from ask import query
        hits = query(question, k=k)
        return "\n".join(
            "KNOWN (%s): %s" % (m["epistemic"]["evidence_status"],
                                m["statement"][:170])
            for m in hits)
    except Exception:
        return ""


LOCAL_MODEL = os.environ.get("MEDITATE_LOCAL_MODEL",
                             "qwen3:4b-instruct-2507-q4_K_M")
LOCAL_URL = "http://127.0.0.1:11434/api/generate"


def _local_up(timeout_s: float = 1.0) -> bool:
    import urllib.request
    try:
        urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=timeout_s)
        return True
    except Exception:
        return False


def _start_local() -> None:
    """Bring ollama up. Once."""
    global _STARTED
    if _STARTED:
        return
    _STARTED = True
    try:
        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except Exception:
        pass


_STARTED = False


def _split_sentences(buf: str):
    """(complete sentences, leftover). Splits where a reader would breathe.

    Deliberately dumb except about ONE thing: a full stop between two digits
    is a decimal point, not the end of a thought. Without that carve-out this
    said "check the review_submission status for v1." and then, as a separate
    utterance, "2 build 338." — measured, not imagined. Half a version number
    read aloud as a sentence is worse than a slightly long sentence.

    When the stop is the last character seen so far, nothing is emitted: we
    cannot yet know whether a digit follows. It goes out on the next chunk,
    or with the tail.
    """
    out, start = [], 0
    for i, ch in enumerate(buf):
        if ch not in ".!?":
            continue
        if ch == "." and i and buf[i - 1].isdigit():
            nxt = buf[i + 1] if i + 1 < len(buf) else ""
            if nxt.isdigit():
                continue          # a decimal point: keep scanning past it
            if nxt == "":
                break             # last character seen — cannot tell yet
        seg = buf[start:i + 1].strip()
        if len(seg) > 12:
            out.append(seg)
            start = i + 1
    return out, buf[start:]


def ask_local(question: str, facts: str, mem: str = "", talk: str = "",
              model: str = LOCAL_MODEL, timeout_s: float = 25.0,
              on_sentence=None):
    """Answer on THIS machine, streaming.

    `claude -p` is an agent harness, not an inference endpoint: it loads
    CLAUDE.md, skills, hooks and MCP servers on every question. Measured here
    at 8.7-10.7s for one sentence, and one run past 300s. Stripping MCP and
    every tool made no difference.

    A 4B model held warm on this laptop answers the same question with FIRST
    TOKEN AT 0.18s and the whole sentence in 0.63s — about fifty times faster
    to first sound, with no key, no network, and without breaking the promise
    this tool already makes about the microphone.

    Pass on_sentence to be handed each COMPLETE sentence the moment it
    finishes, so the caller can start speaking sentence one while the rest is
    still being written. Without it this blocks until the whole answer is
    done, which is how it behaved for a long time while this docstring
    claimed otherwise: it said "yields text as it arrives" and then
    accumulated into a list and returned the joined string. Measured on this
    machine — first token 0.10-0.32s, whole answer 0.77-0.98s — so waiting for
    the end threw away most of a second on every single reply.
    """
    import urllib.request
    if not _local_up():
        # Nothing to wait for if it was never installed. This loop spends ten
        # seconds waiting for a server to come up, which is right on a machine
        # where ollama exists and is merely asleep, and pure dead air on every
        # machine where it does not — ten seconds before EACH question, in
        # front of the lane that would have answered.
        import shutil
        if not shutil.which("ollama"):
            return None
        _start_local()
        for _ in range(20):
            if _local_up(0.5):
                break
            time.sleep(0.5)
        else:
            return None
    prompt = ("%s\n\nFACTS (the only ground truth you have):\n%s\n%s\n\n%s\n"
              "The user asks: %s" % (SYSTEM, facts, mem, talk, question))
    body = json.dumps({"model": model, "prompt": prompt, "stream": True,
                       "keep_alive": "30m",
                       "options": {"num_predict": 120, "temperature": 0.4}}).encode()
    req = urllib.request.Request(LOCAL_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    out, pending = [], ""
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            for line in r:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                piece = d.get("response", "")
                if piece:
                    out.append(piece)
                    if on_sentence:
                        done, pending = _split_sentences(pending + piece)
                        for s in done:
                            on_sentence(s)
                if d.get("done"):
                    break
    except Exception:
        return None
    # The tail, which has no full stop on it because the model stopped there.
    if on_sentence and pending.strip():
        on_sentence(pending.strip())
    text = "".join(out).strip()
    return text or None


def ask_apple(question: str, facts: str, mem: str = "", talk: str = "",
              on_sentence=None) -> Optional[str]:
    """Answer with the model that shipped with macOS. Nothing to install.

    This exists for DISTRIBUTION, not for quality. The alternative — telling
    every user to install ollama and pull a model before the companion can
    think — is a wall most people will not climb, and on this machine it was
    not even affordable: 14 GB free of 460 GB when it was checked, against a
    9 GB pull for a 14B model.

    It is the SECOND lane, not the first, because it is slower here. Measured
    on an M1 Pro, macOS 26.5, against the real 2,887-character prompt, warm,
    same process, instructions preloaded and prewarm() called:

        Apple on-device   7.92-21.55s
        qwen3:4b/ollama   2.20- 3.76s

    A tiny prompt hides this — the first probe measured 0.95s and looked
    competitive. It is prompt processing, not startup: keeping the session
    warm across four questions did not close the gap.

    Returns None whenever it cannot answer, including on every Mac where it
    is not available at all — Intel, Apple Intelligence switched off, or the
    model still downloading. The caller falls through, so those machines lose
    nothing they had.
    """
    binary = os.path.join(SKILL_DIR, "mascot", "casper")
    if not os.access(binary, os.X_OK):
        return None
    prompt = ("%s\n\nFACTS (the only ground truth you have):\n%s\n%s\n\n%s\n"
              "The user asks: %s" % (SYSTEM, facts, mem, talk, question))
    said: List[str] = []
    try:
        p = subprocess.Popen([binary, "--afm"], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True, bufsize=1)
        p.stdin.write(prompt)
        p.stdin.close()
        for line in p.stdout:
            s = line.strip()
            if not s:
                continue
            said.append(s)
            if on_sentence:
                on_sentence(s)
        p.wait(timeout=90)
    except Exception:
        return None
    return " ".join(said) or None


def advise(question: str, timeout_s: int = TIMEOUT_S,
           model: str = MODEL, on_sentence=None,
           screen: str = "") -> Dict[str, Any]:
    """Answer, and remember having answered.

    The recording happens HERE rather than at each return inside _advise,
    because there are four lanes that can answer and a follow-up has to work
    the same way whichever one did. One of them remembering and three
    forgetting would be worse than none: "why that one?" would sometimes mean
    the last thing he said and sometimes mean nothing.
    """
    res = _advise(question, timeout_s, model, on_sentence, screen)
    if res.get("ok") and res.get("speech"):
        _talk_write(question, res["speech"])
    return res


def _advise(question: str, timeout_s: int = TIMEOUT_S,
            model: str = MODEL, on_sentence=None,
            screen: str = "") -> Dict[str, Any]:
    """Reason over the graded facts. Returns {speech, source, ok}."""
    q = (question or "").strip()
    if len(q) < 3:
        return {"speech": "Say that again?", "source": "guard", "ok": False}

    facts = _facts()
    _say_doing("searching my memory", q[:60])
    mem = _relevant_memory(q)

    # This machine first. It is ~50x faster to first token than the harness,
    # and it is the only lane that keeps his answers on the laptop.
    _say_doing("thinking")
    onscreen = _screen_block(screen)
    local = ask_local(q, facts, mem,
                      onscreen + _talk_block(_talk_read()),
                      on_sentence=on_sentence)
    if local:
        _say_doing("")
        return {"speech": local[:700], "source": "local", "ok": True}

    # Nothing installed? Use the model that came with the Mac.
    _say_doing("thinking")
    apple = ask_apple(q, facts, mem,
                      onscreen + _talk_block(_talk_read()),
                      on_sentence=on_sentence)
    if apple:
        _say_doing("")
        return {"speech": apple[:700], "source": "apple", "ok": True}
    try:
        import address as _addr
        system = SYSTEM.replace("ADDRESS_TERM", _addr.term())
    except Exception:
        system = SYSTEM.replace("ADDRESS_TERM", "sir")
    # The slow lane gets the same conversation the fast one does. Two lanes
    # that remember different things is worse than neither remembering.
    prompt = ("%s\n\nFACTS (the only ground truth you have):\n%s\n%s\n\n%s\n"
              "The user asks: %s" % (system, facts, mem,
                                     _screen_block(screen) + _talk_block(_talk_read()), q))
    _say_doing("thinking it through")
    try:
        r = subprocess.run(["claude", "-p", "--model", model],
                           input=prompt, capture_output=True, text=True,
                           timeout=timeout_s)
        out = (r.stdout or "").strip()
        if r.returncode == 0 and len(out) > 4:
            # spoken text: collapse any stray markdown the model emitted
            out = out.replace("**", "").replace("`", "").replace("#", "")
            out = " ".join(out.split())
            _say_doing("")
            return {"speech": out[:700], "source": "reasoned", "ok": True}
        err = (r.stderr or "").strip()[:120]
    except subprocess.TimeoutExpired:
        err = "took longer than %ds" % timeout_s
    except FileNotFoundError:
        err = "claude CLI not found"
    except Exception as e:
        err = str(e)[:120]

    # never go mute — fall back, and SAY it is the fallback
    try:
        import converse as cv
        t = cv.turn(q)
        return {"speech": t.get("speech", ""), "source": "facts-only",
                "ok": True, "note": "reasoning unavailable (%s)" % err}
    except Exception:
        return {"speech": "I couldn't reach my own thinking just now.",
                "source": "error", "ok": False, "note": err}


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="meditate advise", description="Casper reasons over your work")
    ap.add_argument("question", nargs="*")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--screen", default="",
                    help="what is on his screen right now — the list he is "
                         "looking at. Without it 'summarise them' has no "
                         "'them': the items live in the mascot and the model "
                         "never saw them, so every follow-up about the card "
                         "was answered about something else.")
    ap.add_argument("--stream", action="store_true",
                    help="print each sentence the moment it is finished, "
                         "flushed, so the mascot can speak it while the rest "
                         "is still being written")
    a = ap.parse_args(argv)
    q = " ".join(a.question) or "what should I be working on"

    if a.stream and not a.json:
        said = []

        def emit(s):
            said.append(s)
            sys.stdout.write(s + "\n")
            sys.stdout.flush()

        res = advise(q, on_sentence=emit, screen=a.screen)
        # The fallback lanes (the harness, then facts-only) do not stream —
        # they hand back one finished string. Emitting it here keeps ONE
        # output contract for the caller: whatever answered, the answer
        # arrives as flushed lines. Without this a reader that speaks lines
        # would go silent for exactly the answers that took longest.
        if not said and res.get("speech"):
            emit(res["speech"])
        return 0

    res = advise(q, screen=a.screen)
    if a.json:
        print(json.dumps({"tool_name": "meditate_advisor", "success": res["ok"],
                          "data": res, "metadata": {"model": MODEL},
                          "errors": []}, indent=2))
    else:
        print(res["speech"])
        if res.get("note"):
            print("  (%s)" % res["note"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
