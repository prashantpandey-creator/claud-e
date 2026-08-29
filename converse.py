"""converse — the voice TURN: hear a sentence, answer from the graded data, act.

A voice assistant is three parts. Two already exist on this machine:

    microphone  →  turn()  →  speaker
    (SFSpeech /     THIS      (AVSpeech / say /
     Web Speech)              any TTS)

The shell (voice-cockpit's SwiftUI menubar, or a web page) owns the mic and
the voice. This module owns the only part that needs the data: understanding
what was said and answering from what we actually know — projects, goals,
graded memory, the fleet — in one spoken sentence.

Deterministic routing, no LLM: five intents cover what a person actually
asks a project assistant.

    status     "what's bothering me" / "how are we doing"   -> the briefing
    project    "how is mila"                                -> that project's %
                                                               and next task
    knowledge  "what do we know about deploys"              -> graded memory,
                                                               verified first
    command    "run the fleet" / "fix the knowledge"        -> an action, GATED
    unclear    (didn't catch it)                            -> ask again

Two safety lines, both tested:
  - Voice NEVER pushes or deploys. Those stay in the terminal, with you.
  - Actions are gated: by default the turn TELLS you the command instead of
    running it. The shell opts in explicitly (allow_actions=True) once it has
    its own confirmation UX.

    meditate say "how is mila doing"     # one turn, printed
    meditate say "..." --speak           # and spoken aloud
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Callable, Dict, List, Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

# what the voice may never do, no matter how it's phrased
FORBIDDEN = re.compile(r"\b(push|deploy|ship it|release|merge)\b", re.I)

_STATUS = re.compile(r"\b(bother|worry|wrong|status|how are we|how'?s it going|"
                     r"what'?s up|brief|catch me up|state of)\b", re.I)
_KNOW = re.compile(r"\b(what do we know|remind me|do we know|what did we|"
                   r"tell me about|remember)\b", re.I)
_COMMAND = re.compile(r"\b(run|launch|start|dispatch|fix|repair|grade|"
                      r"go ahead|do it)\b", re.I)
_PROJECT_Q = re.compile(r"\b(how(?:'s| is| are)|status of|where(?:'s| is)|"
                        r"progress on)\b", re.I)


def _projects(**kw) -> List[Dict[str, Any]]:
    """Only forward overrides that are actually SET — passing None would
    clobber the module defaults (it did: TypeError on os.path.join(None))."""
    import projects as pj
    args = {k: v for k, v in kw.items()
            if k in ("store_dir", "goals_dir", "history_path") and v}
    return pj.rollup(**args)


def _match_project(text: str, rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Longest project name that appears as a WORD in the sentence."""
    t = " " + text.lower() + " "
    for r in sorted(rows, key=lambda r: -len(r["project"])):
        name = r["project"].lower()
        if len(name) >= 3 and re.search(r"\b%s\b" % re.escape(name), t):
            return r
    return None


def turn(utterance: str, allow_actions: bool = False,
         runner: Optional[Callable[[str], Dict[str, Any]]] = None,
         meditation_dir: Optional[str] = None, store_dir: Optional[str] = None,
         goals_dir: Optional[str] = None,
         history_path: Optional[str] = None) -> Dict[str, Any]:
    """One exchange. Returns what to SAY, plus any action taken."""
    import voice as vc

    kw = {k: v for k, v in dict(meditation_dir=meditation_dir,
                                store_dir=store_dir, goals_dir=goals_dir,
                                history_path=history_path).items() if v}
    text = (utterance or "").strip()
    out: Dict[str, Any] = {"heard": text, "intent": "unclear", "action": None,
                           "executed": False, "speech": ""}

    if len(text) < 3:
        out["speech"] = "I didn't catch that — say it again?"
        return out

    # --- the hard line: voice never ships ---------------------------------
    if FORBIDDEN.search(text):
        out.update(intent="refused", speech=(
            "I won't push or deploy by voice — that one stays in the terminal "
            "with you."))
        return out

    # --- command ----------------------------------------------------------
    if _COMMAND.search(text):
        act = ("fix" if re.search(r"\b(fix|repair)\b", text, re.I)
               else "grade" if re.search(r"\bgrade\b", text, re.I)
               else "go")
        out["intent"] = "command"
        out["action"] = act
        if not allow_actions:
            out["speech"] = ("Say the word and I'll run it — or do it yourself "
                             "with: meditate %s" % act)
            return out
        try:
            res = (runner or _default_runner)(act)
            out["executed"] = bool(res.get("started"))
            first = (res.get("output") or "").strip().splitlines()
            out["speech"] = (first[0][:160] if first else "Done.")
        except Exception as e:
            out["speech"] = "That failed: %s" % str(e)[:120]
        return out

    # --- explicit status question wins before anything else ---------------
    # ("what is bothering me" once matched the catch-all bucket 'other' and
    # answered 'no goal tracked yet' — routing order was the bug.)
    # A question that NAMES a branch gets that branch, not the same brief.
    for kind, rx in _BRANCH:
        if rx.search(text):
            said = _speak_branch(kind)
            if said:
                out["intent"] = "status:" + kind
                out["speech"] = said
                return out
            break

    if _STATUS.search(text):
        out["intent"] = "status"
        # The full catch-up, same composed sentences the console's hero shows —
        # one voice everywhere. Spoken form takes the first three sentences;
        # a five-sentence monologue is a wall in the ear too.
        try:
            import brief as bf
            lines = bf.gather_and_compose() if not kw else []
        except Exception:
            lines = []
        if lines:
            out["speech"] = " ".join(lines[:3])
            return out
        b = vc.briefing(**kw)
        out["speech"] = b["headline"] + ((" " + b["action"] + ".") if b.get("action") else "")
        return out

    # --- a named project --------------------------------------------------
    rows = [r for r in _projects(**kw) if r["project"] != "other"]
    hit = _match_project(text, rows)
    if hit:
        # DISTILLED, not a field readout — the owner's point: say the thing
        # that matters (stuck / grew / rotting / on-task), not the columns.
        from distill_speech import distill_project
        out["intent"] = "project"
        out["speech"] = distill_project(hit)
        return out

    # --- knowledge --------------------------------------------------------
    if _KNOW.search(text):
        import ask as ak
        q = re.sub(r"\b(what do we know about|tell me about|remind me( about)?|"
                   r"do we know|remember)\b", "", text, flags=re.I).strip(" ?.")
        hits = ak.query(q, store_dir=store_dir or ak.STORE_DIR, k=2)
        out["intent"] = "knowledge"
        if not hits:
            out["speech"] = "Nothing verified on that yet."
        else:
            m = hits[0]
            ep = m.get("epistemic") or {}
            grade = ep.get("evidence_status")
            scope = ep.get("evidence_scope")
            lead = m["statement"].strip()[:180]
            if grade != "machine_checked":
                out["speech"] = "Unverified, so treat it carefully: " + lead
            elif scope == "world":
                # Checked against something outside the store. Say it flat.
                out["speech"] = lead
            else:
                # 'quote' (the memory quotes itself correctly) or 'internal'
                # (it links to other memories) — graded green, but nothing in
                # the world can refute either. Measured on the live store: 3 of
                # 4 answers Casper would speak came out of this branch, flat,
                # as confident fact. A spoken sentence carries more authority
                # than a dashboard row, so this is where the conflation does
                # the most damage.
                #
                # The fix is attribution, not hedging. A recorded decision is
                # real and worth saying; it just must not sound like a
                # measurement. Absent scope lands here too: unknown is not
                # verified.
                out["speech"] = "You wrote: " + lead
        return out

    # --- status / everything else ----------------------------------------
    b = vc.briefing(**kw)
    out["intent"] = "status"
    out["speech"] = b["headline"] + ((" " + b["action"] + ".") if b.get("action") else "")
    return out



