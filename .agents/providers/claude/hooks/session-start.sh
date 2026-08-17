#!/bin/sh

set -eu

# Try the current project first, then the global runtime — a global-only
# install (claude/settings.json syncs with scope=both) has no project-local
# .agents, so $CLAUDE_PROJECT_DIR alone would silently find nothing.
candidate_roots="${CLAUDE_PROJECT_DIR:-.} ${HOME:-}"
fallback='{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"dotagents discovery meta-skill was not found in .agents/skills."}}'

for root in $candidate_roots; do
  common="$root/.agents/hooks/discovery-common.sh"
  if [ -x "$common" ]; then
    exec "$common" "$root"
  fi
done

printf '%s\n' "$fallback"
