# AGENTS.md

## Default testing and artifacts preference

- Do not record videos or create UI walkthrough artifacts by default.
- Run terminal-based checks when needed, but skip video/screenshot walkthroughs unless the user explicitly requests them.
- If a task requires manual UI validation and no video was requested, provide concise textual verification only.
- Never start screen recording automatically.
- Never attach screenshots automatically.
- Only create video or screenshot artifacts after an explicit user request in the current chat.
