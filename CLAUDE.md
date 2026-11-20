@AGENTS.md

## Específico de Claude Code
- Los hooks están en `.claude/settings.json`: `SessionStart` corre `./coach session-brief` y `Stop` corre `./coach hook-stop`.
- Las skills están en `.claude/skills/`. Si una petición calza con una, úsala.
- `./coach repertoire set` y `./coach style set` piden permiso a propósito: son decisiones del alumno.
- Responde en español, directo e informal. El tono fino lo pone `coaches/<estilo activo>.md`.
