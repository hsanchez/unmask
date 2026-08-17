---
name: git-guardrails
description: Install git safety guardrails that block destructive git operations. Layer 1 (universal): a git pre-push hook covering every provider and tool. Layer 2 (Claude Code): a PreToolUse hook for early interception before the command runs. Use after dotagents init to harden a repo.
---

# Git Guardrails

Two protection layers against destructive git operations:

1. **git hook** (universal): blocks `git push` at the git level for all agents and tools. Humans bypass with `git push --no-verify` when intentional. CI/CD bypasses via `$CI`.
2. **agent hook** (Claude Code): intercepts dangerous commands before they run via `PreToolUse`. Requires Python 3.9+.

## Layer 1 — git pre-push hook (all providers)

If no existing `pre-push` hook is present, copy directly:

```bash
cp .agents/skills/git-guardrails/scripts/pre-push .git/hooks/pre-push
chmod +x .git/hooks/pre-push
```

If a `pre-push` hook already exists, create a dispatcher that calls both. Do **not** append to the existing hook — an `exit 0` anywhere in it would prevent the guardrail from running:

```bash
HOOK=.git/hooks/pre-push
GUARDRAIL=.agents/skills/git-guardrails/scripts/pre-push

mv "$HOOK" "${HOOK}-original"
cp "$GUARDRAIL" "${HOOK}-guardrails"
chmod +x "${HOOK}-guardrails"

cat > "$HOOK" << 'EOF'
#!/bin/sh
set -e
DIR="$(dirname "$0")"
"$DIR/pre-push-guardrails" "$@"
"$DIR/pre-push-original" "$@"
EOF
chmod +x "$HOOK"
```

The guardrail runs first so the original hook's side effects never execute if the push is blocked. `set -e` means either hook exiting non-zero stops the chain.

Verify:

```bash
git push --dry-run 2>&1 || true
```

Should print a `git-guardrails: push blocked` message and exit non-zero.

## Layer 2 — Claude Code PreToolUse hook

Requires Python 3.9+ (no other dependencies). Skip if Claude Code is not in use.

### Copy the hook script

```bash
mkdir -p .claude/hooks
cp .agents/skills/git-guardrails/scripts/block-dangerous-git .claude/hooks/block-dangerous-git
chmod +x .claude/hooks/block-dangerous-git
```

### Add to `.claude/settings.json`

Merge into the existing `hooks.PreToolUse` array — do not overwrite other settings:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/block-dangerous-git"
          }
        ]
      }
    ]
  }
}
```

### Verify

```bash
echo '{"tool_input":{"command":"git push origin main"}}' | .claude/hooks/block-dangerous-git
```

Should exit 2 and print a BLOCKED message to stderr.

## Coverage

| Operation                       | git hook | agent hook |
|---------------------------------|----------|------------|
| `git push` (all forms)          | ✓        | ✓          |
| `git push --force`              | ✓        | ✓          |
| `git reset --hard`              | —        | ✓          |
| `git clean -f[d]`               | —        | ✓          |
| `git branch -D`                 | —        | ✓          |
| `git branch --delete --force`   | —        | ✓          |
| `git checkout .` / `-- <path>`  | —        | ✓          |
| `git restore`                   | —        | ✓          |

The git hook covers push because it is the highest-risk network operation and the only destructive op with a standard pre-execution git hook. All other operations are covered by the agent hook for Claude Code only — other providers remain uncovered until they gain an equivalent hook mechanism.

## Extending to other providers

As Codex, Gemini, and other providers gain hook mechanisms equivalent to Claude Code's `PreToolUse`, add their configuration here following the same pattern: copy `scripts/block-dangerous-git`, register it in the provider's settings file.

## Known limitations

**Git alias bypass** — `git -c alias.x=push x origin main` executes `git push` via a temporary alias. The agent hook parses the command structurally but does not evaluate alias expansion. This bypass requires deliberate construction and is unlikely in normal agent use, but it exists.

**No full shell parser** — The agent hook uses `shlex.split()` for tokenization, which handles quotes and common patterns correctly. It does not interpret backticks, process substitution (`<()`), or complex compound commands. Deeply nested shell constructs may not be analyzed correctly.

**Layer 2 covers Claude Code only** — Non-Claude agents (Codex, Gemini, etc.) are protected for `git push` via the git hook, but `reset --hard`, `clean -f`, `branch -D`, and other destructive local operations remain uncovered until those providers gain equivalent hook mechanisms.
