---
name: repertorio
description: Revisa y gestiona el repertorio de aperturas con sus resultados reales; detecta dónde sale de la línea o pierde. Úsala con "qué aperturas me convienen", "mi repertorio", "de blancas estoy jugando Catalana", "por qué pierdo con la siciliana".
---

# Repertorio

**Activadores:** declara lo que juega ("de blancas estoy jugando Catalana"), pregunta por sus aperturas, quiere cambiar.

**Entradas:** `./coach repertoire`, `./coach stats --min-games 3` (sección Aperturas), `knowledge/aperturas/` y `estado/perfil.md`.

## Pasos
1. **Declaración:** si el alumno dice qué juega, confirma y fija:
   `./coach repertoire set white "Catalana" --why "<su motivo>"`. Claude Code le pide permiso y queda en `estado/decisiones.md`.
2. **Cumplimiento:** `./coach repertoire` muestra el % en repertorio, el score dentro y fuera y lo que termina jugando en vez de lo declarado.
3. **Huecos:** para las 2–3 aperturas con más partidas y peor score:
   ```
   ./coach sql "SELECT g.uuid, g.my_result, m.move_no, m.san, m.best_san, m.win_drop FROM moves m JOIN games g ON g.uuid=m.game_uuid WHERE g.opening_family='<familia>' AND g.my_color='<color>' AND m.is_me=1 AND m.phase='opening' AND m.win_drop>=10 ORDER BY g.end_time DESC"
   ```
   Si el error se repite en la misma jugada o posición, es un hueco de repertorio. Ofrece repararlo con la skill **jugada-a-jugada** desde esa posición (`--moves`).
4. **Recomendación** comprometida: mantener, reparar (la línea y la idea) o cambiar (por qué y con qué alternativa coherente con su estilo). **Cambiar es decisión del alumno.**

## Salida
- Al alumno: el veredicto en 2–3 líneas y después el detalle.
- `knowledge/aperturas/<apertura>.md` con las ideas clave, si no existe y el alumno quiere.
- La bitácora y el siguiente paso en `estado/actual.md`.

## Criterio de cierre
Toda cifra viene de `repertoire`, `stats` o `sql`; no se cambió la configuración sin el sí del alumno.
