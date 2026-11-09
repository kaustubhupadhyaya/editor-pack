---
name: skill-name-here
description: >-
  One or two sentences describing WHEN to use this skill. Be specific — this
  text is what Claude matches against to decide whether to load the skill, so
  name the trigger conditions and the task. Example: "Use when the user wants to
  remove filler words (um, uh, like) from a video or audio cut in Descript."
---

# Skill Name

## When to use

Plain-language description of the trigger conditions. What is the user asking for
when this skill should fire? What does it explicitly NOT cover?

## Inputs

- What the skill needs to start (a Descript project ID, a media URL, a folder of
  clips, target platform, etc.).
- Sensible defaults if the user doesn't specify.

## Steps

1. Identify the target project/media (e.g. `list_projects` → `get_project` to
   find the composition ID).
2. Perform the edit (e.g. `prompt_project_agent` with a precise instruction).
3. Wait for the job if it runs async (`wait_for_job`).
4. Verify the result and report back (share URL via `publish_project` if asked).

## Notes

- Relevant tools: Descript MCP — `import_media`, `prompt_project_agent`,
  `get_project`, `list_projects`, `wait_for_job`, `publish_project`.
- Gotchas, conventions, and anything that saves a redo next time.
