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
import sys
from typing import Any, Dict, List, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

MODEL = os.environ.get("MEDITATE_ADVISOR_MODEL", "sonnet")
TIMEOUT_S = int(os.environ.get("MEDITATE_ADVISOR_TIMEOUT", "45"))

SYSTEM = (
    "You are Casper, the user's engineering companion. You have read their "
    "work and you speak like a sharp junior developer talking to their lead: "
    "short, concrete, opinionated, never corporate.\n"
    "HARD RULES:\n"
    "1. Reason ONLY over the FACTS block. If the facts don't cover it, say "
    "what you'd check — never invent a number, file, or status.\n"
    "2. Answer in 1-3 spoken sentences. This is read ALOUD: no bullets, no "
    "markdown, no code blocks, no file paths unless essential.\n"
    "3. Lead with your recommendation, then the one reason. End with a "
    "concrete next step when there is one.\n"
    "4. Never suggest pushing or deploying — that stays with the owner.\n"
    "5. Talk about ideas and work, not metrics. 'The marketplace payment key "
    "is still missing' beats 'repair_items=23'."
)


def _facts(limit_projects: int = 4) -> str:
    """Everything Casper is allowed to reason over, compact and verified."""
    lines: List[str] = []
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
    try:
        from projects import rollup
        for r in rollup()[:limit_projects]:
            if not (r["messages"] or r["goals"]):
                continue
            lines.append("PROJECT %s: %d messages of your attention, %d facts"
                         "%s%s."
                         % (r["project"], r["messages"], r["facts"],
                            ", %d need repair" % r["repair_items"] if r["repair_items"] else "",
                            ", last touched %.0f days ago" % r["last_touched_days"]
                            if r["last_touched_days"] is not None else ""))
    except Exception:
        pass
    try:
        from go import repair_items
        import voice as vc
        for m in repair_items()[:3]:
            idea = vc._as_idea(m.get("statement", ""))
            if len(idea) > 25:
                lines.append("BROKEN IDEA: %s" % idea)
    except Exception:
        pass
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


def advise(question: str, timeout_s: int = TIMEOUT_S,
           model: str = MODEL) -> Dict[str, Any]:
    """Reason over the graded facts. Returns {speech, source, ok}."""
    q = (question or "").strip()
    if len(q) < 3:
        return {"speech": "Say that again?", "source": "guard", "ok": False}

    facts = _facts()
    mem = _relevant_memory(q)
    prompt = ("%s\n\nFACTS (the only ground truth you have):\n%s\n%s\n\n"
              "The user asks: %s" % (SYSTEM, facts, mem, q))
    try:
        r = subprocess.run(["claude", "-p", "--model", model],
                           input=prompt, capture_output=True, text=True,
                           timeout=timeout_s)
        out = (r.stdout or "").strip()
        if r.returncode == 0 and len(out) > 4:
            # spoken text: collapse any stray markdown the model emitted
            out = out.replace("**", "").replace("`", "").replace("#", "")
            out = " ".join(out.split())
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
    ap = argparse.ArgumentParser(description="Casper reasons over your work")
    ap.add_argument("question", nargs="*")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    q = " ".join(a.question) or "what should I be working on"
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
