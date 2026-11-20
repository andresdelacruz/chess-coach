#!/usr/bin/env python3
"""
core.py — motor de datos del Chess Coach (librería; la CLI está en coach.py).

Sincroniza tus partidas de Chess.com y Lichess a SQLite, las analiza con
Stockfish y expone consultas compactas para que Claude Code actúe como
entrenador. Todo local, todo gratis (las APIs de Chess.com/Lichess y
Stockfish no cobran).

Uso rápido:
  ./coach sync              # baja partidas nuevas de todas tus cuentas
  ./coach analyze           # analiza con Stockfish las pendientes
  ./coach game last         # informe de tu última partida
  ./coach stats --days 30   # patrones del último mes
  ./coach help              # todos los comandos
"""
from __future__ import annotations

import fcntl
import io
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DB_PATH = DATA / "games.db"
STATE_PATH = DATA / "state.json"
CFG_PATH = ROOT / "config.json"
KB_DIR = ROOT / "knowledge"
ESTADO = ROOT / "estado"
ENTREGABLES = ROOT / "entregables"
EVENTS_PATH = DATA / "events.jsonl"
SESSION_PATH = DATA / "session.json"
TRAIN_DIR = DATA / "train"
CLI_PATH = ROOT / "tools" / "coach.py"
ENV_PATH = ROOT / ".env"

CHESSCOM_API = "https://api.chess.com/pub"
LICHESS_API = "https://lichess.org/api"
MATE = 10000

