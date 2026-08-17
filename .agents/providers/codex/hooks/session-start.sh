#!/bin/sh

set -eu

candidate_roots="$(git rev-parse --show-toplevel 2>/dev/null || pwd) ${HOME:-}"
fallback='{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"dotagents discovery meta-skill was not found in .agents/skills."}}'

for root in $candidate_roots; do
  common="$root/.agents/hooks/discovery-common.sh"
  if [ -x "$common" ]; then
    exec "$common" "$root"
  fi
done

printf '%s\n' "$fallback"
