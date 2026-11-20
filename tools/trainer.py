"""
trainer.py — modo "jugada a jugada".

En cada posición junta tres fuentes y propone UNA jugada:
  1. Tus partidas (qué jugaste acá y cómo te fue)
  2. Explorador de Lichess (qué juega la gente de tu nivel, y los maestros). Requiere LICHESS_TOKEN (gratis).
  3. Stockfish (top 3)
El rival responde según lo que más se juega en tu nivel: un sparring realista, no un motor perfecto.

⚠️ Fair play: SOLO para práctica (contra el coach, tablero de análisis o bots).
   Nunca durante una partida en vivo en Chess.com / Lichess.
"""
from __future__ import annotations

import json
import os
import random
import time
import uuid as uuidlib
from datetime import datetime

import core as C

EXPLORER = "https://explorer.lichess.ovh"
BANDS = [0, 1000, 1200, 1400, 1600, 1800, 2000, 2200, 2500]
CACHE_TTL = 30 * 86400
BOOK_MIN_GAMES = 20  # menos partidas que esto en tu nivel = fuera de libro
PRACTICAL_MIN_GAMES = 30  # mínimo de partidas para confiar en el score de una jugada
CANDIDATE_WINDOW = 5.0  # % de prob. de ganar que aceptamos perder vs la mejor de Stockfish por una jugada más práctica


# ───────────────────────── fuentes ─────────────────────────

def my_rating(con) -> tuple[int | None, str]:
    """Rating para elegir la banda del explorador (prefiere Lichess, porque el explorador es de Lichess)."""
    for src in ("lichess", "chesscom", "pgn"):
        r = con.execute("SELECT my_rating FROM games WHERE source=? AND my_rating IS NOT NULL AND time_class IN ('blitz','rapid') "
                        "ORDER BY end_time DESC LIMIT 1", (src,)).fetchone()
        if r:
            return r[0], src
    return None, "-"


def rating_band(r: int | None) -> str:
    if not r:
        return "1400,1600,1800"
    floor = max(b for b in BANDS if b <= r)
    i = BANDS.index(floor)
    return ",".join(str(b) for b in BANDS[i:i + 2])


def explorer(con, cfg, fen: str, db: str, band: str | None = None) -> dict | None:
    """Consulta el explorador de Lichess con caché en SQLite (30 días). db ∈ lichess|masters."""
    key = f"{db}|{band}|{C.fen_key(fen)}"
    row = con.execute("SELECT json, fetched_at FROM explorer_cache WHERE key=?", (key,)).fetchone()
    if row and time.time() - row["fetched_at"] < CACHE_TTL:
        return json.loads(row["json"])
    if not os.environ.get("LICHESS_TOKEN"):
        return None
    params = {"fen": fen, "moves": 12, "topGames": 0, "recentGames": 0}
    if db == "lichess":
        params.update(ratings=band, speeds="blitz,rapid,classical")
    try:
        r = C.http_get(f"{EXPLORER}/{db}", cfg, params=params, timeout=10)
        if r.status_code != 200:
            C.warn(f"explorador {db}: HTTP {r.status_code}")
            return None
        data = r.json()
    except Exception as e:
        C.warn(f"explorador {db}: {e.__class__.__name__}")
        return None
    con.execute("INSERT OR REPLACE INTO explorer_cache VALUES (?,?,?)", (key, json.dumps(data), int(time.time())))
    con.commit()
    return data


def move_score(m: dict, white_to_move: bool) -> tuple[int, float]:
    n = m["white"] + m["draws"] + m["black"]
    if not n:
        return 0, 0.0
    w = m["white"] if white_to_move else m["black"]
    return n, 100 * (w + 0.5 * m["draws"]) / n