# Vocabulario común de "cómo terminó" (desde el punto de vista de quien perdió)
CHESSCOM_DRAWS = {"agreed", "repetition", "stalemate", "insufficient", "50move", "timevsinsufficient"}
LICHESS_STATUS = {
    "mate": "checkmated", "resign": "resigned", "outoftime": "timeout",
    "timeout": "abandoned", "stalemate": "stalemate", "draw": "agreed",
    "cheat": "cheat", "variantEnd": "other", "unknownFinish": "other",
}
LICHESS_SKIP = {"aborted", "noStart", "created", "started"}
SPEED_MAP = {"ultraBullet": "bullet", "correspondence": "daily", "classical": "rapid"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS games(
  uuid TEXT PRIMARY KEY, source TEXT, url TEXT, end_time INTEGER, date TEXT,
  time_class TEXT, time_control TEXT, rated INTEGER,
  my_color TEXT, my_result TEXT, result_detail TEXT,
  my_rating INTEGER, opp_name TEXT, opp_rating INTEGER,
  eco TEXT, opening TEXT, opening_family TEXT,
  my_accuracy REAL, opp_accuracy REAL, pgn TEXT,
  analyzed INTEGER DEFAULT 0, n_plies INTEGER, acpl REAL,
  n_blunders INTEGER, n_mistakes INTEGER, n_inacc INTEGER,
  peak_win REAL, low_win REAL, synced_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_games_end ON games(end_time);
CREATE TABLE IF NOT EXISTS moves(
  game_uuid TEXT, ply INTEGER, move_no INTEGER, color TEXT, is_me INTEGER,
  san TEXT, fen_before TEXT, eval_before INTEGER, eval_after INTEGER,
  best_san TEXT, best_line TEXT, win_before REAL, win_after REAL,
  win_drop REAL, cp_loss INTEGER, class TEXT, phase TEXT,
  clock REAL, time_trouble INTEGER, fen_key TEXT,
  PRIMARY KEY(game_uuid, ply)
);
CREATE INDEX IF NOT EXISTS ix_moves_me ON moves(is_me, class);
CREATE TABLE IF NOT EXISTS sync_state(key TEXT PRIMARY KEY, etag TEXT, value TEXT, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS explorer_cache(key TEXT PRIMARY KEY, json TEXT, fetched_at INTEGER);
CREATE TABLE IF NOT EXISTS tree(game_uuid TEXT, ply INTEGER, fen_key TEXT, san TEXT, uci TEXT, PRIMARY KEY(game_uuid, ply));
CREATE INDEX IF NOT EXISTS ix_tree_fen ON tree(fen_key);
"""


# ───────────────────────── utilidades ─────────────────────────

def die(msg: str, code: int = 1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def warn(msg: str):
    print(f"aviso: {msg}", file=sys.stderr)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_env():
    """Carga .env (secretos como LICHESS_TOKEN) sin pisar variables ya definidas."""
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_cfg() -> dict:
    load_env()
    if not CFG_PATH.exists():
        die("Falta config.json: configura tus cuentas en http://127.0.0.1:4520 (./coach ui)")
    cfg = json.loads(CFG_PATH.read_text())
    acc = cfg.get("accounts", {})
    cfg["accounts"] = {k: (v or "").strip() for k, v in acc.items() if (v or "").strip()}
    if not cfg["accounts"]:
        die("config.json no tiene cuentas. Pon tu usuario de chesscom y/o lichess.")
    cfg.setdefault("analysis", {})
    return cfg


def my_names(cfg) -> set[str]:
    return {v.lower() for v in cfg["accounts"].values()}


def db(readonly: bool = False) -> sqlite3.Connection:
    DATA.mkdir(exist_ok=True)
    if readonly:
        if not DB_PATH.exists():
            die("No hay base de datos todavía. Corre ./coach sync")
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    else:
        con = sqlite3.connect(DB_PATH, timeout=30)
        con.executescript(SCHEMA)
        migrate(con)
    con.row_factory = sqlite3.Row
    return con


def fen_key(fen: str) -> str:
    """Posición sin contadores de jugadas: sirve para agrupar la misma posición entre partidas."""
    return " ".join(fen.split()[:4])


def migrate(con):
    cols = {r[1] for r in con.execute("PRAGMA table_info(moves)")}
    if "fen_key" not in cols:
        con.execute("ALTER TABLE moves ADD COLUMN fen_key TEXT")
        rows = con.execute("SELECT rowid, fen_before FROM moves").fetchall()
        con.executemany("UPDATE moves SET fen_key=? WHERE rowid=?", [(fen_key(f), r) for r, f in rows])
    con.execute("CREATE INDEX IF NOT EXISTS ix_moves_fen ON moves(fen_key)")
    gcols = {r[1] for r in con.execute("PRAGMA table_info(games)")}
    if "indexed" not in gcols:
        con.execute("ALTER TABLE games ADD COLUMN indexed INTEGER DEFAULT 0")
    con.commit()


TREE_PLIES = 40  # el árbol de aperturas cubre las primeras 20 jugadas


def index_games(con, limit: int | None = None) -> int:
    """Índice de posiciones (sin motor) de TODAS las partidas: base del árbol de aperturas. Rápido e idempotente."""
    import chess.pgn

    sql = "SELECT uuid, pgn FROM games WHERE COALESCE(indexed,0)=0"
    rows = con.execute(sql + (f" LIMIT {int(limit)}" if limit else "")).fetchall()
    n = 0
    for r in rows:
        out = []
        try:
            game = chess.pgn.read_game(io.StringIO(r[1] or ""))
            if game is not None:
                b = game.board()
                for i, mv in enumerate(game.mainline_moves()):
                    if i >= TREE_PLIES:
                        break
                    out.append((r[0], i + 1, fen_key(b.fen()), b.san(mv), mv.uci()))
                    b.push(mv)
        except Exception:
            out = []
        con.executemany("INSERT OR REPLACE INTO tree VALUES (?,?,?,?,?)", out)
        con.execute("UPDATE games SET indexed=1 WHERE uuid=?", (r[0],))
        n += 1
        if n % 500 == 0:
            con.commit()
    con.commit()
    return n


def cmd_index(a):
    con = db()
    n = index_games(con)
    tot = con.execute("SELECT COUNT(*) FROM tree").fetchone()[0]
    print(f"Indexadas {n} partidas nuevas · {tot} posiciones en el árbol.")


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def save_state(st: dict):
    DATA.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(st, indent=2))


def user_agent(cfg) -> str:
    contact = cfg.get("contact_email") or "sin-contacto"
    names = ",".join(f"{k}:{v}" for k, v in cfg["accounts"].items())
    return f"chess-coach/1.0 ({names}; contact: {contact})"


def opening_family(name: str | None) -> str | None:
    if not name:
        return None
    base = name.split(":")[0].strip()
    words = base.split()
    for i, w in enumerate(words):
        if w in {"Defense", "Defence", "Opening", "Game", "Gambit", "Attack", "System"}:
            return " ".join(words[: i + 1])
    return " ".join(words[:3])


def chesscom_opening(eco_url: str | None) -> str | None:
    if not eco_url or "/openings/" not in eco_url:
        return None
    slug = eco_url.rstrip("/").split("/openings/")[-1]
    name = slug.replace("-", " ")
    name = re.sub(r"\s+\d+\.+.*$", "", name).strip()  # quita las jugadas del final
    return name or None


def pgn_headers(pgn: str) -> dict:
    return dict(re.findall(r'\[(\w+) "([^"]*)"\]', pgn or ""))


def base_seconds(tc: str | None) -> int | None:
    if not tc or "/" in tc or tc == "-":
        return None
    try:
        return int(tc.split("+")[0])
    except ValueError:
        return None


def win_pct(cp: int) -> float:
    """Probabilidad de ganar (0-100) desde el POV del que juega (fórmula Lichess)."""
    cp = max(-1000, min(1000, cp))
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def classify(drop: float, is_best: bool) -> str:
    if is_best:
        return "best"
    if drop >= 30:
        return "blunder"
    if drop >= 20:
        return "mistake"
    if drop >= 10:
        return "inaccuracy"
    return "good"


def fmt_eval(cp: int | None, pov_white: bool = True) -> str:
    if cp is None:
        return "?"
    v = cp if pov_white else -cp
    if abs(v) >= MATE - 100:
        return "#" + ("+" if v > 0 else "-")
    return f"{v / 100:+.1f}"


def fmt_clock(s: float | None) -> str:
    if s is None:
        return "-"
    s = int(s)
    return f"{s // 60}:{s % 60:02d}"


def http_get(url, cfg, *, etag=None, params=None, accept=None, timeout=20, stream=False):
    import requests

    h = {"User-Agent": user_agent(cfg), "Accept-Encoding": "gzip"}
    if etag:
        h["If-None-Match"] = etag
    if accept:
        h["Accept"] = accept
    tok = os.environ.get("LICHESS_TOKEN")
    if tok and "lichess.org" in url:
        h["Authorization"] = f"Bearer {tok}"
    r = None
    for attempt in range(3):
        r = requests.get(url, headers=h, params=params, timeout=timeout, stream=stream)
        if r.status_code == 429:
            wait = 60 if "lichess.org" in url else 3 * (attempt + 1)
            warn(f"rate limit en {url.split('/')[2]}, espero {wait}s")
            time.sleep(wait)
            continue
        return r
    return r


# ───────────────────────── ingesta ─────────────────────────

def upsert(con, g: dict) -> bool:
    """Inserta o actualiza una partida. Devuelve True si es nueva."""
    exists = con.execute("SELECT 1 FROM games WHERE uuid=?", (g["uuid"],)).fetchone()
    if exists:
        con.execute(
            "UPDATE games SET my_accuracy=COALESCE(?,my_accuracy), opp_accuracy=COALESCE(?,opp_accuracy) WHERE uuid=?",
            (g.get("my_accuracy"), g.get("opp_accuracy"), g["uuid"]),
        )
        return False
    g = {**g, "synced_at": now_iso(),
         "date": datetime.fromtimestamp(g["end_time"], timezone.utc).strftime("%Y-%m-%d"),
         "opening_family": opening_family(g.get("opening"))}
    cols = ",".join(g)
    con.execute(f"INSERT INTO games({cols}) VALUES ({','.join('?' * len(g))})", list(g.values()))
    return True


def parse_chesscom(g: dict, me: str) -> dict | None:
    if g.get("rules", "chess") != "chess":
        return None
    w, b = g["white"], g["black"]
    if w["username"].lower() == me:
        my, opp, color = w, b, "white"
    elif b["username"].lower() == me:
        my, opp, color = b, w, "black"
    else:
        return None
    res = my.get("result", "")
    my_result = "win" if res == "win" else ("draw" if res in CHESSCOM_DRAWS else "loss")
    detail = opp.get("result") if my_result == "win" else res
    detail = {"lose": "other", "timevsinsufficient": "insufficient"}.get(detail, detail)
    pgn = g.get("pgn", "")
    hdr = pgn_headers(pgn)
    acc = g.get("accuracies") or {}
    other = "black" if color == "white" else "white"
    return dict(
        uuid=f"chesscom:{g.get('uuid') or g['url']}", source="chesscom", url=g["url"],
        end_time=int(g["end_time"]), time_class=g.get("time_class"),
        time_control=g.get("time_control"), rated=int(bool(g.get("rated"))),
        my_color=color, my_result=my_result, result_detail=detail,
        my_rating=my.get("rating"), opp_name=opp["username"], opp_rating=opp.get("rating"),
        eco=hdr.get("ECO"), opening=chesscom_opening(g.get("eco") or hdr.get("ECOUrl")),
        my_accuracy=acc.get(color), opp_accuracy=acc.get(other), pgn=pgn,
    )


def parse_lichess(g: dict, me: str) -> dict | None:
    if g.get("variant", "standard") != "standard" or g.get("status") in LICHESS_SKIP:
        return None
    pl = g.get("players", {})
    w, b = pl.get("white", {}), pl.get("black", {})
    wn = (w.get("user") or {}).get("name", "")
    bn = (b.get("user") or {}).get("name", "")
    if wn.lower() == me:
        my, opp, color, oppname = w, b, "white", bn or "IA"
    elif bn.lower() == me:
        my, opp, color, oppname = b, w, "black", wn or "IA"
    else:
        return None
    winner = g.get("winner")
    my_result = "draw" if not winner else ("win" if winner == color else "loss")
    detail = LICHESS_STATUS.get(g.get("status"), g.get("status"))
    if my_result == "draw" and detail == "agreed":
        detail = "draw"
    clk = g.get("clock") or {}
    tc = f"{clk['initial']}+{clk['increment']}" if clk else "-"
    speed = g.get("speed", "")
    op = g.get("opening") or {}
    return dict(
        uuid=f"lichess:{g['id']}", source="lichess", url=f"https://lichess.org/{g['id']}",
        end_time=int((g.get("lastMoveAt") or g.get("createdAt")) / 1000),
        time_class=SPEED_MAP.get(speed, speed), time_control=tc, rated=int(bool(g.get("rated"))),
        my_color=color, my_result=my_result, result_detail=detail,
        my_rating=my.get("rating"), opp_name=oppname, opp_rating=opp.get("rating"),
        eco=op.get("eco"), opening=op.get("name"),
        my_accuracy=(my.get("analysis") or {}).get("accuracy"),
        opp_accuracy=(opp.get("analysis") or {}).get("accuracy"),
        pgn=g.get("pgn", ""),
    )


def sync_chesscom(con, cfg, me: str, full: bool, timeout: int) -> int:
    r = http_get(f"{CHESSCOM_API}/player/{me}/games/archives", cfg, timeout=timeout)
    if r.status_code == 404:
        warn(f"Chess.com: el usuario '{me}' no existe")
        return 0
    r.raise_for_status()
    archives = r.json().get("archives", [])
    known = {row["key"]: row["etag"] for row in con.execute("SELECT key, etag FROM sync_state WHERE key LIKE 'chesscom:%'")}
    if full:
        targets = archives
    else:  # meses nunca bajados + los 2 últimos (que siguen cambiando)
        targets = list(dict.fromkeys([a for a in archives if f"chesscom:{a}" not in known] + archives[-2:]))
    new = 0
    for i, url in enumerate(targets):
        key = f"chesscom:{url}"
        r = http_get(url, cfg, etag=None if full else known.get(key), timeout=timeout)
        if r.status_code == 304:
            continue
        if r.status_code != 200:
            warn(f"Chess.com {url} → HTTP {r.status_code}")
            continue
        for g in r.json().get("games", []):
            p = parse_chesscom(g, me)
            if p and upsert(con, p):
                new += 1
        con.execute("INSERT OR REPLACE INTO sync_state(key, etag, fetched_at) VALUES (?,?,?)",
                    (key, r.headers.get("ETag"), now_iso()))
        con.commit()
        if len(targets) > 3:
            print(f"  chess.com {i + 1}/{len(targets)} meses…", file=sys.stderr)
        time.sleep(0.2)  # Chess.com pide requests en serie
    return new


def sync_lichess(con, cfg, me: str, full: bool, timeout: int) -> int:
    key = f"lichess:{me}"
    row = con.execute("SELECT value FROM sync_state WHERE key=?", (key,)).fetchone()
    since = None if full or not row else int(row["value"]) + 1
    params = {"pgnInJson": "true", "clocks": "true", "opening": "true", "accuracy": "true",
              "moves": "true", "tags": "true", "sort": "dateAsc"}
    if since:
        params["since"] = since
    r = http_get(f"{LICHESS_API}/games/user/{me}", cfg, params=params,
                 accept="application/x-ndjson", timeout=max(timeout, 60), stream=True)
    if r.status_code == 404:
        warn(f"Lichess: el usuario '{me}' no existe")
        return 0
    r.raise_for_status()
    new, last_ms, n = 0, since or 0, 0
    for line in r.iter_lines():
        if not line:
            continue
        g = json.loads(line)
        last_ms = max(last_ms, g.get("createdAt") or 0)  # el filtro `since` de Lichess es por createdAt
        p = parse_lichess(g, me)
        if p and upsert(con, p):
            new += 1
        n += 1
        if n % 500 == 0:
            con.commit()
            print(f"  lichess {n} partidas leídas…", file=sys.stderr)
    if last_ms:
        con.execute("INSERT OR REPLACE INTO sync_state(key, value, fetched_at) VALUES (?,?,?)",
                    (key, str(last_ms), now_iso()))
    con.commit()
    return new


def do_sync(full=False, timeout=20, quiet=False) -> dict:
    cfg = load_cfg()
    con = db()
    out = {}
    for src, user in cfg["accounts"].items():
        me = user.lower()
        try:
            if src == "chesscom":
                out[src] = sync_chesscom(con, cfg, me, full, timeout)
            elif src == "lichess":
                out[src] = sync_lichess(con, cfg, me, full, timeout)
            else:
                warn(f"fuente desconocida en config: {src}")
        except Exception as e:  # sin red, API caída, etc.: no botar la sesión
            warn(f"{src}: no pude sincronizar ({e.__class__.__name__}: {e})")
            out[src] = None
    try:
        index_games(con)  # el árbol queda al día con cada sync
    except Exception as e:
        warn(f"índice del árbol: {e}")
    if not quiet:
        parts = [f"{k}: {v if v is not None else 'error'} nuevas" for k, v in out.items()]
        print("Sync → " + " | ".join(parts))
    return out


def cmd_sync(a):
    do_sync(full=a.full, timeout=a.timeout)


def cmd_import_pgn(a):
    """Importa un archivo PGN (partidas OTB, de otra web, etc.)."""
    import chess.pgn

    cfg = load_cfg()
    names = my_names(cfg) | {n.lower() for n in (a.as_name or [])}
    con = db()
    text = Path(a.file).read_text(errors="ignore")
    f = io.StringIO(text)
    new = skipped = 0
    while True:
        game = chess.pgn.read_game(f)
        if game is None:
            break
        h = game.headers
        w, b = h.get("White", "").lower(), h.get("Black", "").lower()
        if w in names:
            color, opp = "white", h.get("Black")
        elif b in names:
            color, opp = "black", h.get("White")
        else:
            skipped += 1
            continue
        res = h.get("Result", "*")
        my_result = {"1-0": "win" if color == "white" else "loss",
                     "0-1": "win" if color == "black" else "loss",
                     "1/2-1/2": "draw"}.get(res)
        if not my_result:
            skipped += 1
            continue
        pgn = str(game)
        try:
            dt = datetime.strptime(h.get("Date", "").replace("??", "01"), "%Y.%m.%d").replace(tzinfo=timezone.utc)
        except ValueError:
            dt = datetime.now(timezone.utc)
        import hashlib

        tc = h.get("TimeControl", "-")
        bs = base_seconds(tc)
        tclass = a.time_class or ("rapid" if not bs or bs >= 600 else "blitz" if bs >= 180 else "bullet")
        g = dict(uuid="pgn:" + hashlib.sha1(pgn.encode()).hexdigest()[:16], source="pgn",
                 url=h.get("Site", ""), end_time=int(dt.timestamp()), time_class=tclass,
                 time_control=tc, rated=0, my_color=color, my_result=my_result,
                 result_detail="other", my_rating=_int(h.get("WhiteElo" if color == "white" else "BlackElo")),
                 opp_name=opp, opp_rating=_int(h.get("BlackElo" if color == "white" else "WhiteElo")),
                 eco=h.get("ECO"), opening=h.get("Opening"), my_accuracy=None, opp_accuracy=None, pgn=pgn)
        if upsert(con, g):
            new += 1
    con.commit()
    print(f"Importadas {new} partidas nuevas ({skipped} omitidas: no eras jugador o sin resultado).")


def _int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


# ───────────────────────── análisis ─────────────────────────

def find_stockfish(cfg) -> str:
    cands = [cfg.get("stockfish_path"), shutil.which("stockfish"),
             "/opt/homebrew/bin/stockfish", "/usr/local/bin/stockfish", "/usr/games/stockfish"]
    for c in cands:
        if c and os.path.exists(c):
            return c
    die("No encuentro Stockfish. Mac: brew install stockfish | Linux: sudo apt install stockfish")


def get_engine(cfg):
    import chess.engine

    eng = chess.engine.SimpleEngine.popen_uci(find_stockfish(cfg))
    an = cfg.get("analysis", {})
    opts = {"Threads": an.get("threads", 4), "Hash": an.get("hash_mb", 256)}
    eng.configure({k: v for k, v in opts.items() if k in eng.options})
    return eng


def pos_eval(eng, board, limit):
    """Eval en centipeones desde las blancas + mejor jugada + línea."""
    if board.is_checkmate():
        return (-MATE if board.turn else MATE), None, []
    if board.is_game_over(claim_draw=False):
        return 0, None, []
    info = eng.analyse(board, limit)
    cp = info["score"].white().score(mate_score=MATE)
    pv = info.get("pv") or []
    return cp, (pv[0] if pv else None), pv


def phase_of(board, ply: int) -> str:
    import chess

    vals = {chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}
    npm = sum(len(board.pieces(p, c)) * v for p, v in vals.items() for c in (chess.WHITE, chess.BLACK))
    if npm <= 26:
        return "endgame"
    return "opening" if ply < 24 else "middlegame"


MOVE_COLS = ("game_uuid, ply, move_no, color, is_me, san, fen_before, eval_before, eval_after, best_san, "
             "best_line, win_before, win_after, win_drop, cp_loss, class, phase, clock, time_trouble, fen_key")


def analyze_game(con, eng, g, limit) -> bool:
    import chess
    import chess.pgn

    game = chess.pgn.read_game(io.StringIO(g["pgn"]))
    if game is None:
        return False
    nodes = list(game.mainline())
    if len(nodes) < 2:
        con.execute("UPDATE games SET analyzed=1, n_plies=? WHERE uuid=?", (len(nodes), g["uuid"]))
        return True
    b = game.board()
    evals, bests, pvs = [], [], []
    for i in range(len(nodes) + 1):
        cp, best, pv = pos_eval(eng, b, limit)
        evals.append(cp)
        bests.append(best)
        pvs.append(pv)
        if i < len(nodes):
            b.push(nodes[i].move)

    me_white = g["my_color"] == "white"
    base = base_seconds(g["time_control"])
    b = game.board()
    rows, my_losses, my_wins = [], [], []
    cnt = {"blunder": 0, "mistake": 0, "inaccuracy": 0}
    for i, node in enumerate(nodes):
        mover_white = b.turn == chess.WHITE
        sign = 1 if mover_white else -1
        wb, wa = win_pct(sign * evals[i]), win_pct(sign * evals[i + 1])
        drop = max(0.0, wb - wa)
        cl = max(0, sign * (max(-1000, min(1000, evals[i])) - max(-1000, min(1000, evals[i + 1]))))
        best = bests[i]
        played = node.move
        best_san = b.san(best) if best else None
        line = []
        if pvs[i]:
            tmp = b.copy()
            for mv in pvs[i][:5]:
                try:
                    line.append(tmp.san(mv))
                    tmp.push(mv)
                except Exception:
                    break
        cls = classify(drop, best is not None and played == best)
        clk = node.clock()
        tt = int(bool(clk is not None and base and clk < max(10, 0.1 * base)))
        is_me = int(mover_white == me_white)
        rows.append((g["uuid"], i + 1, b.fullmove_number, "w" if mover_white else "b", is_me,
                     b.san(played), b.fen(), evals[i], evals[i + 1], best_san, " ".join(line),
                     round(wb, 1), round(wa, 1), round(drop, 1), cl, cls, phase_of(b, i), clk, tt, fen_key(b.fen())))
        if is_me:
            my_losses.append(cl)
            if cls in cnt:
                cnt[cls] += 1
        b.push(played)
    for cp in evals:
        my_wins.append(win_pct(cp if me_white else -cp))
    con.execute("DELETE FROM moves WHERE game_uuid=?", (g["uuid"],))
    con.executemany(f"INSERT INTO moves({MOVE_COLS}) VALUES ({','.join('?' * 20)})", rows)
    con.execute(
        "UPDATE games SET analyzed=1, n_plies=?, acpl=?, n_blunders=?, n_mistakes=?, n_inacc=?, peak_win=?, low_win=? WHERE uuid=?",
        (len(nodes), round(sum(my_losses) / max(1, len(my_losses)), 1), cnt["blunder"], cnt["mistake"],
         cnt["inaccuracy"], round(max(my_wins[8:] or my_wins), 1), round(min(my_wins[8:] or my_wins), 1), g["uuid"]),
    )
    return True


def do_analyze(limit_games: int | None = None, uuid: str | None = None, quiet=False) -> int:
    import chess.engine

    cfg = load_cfg()
    DATA.mkdir(exist_ok=True)
    lockf = open(DATA / "analyze.lock", "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        if not quiet:
            print("Ya hay un análisis corriendo en background; espera o revisa data/analyze.log")
        return 0
    con = db()
    if uuid:
        games = con.execute("SELECT * FROM games WHERE uuid=?", (uuid,)).fetchall()
    else:
        n = limit_games or cfg["analysis"].get("max_games_per_run", 25)
        games = con.execute("SELECT * FROM games WHERE analyzed=0 ORDER BY end_time DESC LIMIT ?", (n,)).fetchall()
    if not games:
        if not quiet:
            print("Nada pendiente de análisis.")
        return 0
    limit = chess.engine.Limit(time=cfg["analysis"].get("seconds_per_move", 0.15))
    eng = get_engine(cfg)
    done = 0
    t0 = time.time()
    try:
        for g in games:
            try:
                if analyze_game(con, eng, g, limit):
                    done += 1
                    con.commit()
                    if not quiet:
                        print(f"  ✓ {g['date']} vs {g['opp_name']} ({done}/{len(games)})", file=sys.stderr)
            except Exception as e:
                warn(f"no pude analizar {g['uuid']}: {e}")
                con.execute("UPDATE games SET analyzed=-1 WHERE uuid=?", (g["uuid"],))
                con.commit()
    finally:
        eng.quit()
    pend = con.execute("SELECT COUNT(*) FROM games WHERE analyzed=0").fetchone()[0]
    if not quiet:
        print(f"Analizadas {done} partidas en {time.time() - t0:.0f}s. Pendientes: {pend}.")
    return done


def cmd_analyze(a):
    do_analyze(limit_games=a.limit, uuid=a.uuid)


# ───────────────────────── consultas ─────────────────────────

def resolve_game(con, ref: str):
    if ref in ("last", "ultima", "última"):
        return con.execute("SELECT * FROM games ORDER BY end_time DESC LIMIT 1").fetchone()
    if ref.isdigit():  # "2" = penúltima
        return con.execute("SELECT * FROM games ORDER BY end_time DESC LIMIT 1 OFFSET ?", (int(ref) - 1,)).fetchone()
    row = con.execute("SELECT * FROM games WHERE uuid=? OR url=?", (ref, ref)).fetchone()
    if row:
        return row
    return con.execute("SELECT * FROM games WHERE uuid LIKE ? OR url LIKE ? ORDER BY end_time DESC LIMIT 1",
                       (f"%{ref}%", f"%{ref}%")).fetchone()


RES_ES = {"win": "GANASTE", "loss": "PERDISTE", "draw": "TABLAS"}


def game_line(g) -> str:
    acc = f" acc {g['my_accuracy']:.0f}" if g["my_accuracy"] else ""
    an = f" · ACPL {g['acpl']:.0f} · {g['n_blunders']}B/{g['n_mistakes']}M" if g["analyzed"] == 1 else " · (sin analizar)"
    return (f"{g['date']} [{g['source']}/{g['time_class']} {g['time_control']}] {g['my_color'][:1].upper()} "
            f"vs {g['opp_name']} ({g['opp_rating'] or '-'}) → {RES_ES[g['my_result']]} ({g['result_detail']}) · "
            f"{g['opening_family'] or g['eco'] or '?'}{acc}{an}")


def cmd_last(a):
    con = db(readonly=True)
    rows = con.execute("SELECT * FROM games ORDER BY end_time DESC LIMIT ?", (a.n,)).fetchall()
    for i, g in enumerate(rows, 1):
        print(f"{i:>2}. {game_line(g)}")


def cmd_game(a):
    con = db()
    g = resolve_game(con, a.ref)
    if not g:
        die(f"No encontré la partida '{a.ref}'")
    if g["analyzed"] != 1:
        print("(analizando esta partida ahora…)", file=sys.stderr)
        do_analyze(uuid=g["uuid"], quiet=True)
        g = resolve_game(con, g["uuid"])
    me_w = g["my_color"] == "white"
    print(f"# {RES_ES[g['my_result']]} vs {g['opp_name']} ({g['opp_rating'] or '-'}) — {g['date']}")
    print(f"Fuente: {g['source']} | {g['time_class']} {g['time_control']} | Jugaste con {'blancas' if me_w else 'negras'} "
          f"({g['my_rating'] or '-'}) | Final: {g['result_detail']}")
    print(f"Apertura: {g['eco'] or '?'} {g['opening'] or ''}")
    if g["my_accuracy"]:
        opp_acc = f"{g['opp_accuracy']:.1f}" if g["opp_accuracy"] else "-"
        print(f"Precisión (plataforma): tú {g['my_accuracy']:.1f} / rival {opp_acc}")
    print(f"Stockfish: ACPL {g['acpl']} | errores graves {g['n_blunders']} | errores {g['n_mistakes']} | imprecisiones {g['n_inacc']}")
    print(f"Tu prob. de ganar: máx {g['peak_win']}% / mín {g['low_win']}%")
    print(f"URL: {g['url'] or '-'}")
    mv = con.execute("SELECT * FROM moves WHERE game_uuid=? ORDER BY ply", (g["uuid"],)).fetchall()
    if not mv:
        return
    # curva compacta
    pts = [f"{m['move_no']}:{(m['win_after'] if m['is_me'] else 100 - m['win_after']):.0f}"
           for m in mv if m["ply"] % 10 == 0 or m["ply"] == len(mv)]
    print("\n## Curva (jugada: tu % de ganar)\n" + "  ".join(pts))
    crit = sorted([m for m in mv if m["is_me"] and m["win_drop"] >= a.min_drop], key=lambda m: -m["win_drop"])[: a.top]
    print(f"\n## Tus momentos críticos (top {len(crit)}, caída ≥ {a.min_drop}%)")
    for m in sorted(crit, key=lambda m: m["ply"]):
        dots = "." if m["color"] == "w" else "..."
        tt = " ⚠️ APURO DE RELOJ" if m["time_trouble"] else ""
        print(f"- {m['move_no']}{dots} {m['san']} [{m['class']}] {m['phase']} | tu % {m['win_before']:.0f}→{m['win_after']:.0f} "
              f"| eval {fmt_eval(m['eval_before'], me_w)}→{fmt_eval(m['eval_after'], me_w)} | reloj {fmt_clock(m['clock'])}{tt}")
        print(f"    Mejor: {m['best_san']}  (línea: {m['best_line']})")
        print(f"    FEN: {m['fen_before']}")
    # oportunidades perdidas: el rival se equivocó feo y no castigaste
    missed = []
    for i, m in enumerate(mv[:-1]):
        nxt = mv[i + 1]
        if not m["is_me"] and m["win_drop"] >= 20 and nxt["is_me"] and nxt["win_drop"] >= 15:
            missed.append(nxt)
    if missed:
        print("\n## El rival regaló y no lo castigaste")
        for m in missed:
            dots = "." if m["color"] == "w" else "..."
            print(f"- {m['move_no']}{dots} jugaste {m['san']}, había {m['best_san']} ({m['best_line']}) | FEN: {m['fen_before']}")
    if a.pgn:
        print("\n## PGN\n" + g["pgn"])


def where_clause(a, alias="g"):
    w, p = ["1=1"], []
    if getattr(a, "days", None):
        w.append(f"{alias}.end_time >= ?")
        p.append(int(time.time()) - a.days * 86400)
    if getattr(a, "time_class", None):
        w.append(f"{alias}.time_class = ?")
        p.append(a.time_class)
    if getattr(a, "source", None):
        w.append(f"{alias}.source = ?")
        p.append(a.source)
    if getattr(a, "color", None):
        w.append(f"{alias}.my_color = ?")
        p.append(a.color)
    return " AND ".join(w), p


def score(w, d, n):
    return f"{100 * (w + 0.5 * d) / n:.0f}%" if n else "-"


def cmd_stats(a):
    con = db(readonly=True)
    W, P = where_clause(a)
    q = lambda sql, extra=(): con.execute(sql, P + list(extra)).fetchall()  # noqa: E731
    tot = q(f"SELECT COUNT(*) n, SUM(my_result='win') w, SUM(my_result='draw') d, SUM(my_result='loss') l, "
            f"SUM(analyzed=1) an FROM games g WHERE {W}")[0]
    if not tot["n"]:
        print("Sin partidas con esos filtros.")
        return
    filt = ", ".join(f"{k}={getattr(a, k)}" for k in ("days", "time_class", "source", "color") if getattr(a, k, None)) or "todo el historial"
    print(f"# Estadísticas ({filt})")
    print(f"Partidas {tot['n']} | +{tot['w']} ={tot['d']} -{tot['l']} | score {score(tot['w'], tot['d'], tot['n'])} | analizadas {tot['an']}")

    print("\n## Rating (primero → último en el período)")
    for r in q(f"SELECT source, time_class, COUNT(*) n FROM games g WHERE {W} GROUP BY 1,2 HAVING n>=3 ORDER BY n DESC"):
        ends = q(f"SELECT my_rating FROM games g WHERE {W} AND source=? AND time_class=? ORDER BY end_time", (r["source"], r["time_class"]))
        print(f"- {r['source']}/{r['time_class']}: {ends[0]['my_rating']} → {ends[-1]['my_rating']} ({r['n']} partidas)")

    print("\n## Por color")
    for r in q(f"SELECT my_color c, COUNT(*) n, SUM(my_result='win') w, SUM(my_result='draw') d FROM games g WHERE {W} GROUP BY 1"):
        print(f"- {r['c']}: {r['n']} partidas, score {score(r['w'], r['d'], r['n'])}")

    print("\n## Cómo pierdes")
    for r in q(f"SELECT result_detail k, COUNT(*) n FROM games g WHERE {W} AND my_result='loss' GROUP BY 1 ORDER BY n DESC"):
        print(f"- {r['k']}: {r['n']}")

    print(f"\n## Aperturas (≥{a.min_games} partidas)")
    for r in q(f"SELECT my_color c, COALESCE(opening_family, eco, '?') o, COUNT(*) n, SUM(my_result='win') w, "
               f"SUM(my_result='draw') d, AVG(CASE WHEN analyzed=1 THEN acpl END) acpl FROM games g WHERE {W} "
               f"GROUP BY 1,2 HAVING n>=? ORDER BY n DESC LIMIT 15", (a.min_games,)):
        acpl = f", ACPL {r['acpl']:.0f}" if r["acpl"] else ""
        print(f"- [{r['c'][:1].upper()}] {r['o']}: {r['n']} partidas, score {score(r['w'], r['d'], r['n'])}{acpl}")

    an = tot["an"] or 0
    if not an:
        print("\n(Sin partidas analizadas aún: corre ./coach analyze para ver patrones de errores.)")
        return
    print(f"\n## Errores por fase (tus jugadas, {an} partidas analizadas)")
    for r in q(f"SELECT m.phase ph, COUNT(*) jugadas, SUM(m.class='blunder') b, SUM(m.class='mistake') mi, "
               f"AVG(m.cp_loss) cpl FROM moves m JOIN games g ON g.uuid=m.game_uuid WHERE {W} AND m.is_me=1 GROUP BY 1"):
        print(f"- {r['ph']}: {r['b']} graves + {r['mi']} errores en {r['jugadas']} jugadas "
              f"({100 * (r['b'] + r['mi']) / r['jugadas']:.1f}% de tus jugadas), CPL medio {r['cpl']:.0f}")

    tt = q(f"SELECT SUM(m.time_trouble) tt, COUNT(*) n FROM moves m JOIN games g ON g.uuid=m.game_uuid "
           f"WHERE {W} AND m.is_me=1 AND m.class IN ('blunder','mistake')")[0]
    if tt["n"]:
        print(f"\n## Reloj: {tt['tt'] or 0} de tus {tt['n']} errores/graves fueron en apuro de tiempo ({100 * (tt['tt'] or 0) / tt['n']:.0f}%)")

    conv = q(f"SELECT COUNT(*) n, SUM(my_result='win') w FROM games g WHERE {W} AND analyzed=1 AND peak_win>=85")[0]
    if conv["n"]:
        print(f"\n## Conversión: de {conv['n']} partidas donde llegaste a ≥85% de ganar, ganaste {conv['w']} "
              f"({100 * conv['w'] / conv['n']:.0f}%). Ventajas botadas: {conv['n'] - conv['w']}")
        for g in q(f"SELECT * FROM games g WHERE {W} AND analyzed=1 AND peak_win>=85 AND my_result!='win' ORDER BY end_time DESC LIMIT 5"):
            print(f"  · {game_line(g)} | {g['uuid']}")
    comeback = q(f"SELECT COUNT(*) n FROM games g WHERE {W} AND analyzed=1 AND low_win<=15 AND my_result!='loss'")[0]["n"]
    print(f"Remontadas (desde ≤15% a no perder): {comeback}")

    print("\n## Tipo de jugada donde más fallas (graves, por pieza movida)")
    for r in q(f"SELECT CASE WHEN substr(m.san,1,1) IN ('K','Q','R','B','N') THEN substr(m.san,1,1) "
               f"WHEN m.san LIKE 'O-O%' THEN 'enroque' ELSE 'peón' END pz, COUNT(*) n FROM moves m "
               f"JOIN games g ON g.uuid=m.game_uuid WHERE {W} AND m.is_me=1 AND m.class='blunder' GROUP BY 1 ORDER BY n DESC"):
        print(f"- {r['pz']}: {r['n']}")


def cmd_positions(a):
    """Posiciones de tus propios errores, para entrenar."""
    con = db(readonly=True)
    W, P = where_clause(a)
    cls = [c.strip() for c in a.cls.split(",")]
    sql = (f"SELECT m.*, g.opening_family, g.time_class, g.date, g.url, g.opp_name FROM moves m "
           f"JOIN games g ON g.uuid=m.game_uuid WHERE {W} AND m.is_me=1 AND m.class IN ({','.join('?' * len(cls))})")
    P += cls
    if a.phase:
        sql += " AND m.phase=?"
        P.append(a.phase)
    sql += " ORDER BY " + ("random()" if a.random else "g.end_time DESC, m.ply") + " LIMIT ?"
    P.append(a.n)
    for i, m in enumerate(con.execute(sql, P).fetchall(), 1):
        side = "blancas" if m["color"] == "w" else "negras"
        print(f"{i}. [{m['phase']} · {m['class']}] juegan {side} · {m['date']} vs {m['opp_name']} · {m['opening_family'] or ''}")
        print(f"   FEN: {m['fen_before']}")
        if a.reveal:
            print(f"   Jugaste {m['san']} · mejor {m['best_san']} ({m['best_line']}) · caída {m['win_drop']}%")
        print(f"   ref: {m['game_uuid']}#{m['ply']}")


def cmd_check(a):
    """Evalúa una jugada propuesta en una posición (para corregir ejercicios)."""
    import chess
    import chess.engine

    cfg = load_cfg()
    board = chess.Board(a.fen)
    try:
        move = board.parse_san(a.move)
    except ValueError:
        try:
            move = chess.Move.from_uci(a.move)
            assert move in board.legal_moves
        except Exception:
            die(f"Jugada ilegal o no reconocida: {a.move}")
    eng = get_engine(cfg)
    try:
        lim = chess.engine.Limit(time=a.time)
        cp0, best, pv = pos_eval(eng, board, lim)
        b2 = board.copy()
        b2.push(move)
        cp1, _, pv1 = pos_eval(eng, b2, lim)
    finally:
        eng.quit()
    white = board.turn == chess.WHITE
    sign = 1 if white else -1
    drop = max(0.0, win_pct(sign * cp0) - win_pct(sign * cp1))
    tmp = board.copy()
    line = []
    for mv in pv[:6]:
        line.append(tmp.san(mv))
        tmp.push(mv)
    tmp = b2.copy()
    reply = []
    for mv in pv1[:4]:
        reply.append(tmp.san(mv))
        tmp.push(mv)
    print(f"Propuesta: {board.san(move)} → eval {fmt_eval(cp1, white)} (POV de quien juega) · caída {drop:.1f}% · "
          f"[{classify(drop, move == best)}]")
    print(f"Respuesta del motor tras tu jugada: {' '.join(reply) or '-'}")
    print(f"Mejor: {board.san(best) if best else '-'} → eval {fmt_eval(cp0, white)} · línea: {' '.join(line)}")


def cmd_board(a):
    import chess

    b = chess.Board(a.fen)
    orient = chess.WHITE if a.white else (chess.BLACK if a.black else b.turn)
    print(b.unicode(borders=True, empty_square="·", orientation=orient))
    print(f"Juegan {'blancas' if b.turn else 'negras'}")


def cmd_sql(a):
    con = db(readonly=True)
    rows = con.execute(a.query).fetchmany(a.limit)
    if not rows:
        print("(sin filas)")
        return
    cols = rows[0].keys()
    print(" | ".join(cols))
    for r in rows:
        print(" | ".join("" if r[c] is None else str(r[c])[:120] for c in cols))


def parse_frontmatter(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip("[]")
    return fm


def cmd_kb(a):
    """Índice / búsqueda en la base de conocimiento (knowledge/*.md)."""
    words = [w.lower() for w in a.query]
    hits = []
    for p in sorted(KB_DIR.rglob("*.md")):
        text = p.read_text(errors="ignore")
        low = text.lower()
        if words and not all(w in low for w in words):
            continue
        fm = parse_frontmatter(text)
        rel = p.relative_to(ROOT)
        snippet = ""
        if words:
            for line in text.splitlines():
                if any(w in line.lower() for w in words) and not line.startswith(("---", "tags:", "title:")):
                    snippet = line.strip()[:140]
                    break
        hits.append((rel, fm, snippet))
    if not hits:
        print("Sin resultados en knowledge/.")
        return
    for rel, fm, sn in hits:
        print(f"- {rel} — {fm.get('title', '')} [{fm.get('tags', '')}]")
        if sn:
            print(f"    «{sn}»")


def spawn_background(args, log):
    """Lanza un proceso desacoplado (análisis en background). Aislado en una función para poder testear."""
    return subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, cwd=ROOT)


def cmd_session_brief(a):
    """Lo corre el hook SessionStart: sync rápido + resumen para el coach. Nunca falla."""
    try:
        cfg = load_cfg()
    except SystemExit:
        print("Chess Coach: falta configurar tus cuentas → http://127.0.0.1:4520 (⚙ Configuración)")
        return
    st = load_state()
    prev = st.get("last_seen_end_time", 0)
    sess = {}
    if not sys.stdin.isatty():
        try:
            sess = json.loads(sys.stdin.read() or "{}")
        except Exception:
            sess = {}
    DATA.mkdir(exist_ok=True)
    SESSION_PATH.write_text(json.dumps({"session_id": sess.get("session_id"), "started": time.time()}))
    con = db()
    last = con.execute("SELECT MAX(fetched_at) FROM sync_state").fetchone()[0]
    recent = bool(last) and (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() < 600
    # en la UI cada mensaje abre una sesión: no re-sincronizar si se hizo hace <10 min
    res = {} if recent else do_sync(timeout=8, quiet=True)
    tot = con.execute("SELECT COUNT(*) n, SUM(analyzed=1) an, SUM(analyzed=0) pend FROM games").fetchone()
    new = con.execute("SELECT * FROM games WHERE end_time>? ORDER BY end_time DESC", (prev,)).fetchall()
    print("## Chess Coach — estado al abrir sesión")
    print(f"Cuentas: {', '.join(f'{k}={v}' for k, v in cfg['accounts'].items())}")
    style = cfg.get("coach_style", "profe")
    sp = COACHES_DIR / f"{style}.md"
    print(f"ESTILO DEL COACH: {style} → lee y sigue coaches/{style}.md" + ("" if sp.exists() else " (¡no existe! usa coaches/profe.md)"))
    rep = {k: v for k, v in (cfg.get("repertoire") or {}).items() if v}
    if rep:
        print("Repertorio declarado: " + " | ".join(f"{REP_KEYS.get(k, k)}: {', '.join(v)}" for k, v in rep.items()))
    else:
        print("Repertorio declarado: (vacío) → pregúntale qué juega y fíjalo con ./coach repertoire set …")
    errs = [k for k, v in res.items() if v is None]
    if errs:
        print(f"⚠️ No se pudo sincronizar: {', '.join(errs)} (sin red o API caída). Datos pueden estar desfasados.")
    print(f"Partidas en base: {tot['n']} | analizadas: {tot['an'] or 0} | pendientes: {tot['pend'] or 0}")
    if prev and new:
        print(f"Nuevas desde la última sesión: {len(new)}")
        for g in new[:8]:
            print(f"- {game_line(g)}")
    elif not prev:
        print("Primera sesión: aún no hay perfil del jugador. Propón un diagnóstico inicial (skill diagnostico).")
    else:
        print("Sin partidas nuevas desde la última sesión.")
    for r in con.execute("SELECT source, time_class, my_rating, MAX(end_time) FROM games GROUP BY 1,2 ORDER BY 4 DESC LIMIT 4"):
        print(f"Rating actual {r['source']}/{r['time_class']}: {r['my_rating']}")
    if (tot["pend"] or 0) > 0:
        n = cfg["analysis"].get("max_games_per_run", 25)
        log = open(DATA / "analyze.log", "a")
        spawn_background([sys.executable, str(CLI_PATH), "analyze", "--limit", str(n)], log)
        print(f"(Analizando hasta {n} pendientes en background con Stockfish; log en data/analyze.log)")
    act = TRAIN_DIR / "active.json"
    if act.exists():
        try:
            t = json.loads(act.read_text())
            print(f"Sesión jugada a jugada ACTIVA: {t['id']} ({t.get('opening') or 'sin apertura'}, "
                  f"{len(t['moves'])} medias jugadas) → retómala con ./coach train status")
        except Exception:
            pass
    cur = ESTADO / "actual.md"
    if cur.exists():
        txt = cur.read_text()
        m = re.search(r"## Siguiente paso\s*\n(.*?)(\n## |\Z)", txt, re.S)
        if m and m.group(1).strip() and "(nada" not in m.group(1):
            print("Siguiente paso registrado: " + " ".join(m.group(1).split())[:300])
    if not (ESTADO / "actual.md").exists():
        print("⚠️ Falta estado/ → guarda la configuración en la UI (copia las plantillas)")
    mx = con.execute("SELECT MAX(end_time) FROM games").fetchone()[0]
    if mx:
        st["last_seen_end_time"] = mx
        save_state(st)


def cmd_tick(a):
    do_sync(timeout=20, quiet=True)
    do_analyze(limit_games=10, quiet=True)


def cmd_watch(a):
    print(f"Watch: sync + análisis cada {a.interval}s. Ctrl-C para salir.")
    while True:
        try:
            out = do_sync(timeout=20)
            if any(out.values()):
                do_analyze(limit_games=10)
        except KeyboardInterrupt:
            break
        except Exception as e:
            warn(str(e))
        try:
            time.sleep(a.interval)
        except KeyboardInterrupt:
            break


PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array><string>{py}</string><string>{script}</string><string>tick</string></array>
  <key>WorkingDirectory</key><string>{root}</string>
  <key>StartInterval</key><integer>{interval}</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>{root}/data/launchd.log</string>
  <key>StandardErrorPath</key><string>{root}/data/launchd.log</string>
</dict></plist>
"""
LABEL = "local.chesscoach.tick"


def cmd_autosync(a):
    """Instala/quita un job de macOS (launchd) que sincroniza y analiza solo."""
    if sys.platform != "darwin":
        die("autosync usa launchd (solo macOS). En Linux usa cron: */10 * * * * cd RUTA && ./coach tick")
    dest = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
    if a.action == "off":
        subprocess.run(["launchctl", "unload", "-w", str(dest)], capture_output=True)
        dest.unlink(missing_ok=True)
        print("Autosync desactivado.")
        return
    DATA.mkdir(exist_ok=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(PLIST.format(label=LABEL, py=sys.executable, script=CLI_PATH,
                                 root=ROOT, interval=a.interval))
    subprocess.run(["launchctl", "unload", "-w", str(dest)], capture_output=True)
    subprocess.run(["launchctl", "load", "-w", str(dest)], check=False)
    print(f"Autosync activo: cada {a.interval // 60} min (mientras el Mac esté prendido). Log: data/launchd.log")



UI_LABEL = "local.chesscoach.ui"
UI_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array><string>{py}</string><string>{script}</string><string>ui</string><string>--no-open</string><string>--port</string><string>{port}</string></array>
  <key>WorkingDirectory</key><string>{root}</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>{path}</string>
    <key>HOME</key><string>{home}</string>
    <key>CHESS_COACH_CLAUDE</key><string>{claude}</string>{extra_env}
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>{root}/data/ui.log</string>
  <key>StandardErrorPath</key><string>{root}/data/ui.log</string>
</dict></plist>
"""


def cmd_service(a):
    """macOS: la UI web como servicio (LaunchAgent) — parte al iniciar sesión y se reinicia si se cae."""
    if sys.platform != "darwin":
        die("service usa launchd (solo macOS). En Linux: un systemd --user o tmux con ./coach ui --no-open")
    dest = Path.home() / "Library/LaunchAgents" / f"{UI_LABEL}.plist"
    url = f"http://127.0.0.1:{a.port}/"
    if a.action == "off":
        subprocess.run(["launchctl", "unload", "-w", str(dest)], capture_output=True)
        dest.unlink(missing_ok=True)
        print("Servicio web apagado.")
        return
    if a.action == "status":
        r = subprocess.run(["launchctl", "list", UI_LABEL], capture_output=True, text=True)
        try:
            import requests

            code = requests.get(url + "api/context", timeout=3).status_code
        except Exception as e:
            code = e.__class__.__name__
        print(f"launchd: {'cargado' if r.returncode == 0 else 'no cargado'} · {url} → {code}")
        return
    if " " in str(ROOT) and str(ROOT).startswith(str(Path.home() / "Documents")):
        warn("el proyecto está en ~/Documents: macOS puede bloquear al servicio. Recomendado: moverlo a ~/chess-coach")
    claude = os.environ.get("CHESS_COACH_CLAUDE") or shutil.which("claude") or next(
        (str(p) for p in (Path.home() / ".local/bin/claude", Path.home() / ".claude/local/claude") if p.exists()), "")
    path = os.environ.get("PATH", "") + f":{Path.home()}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
    # launchd no hereda tu shell: copiamos la config de Claude Code (Vertex/Bedrock/API) que tengas exportada
    from xml.sax.saxutils import escape as xml_escape

    prefixes = ("CLAUDE_CODE_", "ANTHROPIC_", "CLOUD_ML_", "VERTEX_", "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_",
                "AWS_", "LICHESS_TOKEN", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY")
    passed = {k: v for k, v in os.environ.items() if k.startswith(prefixes)}
    extra_env = "".join(f"\n    <key>{xml_escape(k)}</key><string>{xml_escape(v)}</string>" for k, v in sorted(passed.items()))
    DATA.mkdir(exist_ok=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(UI_PLIST.format(label=UI_LABEL, py=sys.executable, script=CLI_PATH, root=ROOT, port=a.port,
                                    path=path, home=Path.home(), claude=claude, extra_env=extra_env))
    dest.chmod(0o600)  # puede llevar credenciales: solo tu usuario
    subprocess.run(["launchctl", "unload", "-w", str(dest)], capture_output=True)
    subprocess.run(["launchctl", "load", "-w", str(dest)], check=False)
    print(f"Servicio web activo en {url} (parte al iniciar sesión y se reinicia solo). Log: data/ui.log")
    auth = ("Vertex AI" if passed.get("CLAUDE_CODE_USE_VERTEX") else "Bedrock" if passed.get("CLAUDE_CODE_USE_BEDROCK")
            else "API key" if passed.get("ANTHROPIC_API_KEY") else "tu login de Claude (suscripción)")
    print(f"Chat: Claude Code va a usar {auth}" + (f" · variables copiadas: {', '.join(sorted(passed))}" if passed else ""))
    if not claude:
        warn("no encontré `claude`: el chat no va a responder hasta que instales Claude Code y corras ./coach service on de nuevo")


# ───────────────────────── estilo del coach y repertorio ─────────────────────────

COACHES_DIR = ROOT / "coaches"
REP_KEYS = {"white": "Con blancas", "vs_e4": "Con negras vs 1.e4", "vs_d4": "Con negras vs 1.d4",
            "vs_otros": "Con negras vs 1.c4/1.Nf3/otras"}
# nombres en español → palabra clave en los nombres de apertura de Chess.com/Lichess (inglés)
OPENING_ALIASES = {
    "catalana": "catalan", "siciliana": "sicilian", "francesa": "french", "caro-kann": "caro-kann",
    "caro kann": "caro-kann", "italiana": "italian", "giuoco piano": "italian", "española": "ruy lopez",
    "ruy lopez": "ruy lopez", "escocesa": "scotch", "londres": "london", "sistema londres": "london",
    "gambito de dama": "queen's gambit", "gambito de dama aceptado": "queen's gambit accepted",
    "gambito de dama rehusado": "queen's gambit declined", "eslava": "slav", "semieslava": "semi-slav",
    "india de rey": "king's indian", "india de dama": "queen's indian", "nimzo-india": "nimzo-indian",
    "nimzoindia": "nimzo-indian", "grünfeld": "grunfeld", "grunfeld": "grunfeld", "inglesa": "english",
    "escandinava": "scandinavian", "pirc": "pirc", "moderna": "modern", "holandesa": "dutch",
    "alekhine": "alekhine", "petrov": "petrov", "rusa": "petrov", "vienesa": "vienna", "gambito de rey": "king's gambit",
    "benoni": "benoni", "benko": "benko", "réti": "reti", "reti": "reti", "jobava": "jobava", "trompowsky": "trompowsky",
    "colle": "colle", "dos caballos": "two knights", "cuatro caballos": "four knights", "philidor": "philidor",
    "bird": "bird", "larsen": "nimzo-larsen", "ataque indio de rey": "king's indian attack",
}


# rangos ECO por apertura (respaldo cuando el nombre no viene o no calza)
ECO_RANGES = {
    "catalan": [("E00", "E09")], "nimzo-indian": [("E20", "E59")], "queen's indian": [("E12", "E19")],
    "king's indian": [("E60", "E99")], "grunfeld": [("D70", "D99")], "slav": [("D10", "D19")],
    "semi-slav": [("D43", "D49")], "queen's gambit accepted": [("D20", "D29")],
    "queen's gambit declined": [("D30", "D69")], "queen's gambit": [("D06", "D69")],
    "caro-kann": [("B10", "B19")], "sicilian": [("B20", "B99")], "french": [("C00", "C19")],
    "italian": [("C50", "C54")], "ruy lopez": [("C60", "C99")], "scotch": [("C44", "C45")],
    "petrov": [("C42", "C43")], "scandinavian": [("B01", "B01")], "alekhine": [("B02", "B05")],
    "pirc": [("B07", "B09")], "dutch": [("A80", "A99")], "english": [("A10", "A39")],
    "benoni": [("A56", "A79")], "vienna": [("C25", "C29")], "king's gambit": [("C30", "C39")],
    "two knights": [("C55", "C59")], "four knights": [("C46", "C49")], "philidor": [("C41", "C41")],
}


def in_repertoire(g, kws) -> bool:
    name = (g["opening"] or "").lower()
    if any(k in name for k in kws):
        return True
    eco = (g["eco"] or "").upper()
    return bool(eco) and any(lo <= eco <= hi for k in kws for lo, hi in ECO_RANGES.get(k, []))


def rep_keyword(name: str) -> str:
    n = name.strip().lower()
    return OPENING_ALIASES.get(n, n)


def first_white_move(pgn: str) -> str | None:
    body = re.sub(r"\[.*?\]\s*", "", pgn or "", flags=re.S)
    body = re.sub(r"\{.*?\}", "", body, flags=re.S)
    m = re.search(r"1\.\s*([^\s.]+)", body)
    return m.group(1) if m else None


def rep_bucket(g) -> str:
    if g["my_color"] == "white":
        return "white"
    fm = first_white_move(g["pgn"])
    return {"e4": "vs_e4", "d4": "vs_d4"}.get(fm or "", "vs_otros")


def log_decision(text: str, why: str | None = None):
    """Registro de decisiones del alumno (estado/decisiones.md). Lo escribe el código, no el LLM."""
    ESTADO.mkdir(exist_ok=True)
    p = ESTADO / "decisiones.md"
    if not p.exists():
        p.write_text("# Decisiones\n<!-- qué cambió, por qué y quién decidió -->\n")
    day = datetime.now().strftime("%Y-%m-%d")
    with p.open("a") as f:
        f.write(f"- {day} · {text} · decidió: alumno · motivo: {why or 'no registrado'}\n")


def save_cfg_field(key, value):
    raw = json.loads(CFG_PATH.read_text())
    raw[key] = value
    CFG_PATH.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n")


def cmd_style(a):
    """Lista los estilos de coach o elige uno."""
    cfg = load_cfg()
    styles = sorted(p.stem for p in COACHES_DIR.glob("*.md") if p.stem.lower() != "readme")
    if a.action == "set":
        if a.name not in styles:
            die(f"No existe coaches/{a.name}.md. Disponibles: {', '.join(styles)}")
        prev = cfg.get("coach_style", "profe")
        save_cfg_field("coach_style", a.name)
        log_decision(f"Estilo de coach: {a.name} (antes: {prev})", a.why)
        print(f"Estilo activo: {a.name}. Desde ahora el coach sigue coaches/{a.name}.md (abre una sesión nueva o dile 'cambia de estilo').")
        return
    cur = cfg.get("coach_style", "profe")
    for s_ in styles:
        fm = parse_frontmatter((COACHES_DIR / f"{s_}.md").read_text())
        mark = "▶" if s_ == cur else " "
        print(f"{mark} {s_:<12} {fm.get('title', '')} — {fm.get('description', '')}")


def cmd_repertoire(a):
    """Muestra/fija tu repertorio declarado y mide si lo juegas y cómo te va."""
    cfg = load_cfg()
    rep = cfg.get("repertoire", {}) or {}
    if a.action == "set":
        if a.key not in REP_KEYS:
            die(f"clave inválida. Usa: {', '.join(REP_KEYS)}")
        prev = ", ".join(rep.get(a.key) or []) or "—"
        rep[a.key] = [x.strip() for x in " ".join(a.value).split(",") if x.strip()]
        save_cfg_field("repertoire", rep)
        log_decision(f"Repertorio [{REP_KEYS[a.key]}]: {', '.join(rep[a.key]) or '(vacío)'} (antes: {prev})", a.why)
        print(f"{REP_KEYS[a.key]}: {', '.join(rep[a.key]) or '(vacío)'}")
        return
    if not any(rep.values()):
        print("No hay repertorio declarado. Ej: ./coach repertoire set white Catalana")
        return
    con = db(readonly=True)
    W, P = where_clause(a)
    games = con.execute(f"SELECT * FROM games g WHERE {W}", P).fetchall()
    for key, label in REP_KEYS.items():
        names = rep.get(key) or []
        if not names:
            continue
        kws = [rep_keyword(n) for n in names]
        bucket = [g for g in games if rep_bucket(g) == key]
        inn = [g for g in bucket if in_repertoire(g, kws)]
        out = [g for g in bucket if g not in inn]
        sc = lambda L: score(sum(g["my_result"] == "win" for g in L), sum(g["my_result"] == "draw" for g in L), len(L))  # noqa: E731
        print(f"## {label}: {', '.join(names)}")
        if not bucket:
            print("  sin partidas en este tramo")
            continue
        print(f"  En repertorio: {len(inn)}/{len(bucket)} ({100 * len(inn) / len(bucket):.0f}%) · score {sc(inn)}  | fuera: score {sc(out)}")
        an = [g for g in inn if g["analyzed"] == 1]
        if an:
            print(f"  ACPL dentro del repertorio: {sum(g['acpl'] for g in an) / len(an):.0f}")
        top = {}
        for g in out:
            k = g["opening_family"] or g["eco"] or "?"
            top[k] = top.get(k, 0) + 1
        if top:
            print("  Fuera del repertorio (lo que terminaste jugando o te propuso el rival): " +
                  ", ".join(f"{k} ×{v}" for k, v in sorted(top.items(), key=lambda x: -x[1])[:5]))


