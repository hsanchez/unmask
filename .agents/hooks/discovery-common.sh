#!/bin/sh

# Shared by the claude/gemini/codex SessionStart hooks: given a resolved
# project directory as $1, emit the SessionStart hookSpecificOutput payload
# that injects the dotagents-discovery meta-skill as additional context.
# Each provider script resolves its own project directory (env var or git),
# then delegates here so the loading/escaping/fallback logic lives in one
# place instead of three near-identical copies.

set -eu

project_directory=$1
meta_skill="$project_directory/.agents/skills/dotagents-discovery/SKILL.md"
preface='dotagents discovery is enabled. Read the injected meta-skill before selecting a task skill.'

if [ ! -f "$meta_skill" ]; then
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"dotagents discovery meta-skill was not found in .agents/skills."}}'
  exit 0
fi

if command -v jq >/dev/null 2>&1; then
  message=$({ printf '%s\n\n' "$preface"; cat "$meta_skill"; } | jq -Rs .)
elif command -v python3 >/dev/null 2>&1; then
  message=$(META_SKILL_PATH="$meta_skill" PREFACE="$preface" python3 - <<'PY'
import json
import os
from pathlib import Path

meta_skill = Path(os.environ["META_SKILL_PATH"]).read_text(encoding="utf-8")
print(json.dumps(f"{os.environ['PREFACE']}\n\n{meta_skill}"))
PY
  )
else
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"dotagents discovery meta-skill is installed, but jq or python3 is required to inject it."}}'
  exit 0
fi

printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":%s}}\n' "$message"
