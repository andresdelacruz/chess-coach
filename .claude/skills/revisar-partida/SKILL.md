---
name: revisar-partida
description: Revisa una partida del alumno como entrenador (la última o la que pida), con máximo 3 lecciones conectadas a sus errores recurrentes y a knowledge/. Úsala con "revisa mi última partida", "qué hice mal", "analiza la partida contra X".
---

# Revisar partida

**Activadores:** "revisa…", "qué hice mal", "analiza la partida contra…", partidas nuevas en el brief.

**Entradas:** la referencia de la partida (por defecto `last`; también `2` = penúltima, un uuid, una URL o el nombre del rival vía `./coach last -n 20`) y `estado/errores-recurrentes.md`.

## Pasos
1. `./coach game <ref>`. Si no estaba analizada, se analiza ahí mismo.
2. Cruza los momentos críticos con `estado/errores-recurrentes.md`. El que repite un patrón va primero.
3. Elige **máximo 3**: el que más bajó su % de ganar, el que repite un patrón y la oportunidad que no castigó.
4. Por cada momento:
   - muestra la posición con `./coach board "<FEN>"`;
   - pregúntale qué jugaría y corrige con `./coach check "<FEN>" <jugada>`;
   - explica un concepto, apoyado en `./coach kb <tema>` (cita el archivo).
   Si dice "dime nomás", explica directo.
5. Si el error principal tiene ⚠️ de apuro de reloj, la lección es de reloj (`knowledge/psicologia/gestion-del-reloj.md`).
6. Si la partida era de su repertorio declarado, di si se salió de la línea y dónde.

## Salida
- Al alumno: 3 líneas primero (lo bueno, la lección principal y un ejercicio concreto) y después el detalle.
- `entregables/AAAA-MM-DD-revision-<rival>.md` con los momentos, FEN, la lección y el ejercicio.
- `estado/bitacora.md` (1 línea), `estado/actual.md` (siguiente paso) y `errores-recurrentes.md` si el patrón suma 2 o más partidas.

## Criterio de cierre
Toda jugada y evaluación citada viene de `game` o `check` de esta sesión; hay como máximo 3 lecciones; el entregable y la bitácora quedaron escritos.
