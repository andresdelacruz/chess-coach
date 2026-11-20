"""Tests del Chess Coach. Correr: .venv/bin/pytest -q   (los de motor se saltan si no hay Stockfish)."""
import io
import json
import time
from argparse import Namespace
from contextlib import redirect_stdout

import pytest

import core
import health
import trainer
from conftest import FIX, needs_engine


def run(fn, **kw):
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(Namespace(**kw))
    return buf.getvalue()


def games(con):
    return {r["uuid"]: r for r in con.execute("SELECT * FROM games")}


# ───────── ingesta ─────────

def test_parse_chesscom():
    cc = json.loads((FIX / "chesscom_archive.json").read_text())["games"]
    g = core.parse_chesscom(cc[0], "tester")
    assert (g["my_color"], g["my_result"], g["result_detail"]) == ("white", "loss", "checkmated")
    assert g["opening"] == "Italian Game Blackburne Shilling Gambit"
    assert g["my_accuracy"] == 40.1 and g["opp_accuracy"] == 90.2
    assert core.parse_chesscom(cc[1], "tester") is None  # chess960 fuera
    d = core.parse_chesscom(cc[2], "tester")
    assert (d["my_color"], d["my_result"]) == ("black", "draw")


def test_parse_lichess():
    rows = [json.loads(x) for x in (FIX / "lichess_games.ndjson").read_text().splitlines()]
    g = core.parse_lichess(rows[0], "tester")
    assert (g["my_color"], g["my_result"], g["result_detail"], g["time_control"]) == ("black", "win", "resigned", "600+5")
    assert core.parse_lichess(rows[1], "tester") is None  # abortada
    d = core.parse_lichess(rows[2], "tester")
    assert (d["my_result"], d["time_class"]) == ("draw", "bullet")  # ultraBullet → bullet
    assert core.opening_family(d["opening"]) == "Catalan Opening"
    ai = core.parse_lichess(rows[3], "tester")
    assert ai["opp_name"] == "IA" and ai["my_result"] == "win"


def test_sync_idempotente_no_duplica(proj, fake_api):
    """Aceptación: tras reiniciar / repetir, no duplica partidas."""
    out1 = core.do_sync(quiet=True)
    con = core.db()
    n1 = con.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    assert out1 == {"chesscom": 2, "lichess": 3} and n1 == 5
    out2 = core.do_sync(quiet=True)
    assert out2 == {"chesscom": 0, "lichess": 0}
    assert con.execute("SELECT COUNT(*) FROM games").fetchone()[0] == n1
    # Chess.com: el 2º pedido del mes usa ETag (→ 304); Lichess usa since
    etags = [c[1] for c in fake_api if c[0].endswith("2025/11")]
    assert etags == [None, "E1"]
    assert any("since" in c[2] for c in fake_api if "lichess" in c[0])


def test_sin_red_no_se_cae(proj, monkeypatch):
    """Aceptación: sin acceso entrega lo que puede y nombra la brecha."""
    def boom(*a, **k):
        raise ConnectionError("sin red")

    monkeypatch.setattr(core, "http_get", boom)
    assert core.do_sync(quiet=True) == {"chesscom": None, "lichess": None}
    monkeypatch.setattr("sys.stdin", io.StringIO('{"session_id": "t"}'))
    out = run(core.cmd_session_brief)
    assert "No se pudo sincronizar" in out and "ESTILO DEL COACH" in out


def test_import_pgn(proj):
    out = run(core.cmd_import_pgn, file=str(FIX / "otb.pgn"), as_name=["Tester Real"], time_class=None)
    assert "Importadas 1" in out
    g = list(games(core.db()).values())[0]
    assert (g["my_color"], g["my_result"], g["eco"], g["time_class"]) == ("black", "draw", "E46", "rapid")


# ───────── repertorio y decisiones ─────────

