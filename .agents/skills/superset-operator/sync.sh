#!/bin/sh
# The voice policy is read from ~/.codex/skills at runtime; this repo holds the
# versioned copy. A copy drifts silently, so compare rather than trust it.
#
#   sync.sh check   exit 1 if the two differ (use in CI or before a release)
#   sync.sh pull    bring runtime edits into the repo
#   sync.sh push    install the repo version into the runtime location
set -eu
LIVE="$HOME/.codex/skills/superset-operator/SKILL.md"
REPO="$(cd "$(dirname "$0")" && pwd)/SKILL.md"
case "${1:-check}" in
  check) if cmp -s "$LIVE" "$REPO"; then echo "in sync"; else echo "DRIFTED: $LIVE differs from $REPO"; diff "$REPO" "$LIVE" | head -40; exit 1; fi ;;
  pull)  cp "$LIVE" "$REPO" && echo "pulled runtime -> repo" ;;
  push)  cp "$REPO" "$LIVE" && echo "pushed repo -> runtime" ;;
  *)     echo "usage: sync.sh [check|pull|push]" >&2; exit 2 ;;
esac