def own_stats(con, fen: str) -> dict:
    """Qué jugaste tú (y tus rivales) en esta posición en tus partidas reales."""
    k = C.fen_key(fen)
    rows = con.execute(
        "SELECT m.san, m.is_me, m.win_drop, m.class, g.my_result FROM moves m JOIN games g ON g.uuid=m.game_uuid "
        "WHERE m.fen_key=?", (k,)).fetchall()
    me, opp = {}, {}
    for r in rows:
        d = me if r["is_me"] else opp
        e = d.setdefault(r["san"], {"n": 0, "pts": 0.0, "drop": 0.0})
        e["n"] += 1
        e["pts"] += {"win": 1, "draw": 0.5}.get(r["my_result"], 0)
        e["drop"] += r["win_drop"] or 0
    for d in (me, opp):
        for e in d.values():
            e["score"] = 100 * e["pts"] / e["n"]
            e["drop"] = e["drop"] / e["n"]
    return {"me": me, "opp": opp}


def engine_top(cfg, board, multipv=3, seconds=0.5):
    import chess.engine

    if board.is_game_over():
        return []
    eng = C.get_engine(cfg)
    try:
        infos = eng.analyse(board, chess.engine.Limit(time=seconds), multipv=multipv)
    finally:
        eng.quit()
    out = []
    for info in infos if isinstance(infos, list) else [infos]:
        pv = info.get("pv") or []
        if not pv:
            continue
        cp = info["score"].pov(board.turn).score(mate_score=C.MATE)
        line, tmp = [], board.copy()
        for mv in pv[:5]:
            line.append(tmp.san(mv))
            tmp.push(mv)
        out.append({"san": board.san(pv[0]), "uci": pv[0].uci(), "cp": cp, "win": C.win_pct(cp), "line": " ".join(line)})
    return out


# ───────────────────────── recomendación ─────────────────────────

def recommend(board, eng, lich, own) -> dict:
    """Regla determinista: candidatas = top de Stockfish a ≤5% de la mejor; gana la de mejor score
    práctico en tu nivel (≥30 partidas). Sin datos del explorador, la mejor de Stockfish."""
    white = board.turn
    lmoves = {m["san"]: m for m in (lich or {}).get("moves", [])}
    if eng:
        best_w = eng[0]["win"]
        cands = [e for e in eng if e["win"] >= best_w - CANDIDATE_WINDOW]
        pick, why = cands[0], "la mejor de Stockfish"
        best_prac = None
        for e in cands:
            m = lmoves.get(e["san"])
            if m:
                n, sc = move_score(m, white)
                if n >= PRACTICAL_MIN_GAMES and (best_prac is None or sc > best_prac):
                    best_prac, pick = sc, e
        if best_prac is not None:
            why = (f"casi igual que la mejor de Stockfish y la que más gana en tu nivel ({best_prac:.0f}%)"
                   if pick is not eng[0] else f"la mejor de Stockfish y gana {best_prac:.0f}% en tu nivel")
        rec = {"san": pick["san"], "why": why, "line": pick["line"]}
    elif lmoves:
        ranked = sorted(lmoves.values(), key=lambda m: -(m["white"] + m["draws"] + m["black"]))
        top = [m for m in ranked[:4] if move_score(m, white)[0] >= PRACTICAL_MIN_GAMES] or ranked[:1]
        m = max(top, key=lambda m: move_score(m, white)[1])
        rec = {"san": m["san"], "why": f"la que más gana en tu nivel ({move_score(m, white)[1]:.0f}%)", "line": ""}
    else:
        return {}
    habit = max(own["me"].items(), key=lambda kv: kv[1]["n"]) if own["me"] else None
    if habit and habit[0] != rec["san"] and (habit[1]["drop"] >= 5 or habit[1]["score"] < 45):
        rec["habit_warning"] = (f"Acá sueles jugar {habit[0]} ({habit[1]['n']}×, score {habit[1]['score']:.0f}%, "
                                f"te cuesta {habit[1]['drop']:.0f}% en promedio)")
    return rec