def test_repertorio_y_decisiones(proj, fake_api):
    """Aceptación: recupera una decisión anterior (repertorio) sin volver a preguntarla."""
    core.do_sync(quiet=True)
    run(core.cmd_import_pgn, file=str(FIX / "otb.pgn"), as_name=["Tester Real"], time_class=None)
    run(core.cmd_repertoire, action="set", key="white", value=["Catalana"], why="la vi en un curso",
        days=None, time_class=None, source=None, color=None)
    run(core.cmd_repertoire, action="set", key="vs_d4", value=["Nimzo-India"], why=None,
        days=None, time_class=None, source=None, color=None)
    dec = (proj / "estado" / "decisiones.md").read_text()
    assert "Catalana" in dec and "la vi en un curso" in dec and "decidió: alumno" in dec
    out = run(core.cmd_repertoire, action="show", key=None, value=[], why=None, days=None, time_class=None, source=None, color=None)
    assert "Con blancas: Catalana" in out and "En repertorio: 1/" in out      # Catalan por nombre (Lichess)
    assert "Con negras vs 1.d4: Nimzo-India" in out                          # Nimzo por ECO E46 (PGN sin nombre)
    vs_d4 = out.split("vs 1.d4")[1]
    assert "En repertorio: 1/2" in vs_d4  # la de Chess.com del fixture no trae ECO/nombre → cuenta como "?"
    monkeypatch_stdin = io.StringIO("{}")
    import sys
    old = sys.stdin
    sys.stdin = monkeypatch_stdin
    try:
        brief = run(core.cmd_session_brief)
    finally:
        sys.stdin = old
    assert "Repertorio declarado: Con blancas: Catalana" in brief


def test_style_set_registra(proj):
    run(core.cmd_style, action="set", name="grinder", why="quiero mejorar finales")
    assert json.loads(core.CFG_PATH.read_text())["coach_style"] == "grinder"
    assert "Estilo de coach: grinder (antes: profe)" in (proj / "estado" / "decisiones.md").read_text()
    with pytest.raises(SystemExit):
        run(core.cmd_style, action="set", name="magnus-real", why=None)


# ───────── métricas puras ─────────

def test_win_pct_y_clasificacion():
    assert core.win_pct(0) == pytest.approx(50)
    assert core.win_pct(10000) == core.win_pct(1000)  # tope
    assert core.classify(35, False) == "blunder"
    assert core.classify(22, False) == "mistake"
    assert core.classify(12, False) == "inaccuracy"
    assert core.classify(1, True) == "best"


def test_recomendacion_practica_dentro_de_ventana():
    import chess

    b = chess.Board()
    eng = [{"san": "e4", "win": 55.0, "line": "e4"}, {"san": "d4", "win": 53.0, "line": "d4"},
           {"san": "a4", "win": 40.0, "line": "a4"}]
    lich = {"moves": [{"san": "e4", "white": 40, "draws": 10, "black": 50},   # 45%
                      {"san": "d4", "white": 60, "draws": 10, "black": 30},   # 65% ← más práctica
                      {"san": "a4", "white": 900, "draws": 0, "black": 100}]}  # fuera de ventana de motor
    own = {"me": {"a4": {"n": 4, "score": 25.0, "drop": 12.0}}, "opp": {}}
    rec = trainer.recommend(b, eng, lich, own)
    assert rec["san"] == "d4" and "65%" in rec["why"]
    assert "a4" in rec["habit_warning"]


def test_rating_band():
    assert trainer.rating_band(1450) == "1400,1600"
    assert trainer.rating_band(2600) == "2500"
    assert trainer.rating_band(None) == "1400,1600,1800"


# ───────── verificación ─────────

def test_hook_stop(proj, monkeypatch):
    core.DATA.mkdir(exist_ok=True)
    import os
    core.SESSION_PATH.write_text(json.dumps({"session_id": "t", "started": time.time() - 10}))
    bit = proj / "estado" / "bitacora.md"
    os.utime(bit, (time.time() - 60, time.time() - 60))  # bitácora vieja
    health.log_event("game", {"ref": "last"}, True, 10, time.time() - 1)

    def hook(payload):
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        return run(health.cmd_hook_stop)

    out = hook({"stop_hook_active": False})
    assert json.loads(out)["decision"] == "block"
    assert hook({"stop_hook_active": True}) == ""  # no genera loops
    with (proj / "estado" / "bitacora.md").open("a") as f:
        f.write("- hoy: revisión\n")
    assert hook({}) == ""


def test_hook_stop_sin_coaching_no_bloquea(proj, monkeypatch):
    core.DATA.mkdir(exist_ok=True)
    core.SESSION_PATH.write_text(json.dumps({"started": time.time() - 10}))
    health.log_event("stats", {}, True, 10, time.time())
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    assert run(health.cmd_hook_stop) == ""