# Which BRANCH of the tree a question is about.
#
# Measured 2026-08-29: 70.6% of every interaction with this tool is `say` —
# talking to Casper — and 27.1% is `fix`. Everything else together is under
# 2%. So speech IS the interface, and it was answering three different
# questions with one identical sentence: "what am I working on", "what did I
# leave unfinished" and "what is going on" all returned the repair-queue
# headline. That is also why `fix` is 27% of presses: it was the only action
# ever offered.
#
# The tree already computes five branches, each with its own meaning line.
# This routes the question to the branch that answers it. No new data — the
# coarse intent is being split, not a layer added.
_BRANCH = [
    ("dormant", re.compile(r"\b(left|unfinished|abandon\w*|dropped|stalled|"
                           r"forgot\w*|old project|dormant|sitting)\b", re.I)),
    ("repair", re.compile(r"\b(broke\w*|stale|wrong|no longer true|failed|"
                          r"repair|rot\w*)\b", re.I)),
    ("moving", re.compile(r"\b(working on|work on|goal|milestone|progress|"
                          r"moving|shipping|close to)\b", re.I)),
    ("live", re.compile(r"\b(session|agent|running|who is|whos|open right now|"
                        r"fleet)\b", re.I)),
    ("unassessed", re.compile(r"\b(not measured|no goal|unmeasured|blind|"
                              r"untracked)\b", re.I)),
]


def _speak_branch(kind: str) -> str:
    """One branch of the tree, said aloud — its meaning, then the top of it."""
    import tree as _tree
    t = _tree.build()
    b = next((x for x in t["children"] if x["kind"] == kind), None)
    if b is None or not b["children"]:
        return ""
    # Through _as_idea, which already strips what a file line carries and a
    # mouth cannot say: paths, URLs, SHOUTED tokens, bracketed asides. Without
    # it the repair branch read out "path colon slash Users slash badenath
    # slash dot claude slash plans slash elegant-forging-clover dot md".
    from voice import _as_idea
    lead = "%s: %s." % (b["label"].lower().capitalize(), b["meaning"])
    # Two, not five. A list read aloud is a wall in the ear the same way it is
    # on a page — the tree is there for the rest.
    for k in b["children"][:2]:
        what = _as_idea(k["label"])[:90]
        why = _as_idea(k["meaning"] or "")[:110].rstrip(".")
        lead += " %s%s." % (what, (" — " + why) if why else "")
    return lead


def _default_runner(action: str) -> Dict[str, Any]:
    import brain as br
    return br.ACT_RUNNER(action, "")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="meditate say", description="One voice turn over the graded data")
    ap.add_argument("words", nargs="*", help="what was said")
    ap.add_argument("--speak", action="store_true", help="say the answer aloud")
    ap.add_argument("--allow-actions", action="store_true",
                    help="let this turn actually run commands")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    r = turn(" ".join(args.words), allow_actions=args.allow_actions)
    if args.speak and r["speech"]:
        import voice as vc
        vc._speak(r["speech"])
    if args.json:
        print(json.dumps({"tool_name": "meditate_converse", "success": True,
                          "data": r, "metadata": {}, "errors": []}, indent=2))
        return 0
    print("you: %s" % (r["heard"] or "(nothing)"))
    print("👻  %s" % r["speech"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
