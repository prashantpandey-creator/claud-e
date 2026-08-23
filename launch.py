#!/usr/bin/env python3
"""Launch continuation chats in new terminal windows; report archive candidates.

Reads the INDEX.md of each meditation session and does two things:
(1) opens a new Claude Code terminal for every live (🟢) thread, with the
    right working directory and kickoff prompt;
(2) reports which SESSIONS are fully settled (every thread ✅) so they're
    ready to archive.

This script never calls `archive_session` itself — it can't: that's an MCP
tool, only callable by a live Claude Code agent, not a standalone Python
process. It only detects and reports; the agent running /meditate does the
actual confirmed archive call (see SKILL.md Phase B4). Rule 0 shape: script
computes the WHAT, agent performs the part that needs the real tool call.
"""
import os, re, sys, subprocess, json, glob
from pathlib import Path

MEDITATION_DIR = os.path.expanduser("~/.claude/meditation/sessions")
CLAUDE_PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
FALLBACK_CWD = os.path.expanduser("~/vyasa")


def find_session_cwd(short_session_id: str) -> str:
    """Look up the real working directory a session ran in, from its own
    transcript (same field sessions.py already reads). Falls back to
    FALLBACK_CWD only if no transcript match is found."""
    matches = glob.glob(os.path.join(CLAUDE_PROJECTS_DIR, "*", f"{short_session_id}*.jsonl"))
    for path in matches:
        try:
            with open(path) as f:
                for line in f:
                    try:
                        o = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if o.get("cwd"):
                        return o["cwd"]
        except OSError:
            continue
    print(f"  ⚠️  no cwd found for session {short_session_id}, falling back to {FALLBACK_CWD}")
    return FALLBACK_CWD


def _parse_thread_row(line: str, session_slug: str) -> dict:
    """Parse one '| # | Thread | vṛtti (kind) | Status | Memory |' row.
    Returns None if the line isn't a data row of that shape."""
    if "|" not in line or ("🟢" not in line and "✅" not in line):
        return None
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 6:
        return None
    thread_name = parts[2].replace("**", "").strip("* ").strip()
    status_cell = parts[4].strip()
    memory = parts[5].strip()
    return {
        "session": session_slug,
        "thread": thread_name,
        "status": "live" if "🟢" in status_cell else "settled",
        "memory": memory,
    }


def find_threads(session_dir: str = None) -> list:
    """Find every thread (live 🟢 or settled ✅) across meditation sessions."""
    threads = []
    base = session_dir or MEDITATION_DIR
    if not os.path.exists(base):
        return threads

    for session_slug in os.listdir(base):
        index_path = os.path.join(base, session_slug, "INDEX.md")
        if not os.path.exists(index_path):
            continue

        with open(index_path) as f:
            content = f.read()

        for line in content.split("\n"):
            row = _parse_thread_row(line, session_slug)
            if row is None:
                continue

            if row["status"] == "live":
                # Attach the thread's OWN continuation chat. The Memory column
                # usually names it (→ **file.md**); only when it doesn't do we
                # fall back to the first continue*.md — the old behavior gave
                # EVERY live row in a session the same first-file kickoff.
                sdir = os.path.join(base, session_slug)
                target = None
                m = re.search(r"([A-Za-z0-9._-]+\.md)", row.get("memory", ""))
                if m and os.path.exists(os.path.join(sdir, m.group(1))):
                    target = m.group(1)
                else:
                    for fname in sorted(os.listdir(sdir)):
                        if fname.startswith("continue") and fname.endswith(".md"):
                            target = fname
                            break
                if target:
                    chat_path = os.path.join(sdir, target)
                    with open(chat_path) as cf:
                        chat_content = cf.read()
                    marker = "## Start a fresh chat with"
                    prompt_start = chat_content.find(marker)
                    kickoff = ""
                    if prompt_start >= 0:      # find() returns 0 when the file
                                               # STARTS with the marker; `> 0`
                                               # silently yielded no kickoff
                        tail = chat_content[prompt_start + len(marker):]
                        # Take EVERYTHING up to the next heading, then strip
                        # fence markers. Reading only between the first two
                        # ``` handed the terminal a bare `cd` and threw the
                        # prompt away whenever the template fenced just the
                        # cd line — the thread opened in the right directory
                        # with nothing to do.
                        nxt = tail.find("\n## ")
                        body = tail[:nxt] if nxt > 0 else tail
                        kickoff = "\n".join(
                            l for l in body.splitlines()
                            if not l.strip().startswith("```")).strip()
                    row["path"] = chat_path
                    row["kickoff"] = kickoff
                else:
                    # No continuation chat for a LIVE thread. Report it —
                    # dropping it silently made the count read 14 when 17
                    # were found, which looks like "everything is covered".
                    row["no_chat"] = True

            threads.append(row)

    return threads


