"""
tree.py — árbol de aperturas de TUS partidas (estilo OpeningTree) + Stockfish.

Para cada posición: qué se jugó en tus partidas, cuántas veces, % ganadas/tablas/perdidas
y, donde hay análisis, cuánto te costó en promedio (caída de prob. de ganar según Stockfish).
"""
from __future__ import annotations

import time

import core as C
import trainer as T

_EVAL_CACHE: dict[str, tuple[float, dict]] = {}


def _filters(color: str, tc: str | None, days: int | None):
    w, p = ["g.my_color = ?"], [color]
    if tc:
        w.append("g.time_class = ?")
        p.append(tc)
    if days:
        w.append("g.end_time >= ?")
        p.append(int(time.time()) - int(days) * 86400)
    return " AND ".join(w), p


def quick_eval(cfg, board) -> dict:
    """Stockfish rápido (0.3 s) con caché: mejor jugada y eval desde las blancas."""
    k = C.fen_key(board.fen())
    hit = _EVAL_CACHE.get(k)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    try:
        top = T.engine_top(cfg, board, 1, 0.3)
    except SystemExit:
        return {}
    if not top:
        return {}
    e = top[0]
    cp_white = e["cp"] if board.turn else -e["cp"]
    out = {"best": e["san"], "eval": C.fmt_eval(cp_white), "line": e["line"]}
    _EVAL_CACHE[k] = (time.time(), out)
    return out


def tree_report(moves: list[str], color: str = "white", tc: str | None = None, days: int | None = None,
                engine: bool = True, explorer: bool = True) -> dict:
    import chess

    cfg = C.load_cfg()
    con = C.db()
    b = chess.Board()
    path = []
    for u in moves:
        mv = chess.Move.from_uci(u)
        if mv not in b.legal_moves:
            raise ValueError(f"jugada ilegal en el árbol: {u}")
        path.append({"uci": u, "san": b.san(mv)})
        b.push(mv)
    k = C.fen_key(b.fen())
    W, P = _filters(color, tc, days)
    my_turn = (b.turn == chess.WHITE) == (color == "white")

    rows = con.execute(f"""
        SELECT t.san, t.uci, COUNT(*) n,
               SUM(g.my_result='win') w, SUM(g.my_result='draw') d, SUM(g.my_result='loss') l,
               AVG(m.win_drop) avgdrop, COUNT(m.ply) an, MAX(g.date) last
        FROM tree t JOIN games g ON g.uuid = t.game_uuid
        LEFT JOIN moves m ON m.game_uuid = t.game_uuid AND m.ply = t.ply
        WHERE t.fen_key = ? AND {W}
        GROUP BY t.san, t.uci ORDER BY n DESC""", [k] + P).fetchall()
    out_rows = []
    for r in rows:
        n = r["n"]
        score = 100 * (r["w"] + 0.5 * r["d"]) / n if n else 0
        out_rows.append({"san": r["san"], "uci": r["uci"], "n": n, "w": r["w"], "d": r["d"], "l": r["l"],
                         "score": round(score), "drop": round(r["avgdrop"], 1) if r["an"] else None, "analyzed": r["an"],
                         "last": r["last"],
                         "flag": bool(my_turn and n >= 3 and ((r["an"] and r["avgdrop"] >= 10) or score < 40))})
    total = sum(r["n"] for r in out_rows)
    # cuántas partidas llegaron a esta posición (incluye las que terminaron justo acá)
    reached = con.execute(f"SELECT COUNT(DISTINCT t.game_uuid) FROM tree t JOIN games g ON g.uuid=t.game_uuid "
                          f"WHERE t.fen_key=? AND {W}", [k] + P).fetchone()[0] if moves else \
        con.execute(f"SELECT COUNT(*) FROM games g WHERE {W}", P).fetchone()[0]

    games = [dict(r) for r in con.execute(f"""
        SELECT DISTINCT g.uuid, g.date, g.opp_name, g.opp_rating, g.my_result, g.time_class, g.url
        FROM tree t JOIN games g ON g.uuid = t.game_uuid
        WHERE t.fen_key = ? AND {W} ORDER BY g.end_time DESC LIMIT 6""", [k] + P)]
    op = con.execute(f"""
        SELECT COALESCE(g.opening, g.eco) o, COUNT(*) n FROM tree t JOIN games g ON g.uuid=t.game_uuid
        WHERE t.fen_key=? AND {W} GROUP BY o ORDER BY n DESC LIMIT 1""", [k] + P).fetchone()
    best_mine = con.execute("SELECT best_san, COUNT(*) n FROM moves WHERE fen_key=? AND best_san IS NOT NULL "
                            "GROUP BY best_san ORDER BY n DESC LIMIT 1", (k,)).fetchone()

    lich = []
    if explorer:
        r_, _ = T.my_rating(con)
        band = T.rating_band(r_)
        data = T.explorer(con, cfg, b.fen(), "lichess", band)
        tot = sum(m["white"] + m["draws"] + m["black"] for m in (data or {}).get("moves", [])) or 1
        for m in (data or {}).get("moves", [])[:8]:
            n, sc = T.move_score(m, b.turn)
            lich.append({"san": m["san"], "uci": m["uci"], "pct": round(100 * n / tot), "score": round(sc)})
        if data and data.get("opening") and not op:
            op = {"o": data["opening"].get("name")}

    ev = quick_eval(cfg, b) if engine and not b.is_game_over() else {}
    return {
        "fen": b.fen(), "path": path, "pgn": _pgn(path), "turn": "white" if b.turn else "black", "my_turn": my_turn,
        "color": color, "rows": out_rows, "total": total, "reached": reached, "games": games,
        "opening": (op["o"] if op else None), "best_mine": best_mine["best_san"] if best_mine else None,
        "engine": ev, "lichess": lich, "legal": [m.uci() for m in b.legal_moves], "check": b.is_check(),
        "last": path[-1]["uci"] if path else None,
        "unindexed": con.execute("SELECT COUNT(*) FROM games WHERE COALESCE(indexed,0)=0").fetchone()[0],
    }


def _pgn(path) -> str:
    out = []
    for i, p in enumerate(path):
        if i % 2 == 0:
            out.append(f"{i // 2 + 1}.")
        out.append(p["san"])
    return " ".join(out)


def summary_for_chat(rep: dict) -> str:
    """Contexto compacto para el coach (para preguntas tipo «¿por qué me va mal acá?»)."""
    rows = ", ".join(f"{r['san']} {r['n']}× {r['score']}%" + (f" caída {r['drop']}%" if r["drop"] is not None else "")
                     for r in rep["rows"][:5])
    eng = rep.get("engine") or {}
    return (f"[Contexto UI: árbol de aperturas · juega {rep['color']} · {rep.get('opening') or ''} · "
            f"jugadas: {rep['pgn'] or '(inicio)'} · FEN: {rep['fen']} · {rep['reached']} partidas llegaron acá · "
            f"{'turno del alumno' if rep['my_turn'] else 'turno del rival'} · jugadas en sus partidas: {rows or 'ninguna'}"
            + (f" · Stockfish: {eng.get('best')} ({eng.get('eval')})" if eng else "") + "]")