def position_report(con, cfg, fen: str, use_explorer=True, use_engine=True, seconds=0.5) -> dict:
    import chess

    board = chess.Board(fen)
    r, src = my_rating(con)
    band = rating_band(r)
    lich = explorer(con, cfg, fen, "lichess", band) if use_explorer else None
    mast = explorer(con, cfg, fen, "masters") if use_explorer else None
    own = own_stats(con, fen)
    eng = engine_top(cfg, board, 3, seconds) if use_engine else []
    total = sum(m["white"] + m["draws"] + m["black"] for m in (lich or {}).get("moves", []))
    rec = recommend(board, eng, lich, own)
    return {"fen": fen, "board": board, "band": band, "rating": r, "rating_src": src, "lich": lich, "mast": mast,
            "own": own, "eng": eng, "rec": rec, "book": (total >= BOOK_MIN_GAMES) if lich else None,
            "explorer_on": bool(os.environ.get("LICHESS_TOKEN"))}


def print_report(rep: dict):
    b = rep["board"]
    white = b.turn
    op = ((rep["lich"] or {}).get("opening") or (rep["mast"] or {}).get("opening") or {})
    print(f"## Posición — juegan {'blancas' if white else 'negras'}" + (f" — {op.get('eco', '')} {op.get('name', '')}" if op else ""))
    if not rep["explorer_on"]:
        print("(explorador Lichess apagado: falta LICHESS_TOKEN en .env → solo tus partidas + Stockfish)")
    rows = {}
    for san, e in rep["own"]["me"].items():
        rows.setdefault(san, {})["own"] = f"{e['n']}× {e['score']:.0f}% (−{e['drop']:.0f})"
    lm = (rep["lich"] or {}).get("moves", [])
    ltot = sum(m["white"] + m["draws"] + m["black"] for m in lm) or 1
    for m in lm[:6]:
        n, sc = move_score(m, white)
        rows.setdefault(m["san"], {})["lich"] = f"{100 * n / ltot:.0f}% · {sc:.0f}%"
    mm = (rep["mast"] or {}).get("moves", [])
    mtot = sum(m["white"] + m["draws"] + m["black"] for m in mm) or 1
    for m in mm[:5]:
        n, _ = move_score(m, white)
        rows.setdefault(m["san"], {})["mast"] = f"{100 * n / mtot:.0f}%"
    for e in rep["eng"]:
        rows.setdefault(e["san"], {})["eng"] = C.fmt_eval(e["cp"])
    band = rep["band"].replace(",", "/")
    print(f"| jugada | tú | Lichess {band} | maestros | motor |")
    print("|---|---|---|---|---|")
    order = [e["san"] for e in rep["eng"]] + [m["san"] for m in lm] + list(rep["own"]["me"])
    for san in dict.fromkeys(order):
        r = rows.get(san, {})
        print(f"| {san} | {r.get('own', '-')} | {r.get('lich', '-')} | {r.get('mast', '-')} | {r.get('eng', '-')} |")
    print("tú = veces · score · caída media · Lichess = % que la juega · score · motor = eval de Stockfish (tu POV)")
    if rep["own"]["opp"]:
        print("Tus rivales acá jugaron: " + ", ".join(f"{s} {e['n']}×" for s, e in sorted(rep["own"]["opp"].items(), key=lambda kv: -kv[1]["n"])))
    if rep["book"] is not None:
        print("Libro: " + ("sí (posición conocida en tu nivel)" if rep["book"] else "FUERA DE LIBRO → guía por principios + Stockfish"))
    rec = rep["rec"]
    if rec:
        print(f"\n▶ RECOMENDACIÓN: {rec['san']} — {rec['why']}" + (f" | línea: {rec['line']}" if rec.get("line") else ""))
        if rec.get("habit_warning"):
            print(f"⚠️ {rec['habit_warning']}")


def cmd_move_stats(a):
    cfg = C.load_cfg()
    con = C.db()
    print_report(position_report(con, cfg, a.fen, not a.no_explorer, not a.no_engine, a.time))