def find_archive_candidates(threads: list) -> list:
    """Sessions where EVERY thread is settled — safe to archive.
    A session with even one live thread is never a candidate."""
    by_session = {}
    for t in threads:
        by_session.setdefault(t["session"], []).append(t)

    candidates = []
    for session_slug, rows in by_session.items():
        if rows and all(r["status"] == "settled" for r in rows):
            candidates.append({
                "session": session_slug,
                "short_id": session_slug.split("-")[0],
                "thread_count": len(rows),
            })
    return candidates


FLEET_MODEL = os.environ.get("MEDITATE_FLEET_MODEL", "sonnet")


FLEET_WINDOW_FILE = "/tmp/meditate-fleet-window"


FLEET_SLOT_FILE = "/tmp/meditate-fleet-slot"


def _window_slot(step: int = 26) -> tuple:
    """Where this agent's window goes.

    Only the position. Naming was tried and removed: Claude Code already
    retitles its own window with what it is currently doing — "Locate and
    verify Razorpay key for production payments" beats "goal-razorpay-key" —
    and a static title just fights it.

    Terminal on macOS opened a WINDOW however a tab was asked for: Cmd-T,
    Shell > New Tab, and AppleWindowTabbingMode=always were each measured here
    and each produced a window (13->14, 14->15, 15->16) with tabs in the target
    window unchanged at 1. Rather than keep fighting it, name each window after
    its agent and cascade them, so a fleet reads as a stack you can scan
    instead of a pile you dig through.
    """
    n = 0
    try:
        with open(FLEET_SLOT_FILE) as f:
            n = (int(f.read().strip() or 0) + 1) % 8
    except Exception:
        n = 0
    try:
        with open(FLEET_SLOT_FILE, "w") as f:
            f.write(str(n))
    except OSError:
        pass
    x, y = 60 + n * step, 60 + n * step
    return (x, y, x + 900, y + 520)


def _fleet_window() -> int:
    """The Terminal window the fleet lives in, or 0 for 'make a new one'.

    Agents used to each get their own window. Six windows is not something you
    can look at; six tabs in one window is.
    """
    try:
        with open(FLEET_WINDOW_FILE) as f:
            return int(f.read().strip() or 0)
    except Exception:
        return 0


def _remember_fleet_window(wid: int) -> None:
    try:
        with open(FLEET_WINDOW_FILE, "w") as f:
            f.write(str(int(wid)))
    except OSError:
        pass


