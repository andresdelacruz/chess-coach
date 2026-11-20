#!/usr/bin/env python3
"""
coach — CLI del Chess Coach. Ver `./coach help`.

Piezas: core.py (sync, análisis, consultas), trainer.py (jugada a jugada), health.py (verificación).
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import health  # noqa: E402
import trainer  # noqa: E402
from core import *  # noqa: E402,F401,F403


def main():
    ap = argparse.ArgumentParser(prog="coach", description="Chess Coach — datos para tu entrenador")
    sp = ap.add_subparsers(dest="cmd")

    def add_filters(p):
        p.add_argument("--days", type=int)
        p.add_argument("--time-class", choices=["bullet", "blitz", "rapid", "daily"])
        p.add_argument("--source", choices=["chesscom", "lichess", "pgn"])
        p.add_argument("--color", choices=["white", "black"])

    p = sp.add_parser("sync", help="baja partidas nuevas de tus cuentas")
    p.add_argument("--full", action="store_true", help="re-descarga todo el historial")
    p.add_argument("--timeout", type=int, default=20)
    p.set_defaults(f=cmd_sync)

    p = sp.add_parser("import-pgn", help="importa un archivo .pgn (OTB, otra web)")
    p.add_argument("file")
    p.add_argument("--as-name", nargs="*", help="tus nombres en el PGN si difieren de tus usuarios")
    p.add_argument("--time-class", choices=["bullet", "blitz", "rapid", "daily"])
    p.set_defaults(f=cmd_import_pgn)

    p = sp.add_parser("index", help="índice de posiciones de todas tus partidas (árbol de aperturas)")
    p.set_defaults(f=cmd_index)

    p = sp.add_parser("analyze", help="analiza pendientes con Stockfish")
    p.add_argument("--limit", type=int)
    p.add_argument("--uuid")
    p.set_defaults(f=cmd_analyze)

    p = sp.add_parser("last", help="lista tus últimas partidas")
    p.add_argument("-n", type=int, default=10)
    p.set_defaults(f=cmd_last)

    p = sp.add_parser("game", help="informe de una partida (last | N | uuid | url)")
    p.add_argument("ref", nargs="?", default="last")
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--min-drop", type=float, default=10)
    p.add_argument("--pgn", action="store_true")
    p.set_defaults(f=cmd_game)

    p = sp.add_parser("stats", help="patrones y estadísticas")
    add_filters(p)
    p.add_argument("--min-games", type=int, default=3)
    p.set_defaults(f=cmd_stats)

    p = sp.add_parser("positions", help="posiciones de tus errores para entrenar")
    add_filters(p)
    p.add_argument("--cls", default="blunder,mistake")
    p.add_argument("--phase", choices=["opening", "middlegame", "endgame"])
    p.add_argument("-n", type=int, default=5)
    p.add_argument("--random", action="store_true")
    p.add_argument("--reveal", action="store_true", help="muestra la solución")
    p.set_defaults(f=cmd_positions)

    p = sp.add_parser("check", help="evalúa una jugada en un FEN")
    p.add_argument("fen")
    p.add_argument("move")
    p.add_argument("--time", type=float, default=1.0)
    p.set_defaults(f=cmd_check)

    p = sp.add_parser("board", help="dibuja un FEN")
    p.add_argument("fen")
    p.add_argument("--white", action="store_true")
    p.add_argument("--black", action="store_true")
    p.set_defaults(f=cmd_board)

    p = sp.add_parser("sql", help="consulta SQL de solo lectura")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(f=cmd_sql)

    p = sp.add_parser("kb", help="índice/búsqueda en knowledge/")
    p.add_argument("query", nargs="*")
    p.set_defaults(f=cmd_kb)

    p = sp.add_parser("style", help="lista estilos de coach · style set <nombre> lo cambia")
    p.add_argument("action", nargs="?", choices=["list", "set"], default="list")
    p.add_argument("name", nargs="?")
    p.add_argument("--why", help="motivo (queda en estado/decisiones.md)")
    p.set_defaults(f=cmd_style)

    p = sp.add_parser("repertoire", help="repertorio declarado: ver cumplimiento o fijarlo")
    p.add_argument("action", nargs="?", choices=["show", "set"], default="show")
    p.add_argument("key", nargs="?", help="white | vs_e4 | vs_d4 | vs_otros")
    p.add_argument("value", nargs="*", help='ej: Catalana, Londres')
    p.add_argument("--why", help="motivo (queda en estado/decisiones.md)")
    add_filters(p)
    p.set_defaults(f=cmd_repertoire)

    p = sp.add_parser("session-brief", help="(hook) sync + resumen al abrir Claude Code")
    p.set_defaults(f=cmd_session_brief)

    p = sp.add_parser("tick", help="(cron/launchd) sync + analiza 10")
    p.set_defaults(f=cmd_tick)

    p = sp.add_parser("watch", help="loop en primer plano: sync + análisis")
    p.add_argument("--interval", type=int, default=300)
    p.set_defaults(f=cmd_watch)

    p = sp.add_parser("autosync", help="macOS: on/off sync automático en background")
    p.add_argument("action", choices=["on", "off"])
    p.add_argument("--interval", type=int, default=600)
    p.set_defaults(f=cmd_autosync)

    # ── jugada a jugada ──
    p = sp.add_parser("move-stats", help="una posición: tus partidas + explorador Lichess + Stockfish → 1 recomendación")
    p.add_argument("fen")
    p.add_argument("--no-explorer", action="store_true")
    p.add_argument("--no-engine", action="store_true")
    p.add_argument("--time", type=float, default=0.5)
    p.set_defaults(f=trainer.cmd_move_stats)

    p = sp.add_parser("train", help="sesión jugada a jugada: start | suggest | play | rival | status | end")
    p.add_argument("action", choices=["start", "suggest", "play", "rival", "status", "end"])
    p.add_argument("move", nargs="?", help="tu jugada (play)")
    p.add_argument("--color", choices=["white", "black"], default="white")
    p.add_argument("--opening", help="apertura objetivo, ej. Catalana")
    p.add_argument("--moves", help='jugadas iniciales, ej. "1.d4 Nf6 2.c4 e6 3.g3"')
    p.add_argument("--mode", choices=["recomendar", "guia"], default="recomendar",
                   help="recomendar = sugiere cada jugada; guia = juegas tú y solo interviene si te equivocas")
    p.add_argument("--move", dest="rival_move", help="(rival) fuerza la jugada del rival")
    p.add_argument("--seed", type=int)
    p.add_argument("--time", type=float, default=0.4)
    p.add_argument("--no-explorer", action="store_true")
    p.add_argument("--force", action="store_true")
    p.set_defaults(f=trainer.cmd_train)

    # ── verificación y observabilidad ──
    p = sp.add_parser("doctor", help="chequeo de salud del harness")
    p.add_argument("--net", action="store_true", help="prueba también la red a Chess.com/Lichess")
    p.set_defaults(f=health.cmd_doctor)

    p = sp.add_parser("anomalies", help="busca casos imposibles y los registra en estado/anomalias.md")
    p.set_defaults(f=health.cmd_anomalies)

    p = sp.add_parser("events", help="resumen del registro de eventos")
    p.add_argument("--days", type=int, default=7)
    p.set_defaults(f=health.cmd_events)

    p = sp.add_parser("ui", help="front web local: chat con el coach + tablero jugada a jugada")
    p.add_argument("--port", type=int, default=4520)
    p.add_argument("--no-open", action="store_true", help="no abrir el navegador")
    p.set_defaults(f=lambda a: __import__("webui").cmd_ui(a))

    p = sp.add_parser("service", help="macOS: la UI web como servicio (on | off | status)")
    p.add_argument("action", choices=["on", "off", "status"])
    p.add_argument("--port", type=int, default=4520)
    p.set_defaults(f=cmd_service)

    p = sp.add_parser("hook-stop", help="(hook Stop de Claude Code)")
    p.set_defaults(f=health.cmd_hook_stop)

    sp.add_parser("help")
    a = ap.parse_args()
    if not a.cmd or a.cmd == "help":
        ap.print_help()
        return
    if a.cmd == "train" and a.action == "play" and not a.move:
        ap.error("train play necesita tu jugada, ej: ./coach train play Nf3")
    if a.cmd == "train":
        a.move = a.rival_move if a.action == "rival" else a.move
    t0 = time.time()
    try:
        a.f(a)
    except SystemExit as e:
        if a.cmd not in ("hook-stop", "session-brief", "ui"):
            health.log_event(a.cmd, vars(a), e.code in (0, None), int((time.time() - t0) * 1000), t0, None if e.code in (0, None) else "exit")
        raise
    except Exception as e:
        health.log_event(a.cmd, vars(a), False, int((time.time() - t0) * 1000), t0, f"{e.__class__.__name__}: {e}")
        raise
    if a.cmd not in ("hook-stop", "session-brief", "ui"):
        health.log_event(a.cmd, vars(a), True, int((time.time() - t0) * 1000), t0)


if __name__ == "__main__":
    main()