# ───────────────────────── sesión de entrenamiento ─────────────────────────

def _active_path():
    return C.TRAIN_DIR / "active.json"


def load_session(required=True) -> dict | None:
    p = _active_path()
    if not p.exists():
        if required:
            C.die("No hay sesión activa. Empieza con: ./coach train start --color white [--opening Catalana]")
        return None
    return json.loads(p.read_text())


def save_session(t: dict):
    C.TRAIN_DIR.mkdir(parents=True, exist_ok=True)
    data = json.dumps(t, indent=2, ensure_ascii=False)
    (C.TRAIN_DIR / f"{t['id']}.json").write_text(data)
    _active_path().write_text(data)


def board_of(t):
    import chess

    b = chess.Board(t["start_fen"])
    for m in t["moves"]:
        b.push_uci(m["uci"])
    return b


def my_turn(t, board) -> bool:
    import chess

    return (board.turn == chess.WHITE) == (t["color"] == "white")


def parse_move(board, s: str):
    import chess

    try:
        return board.parse_san(s)
    except ValueError:
        try:
            mv = chess.Move.from_uci(s)
            if mv in board.legal_moves:
                return mv
        except ValueError:
            pass
    C.die(f"Jugada ilegal o no reconocida en esta posición: {s}")


def cmd_train(a):
    globals()[f"train_{a.action}"](a)


def train_start(a):
    import chess

    old = load_session(required=False)
    if old and not a.force:
        C.die(f"Ya hay una sesión activa ({old['id']}). Ciérrala con ./coach train end o usa --force.")
    b = chess.Board()
    pre = []
    if a.moves:
        for tok in a.moves.replace(".", ". ").split():
            if tok.rstrip(".").isdigit() or tok.endswith("."):
                continue
            mv = parse_move(b, tok)
            pre.append({"ply": len(pre) + 1, "san": b.san(mv), "uci": mv.uci(), "by": "pre"})
            b.push(mv)
    t = {"id": datetime.now().strftime("%Y%m%d-%H%M") + "-" + uuidlib.uuid4().hex[:4], "started": C.now_iso(),
         "color": a.color, "mode": a.mode, "opening": a.opening, "start_fen": chess.STARTING_FEN,
         "moves": pre, "suggestion": None, "out_of_book_ply": None}
    save_session(t)
    print("⚠️ Fair play: esto es SOLO para práctica. Nunca lo uses durante una partida en vivo.")
    print(f"Sesión {t['id']} · juegas {'blancas' if a.color == 'white' else 'negras'} · modo {a.mode}"
          + (f" · apertura objetivo: {a.opening}" if a.opening else ""))
    train_status(a)


def train_status(a):
    t = load_session()
    b = board_of(t)
    import chess

    orient = chess.WHITE if t["color"] == "white" else chess.BLACK
    print(b.unicode(borders=True, empty_square="·", orientation=orient))
    print(f"Jugadas: {pgn_moves(t) or '(inicio)'}")
    if b.is_game_over():
        print(f"Partida terminada: {b.result()}")
    else:
        print(f"Turno: {'TUYO' if my_turn(t, b) else 'del RIVAL (./coach train rival)'} · FEN: {b.fen()}")
    if t.get("suggestion") and t["suggestion"].get("ply") == len(t["moves"]) + 1:
        print(f"Sugerencia vigente: {t['suggestion']['san']}")


def train_suggest(a):
    t = load_session()
    b = board_of(t)
    if not my_turn(t, b):
        C.die("Es turno del rival. Usa ./coach train rival")
    cfg = C.load_cfg()
    con = C.db()
    rep = position_report(con, cfg, b.fen(), not a.no_explorer, True, a.time)
    print_report(rep)
    if rep["rec"]:
        t["suggestion"] = {"ply": len(t["moves"]) + 1, "san": rep["rec"]["san"], "why": rep["rec"]["why"],
                           "line": rep["rec"].get("line", ""), "habit": rep["rec"].get("habit_warning"), "book": rep["book"]}
    if rep["book"] is False and t["out_of_book_ply"] is None:
        t["out_of_book_ply"] = len(t["moves"]) + 1
    save_session(t)