def build_launch(cwd: str, kickoff: str, thread_name: str, model: str = ""):
    """Build (kickoff_file, shell_cmd, applescript) — separated so tests can
    verify the command without opening windows.

    The old command was `cat file | claude`: PIPED stdin puts claude in
    non-interactive mode, so the agent ran headless-or-died and the owner got
    "just the terminal". The prompt must be an ARGUMENT — claude "$(cat f)" —
    which starts a real interactive session that stays open. cwd is quoted
    (goal cwds contain spaces: 'vedic puran').
    """
    safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in thread_name)[:40]
    kickoff_file = f"/tmp/claude-kickoff-{safe_name}.txt"
    with open(kickoff_file, "w") as f:
        f.write(kickoff)
    # Fleet agents run unattended — a permission prompt in an unwatched
    # Terminal is a silent stall (owner: "should run in allow-all ideally").
    # The gate moves into the kickoff TEXT: ship discipline rides in the
    # prompt + the SessionStart hook, not in prompts nobody is there to click.
    import shlex
    mdl = model or FLEET_MODEL
    if not all(c.isalnum() or c in "-._" for c in mdl):
        mdl = "sonnet"
    safe_cwd = cwd if os.path.isdir(cwd) else os.path.expanduser("~")
    fleet_wid = _fleet_window()
    slot = _window_slot()
    # Start a LIVE interactive session. Do NOT pass the prompt as an argument:
    # `claude "prompt"` answers once and EXITS, leaving a bare shell prompt —
    # that is exactly why dispatched agents looked like "only a terminal opened".
    shell_cmd = ("cd %s && clear && echo %s && claude --model %s "
                 "--dangerously-skip-permissions"
                 % (shlex.quote(safe_cwd),
                    shlex.quote("\u2500\u2500 " + safe_name + " \u2500\u2500"), mdl))
    as_escaped = shell_cmd.replace("\\", "\\\\").replace('"', '\\"')
    # ...then TYPE the kickoff into that live session so the agent actually
    # receives its instructions and keeps working.
    kick = " ".join(kickoff.split())
    if kick.startswith("/"):
        kick = " " + kick          # a leading / would be read as a slash command
    kick_escaped = kick.replace("\\", "\\\\").replace('"', '\\"')
    # WAIT FOR READY, don't guess. `delay 7` worked on an idle machine and
    # silently lost the kickoff whenever claude took longer to boot — the text
    # landed in the shell instead of the agent, which is exactly what "only a
    # terminal opened" looks like. Poll the tab until claude's TUI has painted.
    # ONE window, one tab per agent. Six windows scattered across the desktop
    # is not a fleet you can look at; six tabs in one window is.
    #
    # And the kickoff is submitted with a REAL Return, sent separately.
    # `do script` delivers long text as a bracketed paste, and inside a paste
    # a trailing newline is a NEWLINE, not Enter — which is exactly why short
    # kickoffs started and long ones sat in the input box waiting. Measured:
    # 40 chars submitted, 303 chars did not.
    script = ('tell application "Terminal"\n'
              '  activate\n'
              '  set reuse to false\n'
              '  if %s is not 0 then\n'
              '    try\n'
              '      set fw to window id %s\n'
              '      set index of fw to 1\n'
              '      set reuse to true\n'
              '    end try\n'
              '  end if\n'
              '  if reuse then\n'
              '    tell application "System Events" to keystroke "t" using command down\n'
              '    delay 0.5\n'
              '    set w to do script "%s" in front window\n'
              '  else\n'
              '    set w to do script "%s"\n'
              '  end if\n'
              '  set wid to id of (window 1 whose selected tab is w)\n'
              '  try\n'
              '    set bounds of (window id wid) to {%d, %d, %d, %d}\n'
              '  end try\n'
              '  set ready to false\n'
              '  repeat 90 times\n'
              '    delay 0.5\n'
              '    set txt to contents of selected tab of window id wid\n'
              '    if txt contains "bypass permissions" or txt contains "for shortcuts" then\n'
              '      set ready to true\n'
              '      exit repeat\n'
              '    end if\n'
              '  end repeat\n'
              '  if ready then\n'
              '    delay 0.8\n'
              '    do script "%s" in w\n'
              '    delay 0.6\n'
              '    set index of (window id wid) to 1\n'
              '    activate\n'
              '    tell application "System Events" to key code 36\n'
              '    return "ready:" & wid & ":" & (reuse as text)\n'
              '  else\n'
              '    return "timeout:" & wid & ":" & (reuse as text)\n'
              '  end if\n'
              'end tell' % (fleet_wid, fleet_wid, as_escaped, as_escaped,
                            slot[0], slot[1], slot[2], slot[3],
                            kick_escaped))
    return kickoff_file, shell_cmd, script


