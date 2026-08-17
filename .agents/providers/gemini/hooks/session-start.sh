#!/bin/sh

set -eu

candidate_roots="${GEMINI_PROJECT_DIR:-${CLAUDE_PROJECT_DIR:-.}} ${HOME:-}"
fallback='{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"dotagents discovery meta-skill was not found in .agents/skills."}}'

for root in $candidate_roots; do
  common="$root/.agents/hooks/discovery-common.sh"
  if [ -x "$common" ]; then
    exec "$common" "$root"
  fi
done

printf '%s\n' "$fallback"