def train_play(a):
    """Registra TU jugada y la evalúa contra la mejor."""
    import chess.engine

    t = load_session()
    b = board_of(t)
    if not my_turn(t, b):
        C.die("Es turno del rival. Usa ./coach train rival (o --move X para elegir su jugada)")
    mv = parse_move(b, a.move)
    cfg = C.load_cfg()
    eng = C.get_engine(cfg)
    try:
        lim = chess.engine.Limit(time=a.time)
        cp0, best, _ = C.pos_eval(eng, b, lim)
        b2 = b.copy()
        b2.push(mv)
        cp1, _, _ = C.pos_eval(eng, b2, lim)
    finally:
        eng.quit()
    sign = 1 if b.turn == chess.WHITE else -1
    drop = max(0.0, C.win_pct(sign * cp0) - C.win_pct(sign * cp1))
    cls = C.classify(drop, best is not None and mv == best)
    sug = t.get("suggestion") if (t.get("suggestion") or {}).get("ply") == len(t["moves"]) + 1 else None
    entry = {"ply": len(t["moves"]) + 1, "san": b.san(mv), "uci": mv.uci(), "by": "me", "fen_before": b.fen(),
             "drop": round(drop, 1), "class": cls, "best": b.san(best) if best else None,
             "suggested": sug["san"] if sug else None, "eval_after": cp1}
    t["moves"].append(entry)
    t["suggestion"] = None
    save_session(t)
    verdict = {"best": "✅ la mejor", "good": "✅ buena", "inaccuracy": "🟡 imprecisión",
               "mistake": "🟠 error", "blunder": "🔴 error grave"}[cls]
    print(f"Jugaste {entry['san']}: {verdict} · caída {drop:.1f}% · eval {C.fmt_eval(cp1, sign == 1)} (tu POV)")
    if cls not in ("best", "good"):
        print(f"Mejor era: {entry['best']}")
    if sug and sug["san"] != entry["san"]:
        print(f"(La sugerencia era {sug['san']})")
    if b2.is_game_over():
        print(f"Fin: {b2.result()}")
    elif t["mode"] == "guia" and drop >= 10:
        print("→ MODO GUÍA: intervén ahora (la jugada costó ≥10%). Explica el porqué antes de seguir.")


def train_rival(a):
    """Jugada del rival: muestreada de lo que juega tu nivel (explorador), o de tus rivales reales, o de Stockfish."""
    t = load_session()
    b = board_of(t)
    if my_turn(t, b):
        C.die("Es tu turno. Pide sugerencia con ./coach train suggest o juega con ./coach train play <jugada>")
    if a.move:
        mv, src = parse_move(b, a.move), "elegida por el alumno"
    else:
        cfg = C.load_cfg()
        con = C.db()
        rng = random.Random(a.seed)
        r, _ = my_rating(con)
        band = rating_band(r)
        lich = explorer(con, cfg, b.fen(), "lichess", band)
        moves = (lich or {}).get("moves", [])
        total = sum(m["white"] + m["draws"] + m["black"] for m in moves)
        mv = src = None
        explorer_off = lich is None and not os.environ.get("LICHESS_TOKEN")
        if total >= BOOK_MIN_GAMES:
            weights = [m["white"] + m["draws"] + m["black"] for m in moves]
            m = rng.choices(moves, weights=weights)[0]
            mv = parse_move(b, m["uci"])
            src = f"Lichess {band.replace(',', '/')}: {100 * weights[moves.index(m)] / total:.0f}% de las partidas"
        else:
            if t["out_of_book_ply"] is None and not explorer_off:
                t["out_of_book_ply"] = len(t["moves"]) + 1
            own = own_stats(con, b.fen())["opp"]
            if sum(e["n"] for e in own.values()) >= 3:
                sans = list(own)
                m = rng.choices(sans, weights=[own[s]["n"] for s in sans])[0]
                mv, src = parse_move(b, m), "lo que te jugaron tus rivales reales acá"
            else:
                eng = engine_top(cfg, b, 3, a.time)
                if not eng:
                    C.die("No hay jugadas (partida terminada).")
                # rival de club: no siempre la mejor; pesos 60/25/15
                w = [60, 25, 15][: len(eng)]
                e = rng.choices(eng, weights=w)[0]
                mv, src = parse_move(b, e["uci"]), "Stockfish top-3 " + ("(explorador apagado)" if explorer_off else "(fuera de libro)")
    entry = {"ply": len(t["moves"]) + 1, "san": b.san(mv), "uci": mv.uci(), "by": "rival", "src": src}
    t["moves"].append(entry)
    save_session(t)
    print(f"Rival juega {entry['san']}  ({src})")
    b.push(mv)
    if b.is_game_over():
        print(f"Fin: {b.result()}")


