# AGENTS.md

## Default testing and artifacts preference

- Do not record videos or create UI walkthrough artifacts by default.
- Run terminal-based checks when needed, but skip video/screenshot walkthroughs unless the user explicitly requests them.
- If a task requires manual UI validation and no video was requested, provide concise textual verification only.
- Never start screen recording automatically.
- Never attach screenshots automatically.
- Only create video or screenshot artifacts after an explicit user request in the current chat.

## Default deployment behavior

- After each completed code task, deploy by pushing the latest relevant changes to `master` to trigger auto-deploy.
- Treat deployment as mandatory by default.
- Skip deployment only if the user explicitly says to stop, delay, or avoid deploy/push to `master`.