def test_anomalias_detecta_y_no_duplica(proj, fake_api):
    """Aceptación: detecta una contradicción y deja registro rastreable (una sola vez)."""
    core.do_sync(quiet=True)
    run(core.cmd_import_pgn, file=str(FIX / "otb.pgn"), as_name=["Tester Real"], time_class=None)
    con = core.db()
    con.execute("UPDATE games SET my_accuracy=150 WHERE uuid='chesscom:cc-1'")
    g = dict(con.execute("SELECT * FROM games WHERE source='pgn'").fetchone())
    g.update(uuid="lichess:dup", source="lichess")  # misma partida (20 jugadas) en dos fuentes
    con.execute(f"INSERT INTO games({','.join(g)}) VALUES ({','.join('?' * len(g))})", list(g.values()))
    con.commit()
    out = run(health.cmd_anomalies)
    md = (proj / "estado" / "anomalias.md").read_text()
    assert "ANO-001" in md and "Precisión fuera de 0–100" in md and "La misma partida en dos fuentes" in md
    assert "Estado: pendiente" in md
    out2 = run(health.cmd_anomalies)
    assert "0 nuevas" in out2 and md == (proj / "estado" / "anomalias.md").read_text()
    assert "2 nuevas" in out or "nuevas" in out


def test_eventos_sin_secretos(proj):
    health.log_event("sync", {"full": True, "f": print, "token": None}, True, 5, time.time())
    ev = json.loads(core.EVENTS_PATH.read_text().splitlines()[-1])
    assert ev["cmd"] == "sync" and "f" not in ev["args"] and "token" not in ev["args"]


# ───────── con Stockfish ─────────

@needs_engine
def test_analisis_detecta_el_error_grave(proj, fake_api):
    core.do_sync(quiet=True)
    core.do_analyze(quiet=True)
    con = core.db()
    m = con.execute("SELECT * FROM moves WHERE game_uuid='chesscom:cc-1' AND san='Nxf7'").fetchone()
    assert m["is_me"] == 1 and m["class"] in ("blunder", "mistake") and m["time_trouble"] == 1
    assert m["fen_key"] and m["fen_key"].count(" ") == 3
    out = run(core.cmd_game, ref="chesscom:cc-1", top=5, min_drop=10, pgn=False)
    assert "Nxf7" in out and "APURO DE RELOJ" in out
    assert len(health.detect(con)) == 0  # datos sanos → sin anomalías


@needs_engine
def test_jugada_a_jugada_flujo_y_continuidad(proj, fake_api):
    """Aceptación: tras reiniciar retoma la sesión; no permite jugar fuera de turno; deja entregable + bitácora."""
    core.do_sync(quiet=True)
    core.do_analyze(quiet=True)
    base = dict(color="white", opening="Italiana", moves="1.e4 e5 2.Nf3 Nc6 3.Bc4 Nd4", mode="recomendar",
                seed=1, time=0.1, no_explorer=True, force=False, move=None)
    run(trainer.cmd_train, action="start", **base)
    out = run(trainer.cmd_train, action="suggest", **base)
    assert "RECOMENDACIÓN" in out and "Nxe5" in out  # aparece tu jugada real en esa posición
    # "reinicio": la sesión vive en disco
    t = trainer.load_session()
    assert len(t["moves"]) == 6 and t["suggestion"]["ply"] == 7
    run(trainer.cmd_train, action="play", **{**base, "move": "Nxe5"})
    with pytest.raises(SystemExit):
        run(trainer.cmd_train, action="play", **{**base, "move": "d3"})  # turno del rival
    out = run(trainer.cmd_train, action="rival", **base)
    assert "Rival juega" in out
    out = run(trainer.cmd_train, action="end", **base)
    assert "Te desviaste de la sugerencia" in out or "Seguiste la sugerencia: 1/1" in out
    assert list((proj / "entregables").glob("*jugada-a-jugada*.md"))
    assert "jugada a jugada (Italiana)" in (proj / "estado" / "bitacora.md").read_text()
    assert trainer.load_session(required=False) is None