def launch_claude(cwd: str, kickoff: str, thread_name: str, model: str = "") -> bool:
    """Open a Terminal running a REAL interactive claude on the kickoff."""
    _, _, script = build_launch(cwd, kickoff, thread_name, model)
    try:
        # the script polls for up to 45s for claude's TUI, so allow more here
        r = subprocess.run(["osascript", "-e", script], check=True,
                           timeout=75, capture_output=True, text=True)
        out = (r.stdout or "").strip()
        state, _, wid = out.partition(":")
        launch_claude.last_window_id = wid.strip()
        if wid.strip().isdigit():
            _remember_fleet_window(int(wid.strip()))   # next agent joins as a tab
        if state == "timeout":
            # a window opened but claude never came up, so the kickoff was NOT
            # delivered. Saying "launched" here is how a dead agent gets
            # counted as a live one.
            print(f"  {thread_name}: terminal opened but claude never came up — "
                  f"kickoff not delivered")
            return False
        return True
    except Exception as e:
        print(f"  osascript error: {e}")
        return False


launch_claude.last_window_id = ""   # set by the call above; read by drive.py


def launch_all(auto_open: bool = False):
    """Analyze, report archive candidates, and optionally launch live threads."""
    threads = find_threads()
    live = [t for t in threads if t.get("status") == "live" and t.get("kickoff")]
    # Live but with no continuation chat to open. These used to be filtered
    # out here and never mentioned, so the count read "14 live" while 17 were
    # found — silent truncation that looks like full coverage.
    orphans = [t for t in threads if t.get("status") == "live" and t.get("no_chat")]
    archive_candidates = find_archive_candidates(threads)

    if not live and not archive_candidates:
        print("🧘 All settled. ✅ Nothing live, nothing to archive.")
        print("\n— powered by Claude —")
        return

    # ═══ ANALYSIS (always shown) ═══
    if live:
        print(f"🧘 Nirodha (Stillness) — {len(live)} live thread(s)\n")
        print(f"{'#':<3} {'Thread':<35} {'Memory':<30} {'Action'}")
        print("-" * 90)
        for i, t in enumerate(live):
            mem = t.get('memory', '-')[:28]
            action = "🔴 OPEN FIRST" if i == 0 else "🟡 Review then open"
            print(f"{i+1:<3} {t['thread']:<35} {mem:<30} {action}")
    else:
        print("🧘 No live threads to open.")

    if orphans:
        print(f"\n⚠️  {len(orphans)} live thread(s) have NO continuation chat "
              f"— they cannot be opened:")
        for t in orphans:
            print(f"   {t['session']}: {t['thread'][:64]}")
        print("   Name the chat file in that row's Memory column "
              "(e.g. `→ **thread-name.md**`), or write the chat.")

    if archive_candidates:
        print(f"\n📦 {len(archive_candidates)} session(s) fully settled — ready to archive:")
        for c in archive_candidates:
            print(f"   {c['session']} ({c['thread_count']} thread(s) done)")
        print("   This script does NOT archive them — that needs a live agent")
        print("   calling mcp__ccd_session_mgmt__archive_session (with your")
        print("   confirmation) after matching short_id above to a real")
        print("   sessionId via list_sessions. Run /meditate archive, or ask.")

    if not auto_open:
        if live:
            print(f"\n💡 To open live threads: python3 ~/.claude/skills/meditate/launch.py --open")
        print("\n— powered by Claude —")
        return

    # ═══ LAUNCH (--open flag) ═══
    if live:
        print(f"\n🚀 Opening {len(live)} Claude Code session(s)...\n")
        for i, t in enumerate(live):
            print(f"[{i+1}/{len(live)}] {t['thread']}")
            short_id = t["session"].split("-")[0]
            cwd = find_session_cwd(short_id)
            ok = launch_claude(cwd, t["kickoff"], t["thread"])
            status = "✅" if ok else f"❌ (manual: cat {t['path']})"
            print(f"  {status}")
            print()

    print("— powered by Claude —")


if __name__ == "__main__":
    launch_all(auto_open="--open" in sys.argv)