def pgn_moves(t) -> str:
    import chess

    b = chess.Board(t["start_fen"])
    out = []
    for m in t["moves"]:
        if b.turn == chess.WHITE:
            out.append(f"{b.fullmove_number}.")
        elif not out:
            out.append(f"{b.fullmove_number}...")
        out.append(m["san"])
        b.push_uci(m["uci"])
    return " ".join(out)


def train_end(a):
    t = load_session()
    mine = [m for m in t["moves"] if m["by"] == "me"]
    avg = sum(m["drop"] for m in mine) / len(mine) if mine else 0
    bad = [m for m in mine if m["class"] in ("mistake", "blunder", "inaccuracy")]
    dev = [m for m in mine if m.get("suggested") and m["suggested"] != m["san"]]
    follow = [m for m in mine if m.get("suggested")]
    day = datetime.now().strftime("%Y-%m-%d")
    C.ENTREGABLES.mkdir(exist_ok=True)
    out = C.ENTREGABLES / f"{day}-jugada-a-jugada-{t['id']}.md"
    lines = [f"# Jugada a jugada · {day}", "",
             f"- Color: {t['color']} · modo: {t['mode']} · apertura objetivo: {t.get('opening') or '-'}",
             f"- Jugadas: `{pgn_moves(t)}`",
             f"- Tus jugadas: {len(mine)} · caída media {avg:.1f}% · imprecisiones/errores: {len(bad)}",
             f"- Seguiste la sugerencia: {len(follow) - len(dev)}/{len(follow)}",
             f"- Salida de libro en la media jugada: {t.get('out_of_book_ply') or 'no salió'}", ""]
    if bad:
        lines.append("## Dónde perdiste precisión")
        for m in bad:
            lines.append(f"- {m['san']} [{m['class']}, −{m['drop']}%] → mejor {m['best']} · FEN `{m['fen_before']}`")
    if dev:
        lines.append("\n## Te desviaste de la sugerencia")
        for m in dev:
            lines.append(f"- jugaste {m['san']} en vez de {m['suggested']} ({m['class']}) · FEN `{m['fen_before']}`")
    out.write_text("\n".join(lines) + "\n")
    C.ESTADO.mkdir(exist_ok=True)
    with (C.ESTADO / "bitacora.md").open("a") as f:
        f.write(f"- {day}: jugada a jugada ({t.get('opening') or t['color']}), {len(mine)} jugadas, caída media {avg:.1f}%, "
                f"{len(bad)} con error → {out.name}\n")
    _active_path().unlink(missing_ok=True)
    print("\n".join(lines))
    print(f"\nGuardado en {out.relative_to(C.ROOT)} y en la bitácora.")