@needs_engine
def test_check_jugada_ilegal(proj):
    with pytest.raises(SystemExit):
        run(core.cmd_check, fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", move="Ke2", time=0.1)


# ───────── UI web ─────────

def test_ui_contexto_y_feedback(proj, fake_api):
    import webui

    core.do_sync(quiet=True)
    ctx = webui.context()
    assert ctx["accounts"]["chesscom"] == "tester" and len(ctx["skills"]) >= 5 and ctx["styles"]
    assert any(c["name"] == "Chess.com" and c["ok"] for c in ctx["connections"])
    c = webui.new_conv()
    c["messages"] += [{"role": "user", "text": "revisa mi última"}, {"role": "assistant", "text": "ok"}]
    webui.save_conv(c)
    h = webui.H.__new__(webui.H)
    h._feedback({"conv": c["id"], "index": 1, "value": "down", "note": "muy largo"})
    fb = (proj / "estado" / "feedback.md").read_text()
    assert "hay que corregir" in fb and "muy largo" in fb and "revisa mi última" in fb
    with pytest.raises(ValueError):
        webui.conv_path("../../etc/passwd")


def test_onboarding_desde_cero(proj, monkeypatch):
    """Aceptación v1: sin config la UI pide configuración; al guardar queda lista y registra la decisión."""
    import webui

    core.CFG_PATH.unlink()
    import shutil as _sh
    _sh.copy(FIX.parent.parent / "config.example.json", core.ROOT / "config.example.json")
    _sh.copytree(FIX.parent.parent / "templates", core.ROOT / "templates")
    assert webui.context() == {"needs_setup": True, "setup": webui.setup_state()}
    monkeypatch.setattr(webui, "verify_user", lambda src, user: "El usuario 'nadie' no existe en Chess.com." if user == "nadie" else None)
    monkeypatch.setattr(webui.WORKER, "kick", lambda: None)
    assert webui.save_setup({"chesscom": "", "lichess": ""})["ok"] is False
    assert "no existe" in webui.save_setup({"chesscom": "nadie"})["error"]
    r = webui.save_setup({"chesscom": "Tester", "style": "grinder", "repertoire": {"white": "Catalana, Londres"},
                          "lichess_token": "lip_abc"})
    assert r == {"ok": True, "first": True}
    cfg = json.loads(core.CFG_PATH.read_text())
    assert cfg["accounts"] == {"chesscom": "Tester"} and cfg["coach_style"] == "grinder"
    assert cfg["repertoire"]["white"] == ["Catalana", "Londres"] and "lichess_token" not in cfg
    assert "LICHESS_TOKEN=lip_abc" in core.ENV_PATH.read_text()
    assert "Configuración inicial" in (core.ESTADO / "decisiones.md").read_text()
    assert webui.has_config() and webui.setup_state()["token_set"]
    r2 = webui.save_setup({"chesscom": "Tester", "style": "profe", "repertoire": {"white": "Catalana, Londres"}})
    assert r2["first"] is False and "Estilo de coach: profe (antes: grinder)" in (core.ESTADO / "decisiones.md").read_text()


def test_arbol_de_aperturas(proj, fake_api):
    """Árbol estilo OpeningTree: índice de TODAS las partidas (sin motor) y stats por posición."""
    import tree

    core.do_sync(quiet=True)  # el sync indexa
    con = core.db()
    assert con.execute("SELECT COUNT(*) FROM games WHERE COALESCE(indexed,0)=0").fetchone()[0] == 0
    root = tree.tree_report([], "white", engine=False, explorer=False)
    assert root["reached"] == 3 and {r["san"] for r in root["rows"]} == {"e4", "d4"}
    e4 = next(r for r in root["rows"] if r["san"] == "e4")
    assert e4["n"] == 2 and e4["w"] + e4["d"] + e4["l"] == 2 and e4["drop"] is None  # sin análisis aún
    after = tree.tree_report(["e2e4"], "white", engine=False, explorer=False)
    assert after["my_turn"] is False and after["pgn"] == "1. e4" and after["rows"]
    black = tree.tree_report([], "black", engine=False, explorer=False)
    assert black["reached"] == 2
    assert "árbol de aperturas" in tree.summary_for_chat(after)
    with pytest.raises(ValueError):
        tree.tree_report(["e2e5"], "white", engine=False, explorer=False)
    assert core.index_games(con) == 0  # idempotente
