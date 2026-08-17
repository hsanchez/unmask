---
name: dotagents-discovery
description: Discovers and invokes the skills installed by dotagents. Use when starting a session or when you need to determine which skill applies to the current task.
---

# Dotagents Skill Discovery

This repository uses dotagents-managed skills. Only skills materialized under
`.agents/skills/` are available for this session.

## Discovery flow

When a task arrives:

1. Inspect the available skill directories under `.agents/skills/`.
2. Match the task to the description and trigger guidance in the relevant
   `SKILL.md`.
3. Read the matching skill completely before taking task-specific action.
4. Follow that skill's workflow and verification requirements.

For session readiness requests such as "are you on?", invoke the `startup`
skill. It performs the repository readiness check before work begins.

Do not claim that a skill is unavailable until the managed `.agents/skills/`
directory has been checked. Do not use package skills that are absent from the
managed runtime.
