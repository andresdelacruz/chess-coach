---
name: entrenar
description: Ejercicios con posiciones de los propios errores del alumno (puzzles de sus partidas). Úsala con "entrenemos", "dame ejercicios", "puzzles de mis errores", "practiquemos finales".
---

# Entrenar con tus propios errores

**Activadores:** "ejercicios", "puzzles", "entrenemos finales, táctica…", tareas del `estado/plan.md`.

**Entradas:** el foco (el que pida el alumno o el del plan) y `./coach positions --random -n 5 [--phase …] [--cls blunder,mistake]`, **sin** `--reveal`.

## Pasos
1. Una posición a la vez: `./coach board "<FEN>"`, quién juega y el contexto ("en esta partida jugaste otra cosa y te costó caro").
2. Espera su jugada y corrige con `./coach check "<FEN>" <jugada>`.
   - Si es `best` o `good`, felicítalo en corto y sigue.
   - Si no, dale **una pista** conceptual (no la jugada) y deja un segundo intento. A la segunda, muestra la solución y el porqué (con `kb`).
3. Si falla dos veces el mismo tipo de posición, anótalo.

## Salida
- Puntaje x/5 y el patrón que más le costó.
- `estado/bitacora.md` (1 línea), y `errores-recurrentes.md` si se repite.

## Criterio de cierre
Cada corrección salió de `check`; la solución nunca se mostró antes de que intentara.
