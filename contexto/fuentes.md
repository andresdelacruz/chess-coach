# Fuentes: cuál manda

Cada dato tiene una sola fuente oficial. Las demás la citan, no la copian.

| Dato | Fuente que manda | Se lee con | Se escribe con | Notas |
|---|---|---|---|---|
| Partidas (PGN, resultado, rating, apertura) | Chess.com / Lichess → `data/games.db` | `./coach last`, `game`, `stats`, `sql` | `./coach sync`, `import-pgn` | Chess.com puede tardar hasta ~12 h en publicar partidas nuevas por caché; Lichess es inmediato |
| Análisis por jugada | Stockfish local → `data/games.db` (`moves`) | `./coach game`, `positions`, `stats` | `./coach analyze` | Determinista para la misma versión y tiempo por jugada |
| Estadística de aperturas | Explorador de Lichess (caché de 30 días en `games.db`) | `./coach move-stats`, `train suggest` | automático | Requiere `LICHESS_TOKEN` (gratis); sin token se usa solo lo tuyo + Stockfish |
| Repertorio declarado, estilo de coach | `config.json` | `./coach repertoire`, `./coach style` | `./coach repertoire set`, `style set` (con permiso del alumno) | Cada cambio queda en `estado/decisiones.md` |
| Teoría | `knowledge/*.md` | `./coach kb` + leer el archivo | el coach, cuando el alumno lo pide | Siempre resumido con palabras propias |
| Interpretación: perfil, patrones, plan | `estado/` | leer el MD | el coach | Solo conclusiones con evidencia (uuid o URL), nunca tablas copiadas de la DB |
| Qué pasó y qué sigue | `estado/actual.md`, `bitacora.md` | leer el MD | el coach (exigido por el hook Stop) | |
| Decisiones | `estado/decisiones.md` | leer el MD | el código, al fijar repertorio o estilo; el coach, para otras decisiones del alumno | Qué cambió, por qué y quién decidió |
| Uso y errores | `data/events.jsonl` | `./coach events` | automático por cada comando | Sin secretos |
| Anomalías | `estado/anomalias.md` | leer el MD | `./coach anomalies` + el coach actualiza el estado | Formato ANO-xxx |
| Entregables | `entregables/` | leer el MD | el coach o `train end` | Fechados, no se sobrescriben |
| Secretos (`LICHESS_TOKEN`) | `.env` | solo el código | el alumno | El coach no lo lee (permiso denegado) |
