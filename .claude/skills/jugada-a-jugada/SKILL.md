---
name: jugada-a-jugada
description: Entrenamiento jugada por jugada. En cada posición el coach cruza tus partidas, lo que juega tu nivel en Lichess y Stockfish, recomienda UNA jugada y el rival responde como un jugador real de tu nivel. Úsala con "entrenemos la Catalana", "guíame jugada a jugada", "juguemos una de práctica", "quiero practicar mi salida".
---

# Jugada a jugada

**Activadores:** practicar una apertura o salida, "guíame", "juguemos de práctica", "jugada a jugada".

**Entradas:** color y apertura objetivo (si no las dice, usa el repertorio del brief: "con blancas declaraste Catalana, ¿esa?"), modo (`recomendar` por defecto, o `guia` si quiere jugar solo y que lo corrijan) y jugadas iniciales opcionales.

**⚠️ Antes de partir:** si hay señales de que está jugando una partida real en este momento, **no sigas** (regla de fair play del AGENTS.md §3.1).

## Pasos
1. **Retomar o iniciar.** Si el brief muestra una sesión activa, `./coach train status` y ofrece seguir. Si no:
   `./coach train start --color white --opening "Catalana" [--moves "1.d4 Nf6 2.c4 e6 3.g3"] [--mode guia]`
2. **Ciclo por jugada** (una a la vez, sin adelantarte):
   - **Tu turno, modo recomendar:** `./coach train suggest`. Muestra el tablero (`./coach train status`) y da **una** recomendación con su idea en una frase, conectando con `knowledge/` si aplica. Si hay ⚠️ de hábito ("acá sueles jugar X y te va mal"), dilo: es el corazón del método. Después espera la jugada del alumno.
   - **Tu turno, modo guía:** no sugieras. Espera su jugada.
   - **El alumno juega:** `./coach train play <jugada>`. Comenta en 1 línea. Si es error o error grave, explica el porqué antes de seguir. En modo guía, interviene solo cuando el comando lo indique (caída de 10% o más).
   - **Turno del rival:** `./coach train rival`. Di qué jugó y de dónde salió ("lo que más juega tu nivel", "lo que te jugaron tus rivales reales"). Si el alumno quiere elegir la jugada del rival: `./coach train rival --move <jugada>`.
3. **Fuera de libro:** cuando `suggest` diga FUERA DE LIBRO, avisa ("acá se acaba la teoría de tu nivel") y pasa a guiar por plan y estructura (`knowledge/estrategia/`), sin dejar de usar `suggest`.
4. **Cuándo parar:** al llegar a 12–15 jugadas, cuando la posición se estabilice o cuando el alumno lo pida. Después, `./coach train end`.

## Salida
- `train end` guarda el entregable `entregables/AAAA-MM-DD-jugada-a-jugada-<id>.md` y agrega la línea a la bitácora.
- Resume en 3 líneas: dónde se desvió, la idea clave de la salida y la posición para repetir.
- Si una desviación se repite entre sesiones, agrégala a `estado/errores-recurrentes.md` y sugiere repetir la sesión desde esa posición con `--moves`.

## Criterio de cierre
- Cada recomendación salió de `train suggest` (nunca de memoria).
- El alumno vio **una** jugada a la vez.
- La sesión quedó cerrada con `train end`, o abierta a propósito con el siguiente paso anotado en `estado/actual.md`.

## Costo
Cada jugada es un turno de conversación (tokens). Responde corto: tablero, una frase y la recomendación. Lo pesado lo calcula `./coach`.
