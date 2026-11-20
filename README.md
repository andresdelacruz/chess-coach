# ♞ Chess Coach

Tu entrenador personal de ajedrez, en tu computador. Conoce **todas tus partidas** de Chess.com y Lichess, las analiza con **Stockfish**, recuerda tus errores y te entrena **jugada a jugada** con el estilo de coach que elijas.

## Instalación (1 comando)
**macOS o Linux** (en Windows, dentro de WSL). Abre la Terminal y pega:
```bash
curl -fsSL https://raw.githubusercontent.com/andresdelacruz/chess-coach/main/install.sh | bash
```
Se abre **http://127.0.0.1:4520**: ahí pones tu usuario de Chess.com y/o Lichess, eliges tu coach y listo. En Mac queda corriendo solo (también al reiniciar).

- **Actualizar:** pega el mismo comando. No borra tus datos.
- **Apagar:** `cd ~/chess-coach && ./coach service off`
- **Desinstalar:** apágalo y borra la carpeta `~/chess-coach`.

El instalador pone Stockfish (con [Homebrew](https://brew.sh) en Mac), un Python propio y, si falta, [Claude Code](https://code.claude.com/docs/en/setup) con su instalador oficial.

### Para el chat con el coach
El chat usa **tu** cuenta de Claude a través de Claude Code. La primera vez, en la Terminal:
```bash
claude        # dentro escribe /login y entra con tu cuenta
```
En el front, ⚙ Configuración → "Probar conexión" te dice si quedó bien.

## Costos 💸
| Qué | Costo |
|---|---|
| Stockfish, APIs de Chess.com/Lichess, explorador de aperturas | **Gratis** |
| Tablero **jugada a jugada** | **Gratis** (no usa Claude) |
| **Chat** con el coach | Tu cuenta de Claude: incluido en Pro/Max, o por token si usas API/Vertex. Cada respuesta muestra su costo estimado |

## Qué hace
- **Jugada a jugada** ⭐: practicas tu salida (ej. Catalana). En cada posición cruza **tus partidas** ("acá sueles jugar X y te va mal"), **lo que juega tu nivel en Lichess** y **Stockfish**, y te recomienda una sola jugada. El rival responde como la gente de tu nivel. Modo guía: juegas tú y solo interviene si te equivocas.
- **🌳 Árbol de aperturas** (estilo OpeningTree, con Stockfish encima): en cada posición ves qué jugaste, cuántas veces, % ganadas/tablas/perdidas y **cuánto te cuesta** cada jugada. Navegas jugada a jugada, filtras por color/ritmo/período, y "Entrenar desde aquí" o "¿por qué me va mal acá?".
- **Chat con tu coach**: revisión de partidas (máximo 3 lecciones, te pregunta antes de responder), diagnóstico con plan, ejercicios con tus propios errores y repertorio.
- **Estilos de coach**: El Profe, El Grinder, El Blitzero, El Profe de Club Chileno (o el tuyo en `coaches/`).
- **Memoria**: estado, decisiones, bitácora y entregables en Markdown, todo local.
- **Sincroniza solo**: al abrir y cada 15 min, y analiza en background de lo más nuevo a lo más viejo.

> ⚠️ **Fair play:** jugada a jugada y el análisis son para **práctica**. Usarlos en una partida en vivo es asistencia y te pueden banear. El coach se niega.

**Token de Lichess (opcional, gratis):** el explorador de aperturas lo exige. Créalo sin permisos en https://lichess.org/account/oauth/token/create y pégalo en ⚙ Configuración. Sin token, jugada a jugada usa tus partidas y Stockfish.

## Privacidad
Todo corre en tu computador: la UI escucha solo en `127.0.0.1`. Tus partidas, configuración y memoria quedan en `~/chess-coach` (`config.json`, `.env`, `data/`, `estado/`, `entregables/`) y nunca se suben al repo. Lo único que sale es lo que tú le escribes al chat de Claude.

## Arquitectura (9 piezas)
| # | Pieza | Dónde |
|---|---|---|
| 1 | Interfaz | `./coach ui` → web local `127.0.0.1:4520` (`web/`), o `claude` en la terminal |
| 2 | Motor | Claude Code (`claude -p`); las reglas están en `AGENTS.md` para no amarrarse a un proveedor |
| 3 | Reglas y gobierno | `AGENTS.md` (roles, fair play), `.claude/settings.json` (permisos) |
| 4 | Contexto | `contexto/fuentes.md`, `config.json`, `coaches/`, `knowledge/` |
| 5 | Skills | `.claude/skills/`: jugada-a-jugada · revisar-partida · diagnostico · entrenar · repertorio |
| 6 | Conectores | `tools/core.py` (Chess.com, Lichess, PGN, Stockfish), `tools/trainer.py` (explorador Lichess) |
| 7 | Estado y entregables | `estado/` y `entregables/` |
| 8 | Activación | LaunchAgent (`./coach service on`), worker de sync/análisis, hook `SessionStart` |
| 9 | Verificación | hook `Stop`, `data/events.jsonl`, `./coach doctor`, `./coach anomalies`, `tests/` + `tests/aceptacion.md` |

## Para desarrolladores
```bash
./coach help                 # todos los comandos (sync, analyze, game, stats, train, doctor…)
.venv/bin/pytest -q tests    # tests
./install.sh                 # reinstala desde la copia local
```
