#!/usr/bin/env bash
# Install meditate for one user. Nothing else on the machine changes.
#
# It does exactly three things:
#   1. puts this directory where Claude Code looks for skills
#   2. puts `meditate` on your PATH
#   3. runs the suite, and tells you if anything is red
#
# It starts no background service, installs no launch agent, and asks for no
# permissions. Undo it with ./uninstall.sh.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
SKILLS="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
DEST="$SKILLS/meditate"
BIN="${MEDITATE_BIN:-$HOME/.local/bin}"

echo "meditate installer"
echo

# 1 — the skill
if [ "$SRC" != "$DEST" ]; then
  mkdir -p "$SKILLS"
  if [ -e "$DEST" ] && [ ! -L "$DEST" ]; then
    echo "  [skip]  $DEST already exists and is not a link — leaving it alone"
  else
    ln -sfn "$SRC" "$DEST"
    echo "  [ok]    skill linked: $DEST"
  fi
else
  echo "  [ok]    already installed at $DEST"
fi

# 2 — the command
mkdir -p "$BIN"
ln -sf "$SRC/meditate" "$BIN/meditate"
chmod +x "$SRC/meditate"
echo "  [ok]    command: $BIN/meditate"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "  [note]  $BIN is not on your PATH — add it:"
     echo "            echo 'export PATH=\"$BIN:\$PATH\"' >> ~/.zshrc" ;;
esac

# 3 — prove it works here, on this machine, before claiming it is installed
echo
echo "  running the suite..."
red=0
for t in "$SRC"/test_*.py; do
  name=$(basename "$t")
  if out=$(python3 "$t" 2>&1); then
    printf '  [ok]    %-24s %s\n' "$name" "$(echo "$out" | tail -1)"
  else
    red=1
    printf '  [RED]   %-24s %s\n' "$name" "$(echo "$out" | tail -1)"
  fi
done

echo
if [ "$red" = 0 ]; then
  echo "  installed. start with:  meditate sessions"
else
  echo "  installed, but the suite is red above — that is a real failure on"
  echo "  this machine, not a warning. Please open an issue with the output."
  exit 1
fi
