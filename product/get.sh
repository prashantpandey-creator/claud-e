#!/usr/bin/env sh
# meditate — one-line install.
#
#   curl -fsSL https://raw.githubusercontent.com/prashantpandey-creator/meditate-sessions/main/get.sh | sh
#
# Clones into ~/.claude/skills/meditate and runs the installer, which links
# the command and runs the suite. Nothing runs in the background, no launch
# agent is installed, no permissions are asked for.
set -eu
REPO="${MEDITATE_REPO:-https://github.com/prashantpandey-creator/meditate-sessions.git}"
DEST="${MEDITATE_DIR:-$HOME/.claude/skills/meditate}"

command -v git >/dev/null 2>&1 || { echo "meditate needs git"; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "meditate needs python3 (3.9+)"; exit 1; }

if [ -d "$DEST/.git" ]; then
  echo "updating $DEST"
  git -C "$DEST" pull --ff-only --quiet
else
  if [ -e "$DEST" ]; then
    echo "$DEST already exists and is not a meditate checkout — move it first"
    exit 1
  fi
  echo "cloning into $DEST"
  mkdir -p "$(dirname "$DEST")"
  git clone --quiet --depth 1 "$REPO" "$DEST"
fi

exec sh "$DEST/install.sh"
