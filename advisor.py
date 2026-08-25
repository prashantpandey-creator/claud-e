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
    "the decision back. Every sentence is grovelling; once is a person."
)


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


def ask_local(question: str, facts: str, mem: str = "",
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
        _start_local()
        for _ in range(20):
            if _local_up(0.5):
                break
            time.sleep(0.5)
        else:
            return None
    prompt = ("%s\n\nFACTS (the only ground truth you have):\n%s\n%s\n\n"
              "The user asks: %s" % (SYSTEM, facts, mem, question))
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


def advise(question: str, timeout_s: int = TIMEOUT_S,
           model: str = MODEL, on_sentence=None) -> Dict[str, Any]:
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
    local = ask_local(q, facts, mem, on_sentence=on_sentence)
    if local:
        _say_doing("")
        return {"speech": local[:700], "source": "local", "ok": True}
    try:
        import address as _addr
        system = SYSTEM.replace("ADDRESS_TERM", _addr.term())
    except Exception:
        system = SYSTEM.replace("ADDRESS_TERM", "sir")
    prompt = ("%s\n\nFACTS (the only ground truth you have):\n%s\n%s\n\n"
              "The user asks: %s" % (system, facts, mem, q))
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

        res = advise(q, on_sentence=emit)
        # The fallback lanes (the harness, then facts-only) do not stream —
        # they hand back one finished string. Emitting it here keeps ONE
        # output contract for the caller: whatever answered, the answer
        # arrives as flushed lines. Without this a reader that speaks lines
        # would go silent for exactly the answers that took longest.
        if not said and res.get("speech"):
            emit(res["speech"])
        return 0

    res = advise(q)
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
