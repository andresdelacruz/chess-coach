"""
webui.py — front web local del Chess Coach (pieza 1: interfaz).

  ./coach ui            → http://127.0.0.1:4520 (solo este Mac)

- Chat: cada mensaje corre `claude -p` en la carpeta del proyecto (mismas reglas, skills y hooks).
  💸 Cada mensaje consume tokens; la UI muestra el costo que reporta Claude Code.
- Jugada a jugada en tablero: Stockfish + tus datos, sin LLM (gratis).
- Feedback "Me sirvió / Hay que corregir" → estado/feedback.md (local; los cambios de reglas se acuerdan contigo).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import core as C
import trainer as T
import tree as TR

WEB = C.ROOT / "web"
CONV_DIR = C.DATA / "ui" / "conversations"
UI_PROMPT = (
    "Estás respondiendo desde la UI web del Chess Coach (no hay terminal interactiva). "
    "No puedes pedir permisos: si el alumno quiere cambiar estilo o repertorio, dile que use ⚙ Configuración. "
    "EN CORTO Y AL GRANO: máximo 5 líneas salvo que pida detalle; sin preámbulos ni resúmenes de lo que hiciste. "
    "Usa las mínimas herramientas posibles (cada paso cuesta). La UI ya dibuja el tablero: no uses `./coach board`. "
    "Si el alumno quiere entrenar jugada a jugada, dile que escriba «entrenemos <apertura>» o use el botón ♟ Entrenar: "
    "el tablero del chat lo hace gratis, sin ti."
)
MODELS = {"sonnet": "Sonnet · rápido y barato", "opus": "Opus · más profundo", "haiku": "Haiku · el más barato"}
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}
_lock = threading.Lock()


# ───────────────────────── utilidades ─────────────────────────

def claude_bin() -> str | None:
    return os.environ.get("CHESS_COACH_CLAUDE") or shutil.which("claude") or next(
        (str(p) for p in (Path.home() / ".local/bin/claude", Path.home() / ".claude/local/claude",
                           Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude")) if p.exists()), None)


def run_cli(*args, timeout=120) -> tuple[bool, str]:
    """Corre ./coach con los mismos efectos que en terminal (eventos, decisiones, bitácora)."""
    p = subprocess.run([sys.executable, str(C.CLI_PATH), *args], cwd=C.ROOT, capture_output=True, text=True, timeout=timeout)
    out = (p.stdout or "") + (("\n" + p.stderr.strip()) if p.returncode and p.stderr else "")
    return p.returncode == 0, out.strip()


def ago(iso: str | None) -> str:
    if not iso:
        return "nunca"
    try:
        s = (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()
    except ValueError:
        return iso
    if s < 90:
        return "recién"
    if s < 3600:
        return f"hace {int(s // 60)} min"
    if s < 86400:
        return f"hace {int(s // 3600)} h"
    return f"hace {int(s // 86400)} d"


def fm(text: str) -> dict:
    return C.parse_frontmatter(text)


# ───────────────────────── contexto del harness ─────────────────────────

def context() -> dict:
    if not has_config():
        return {"needs_setup": True, "setup": setup_state()}
    cfg = C.load_cfg()
    out = {"accounts": cfg["accounts"], "style": cfg.get("coach_style", "profe"),
           "repertoire": {k: v for k, v in (cfg.get("repertoire") or {}).items()},
           "rep_labels": C.REP_KEYS, "claude": bool(claude_bin()), "explorer": bool(os.environ.get("LICHESS_TOKEN"))}
    try:
        out["stockfish"] = bool(C.find_stockfish(cfg))
    except SystemExit:
        out["stockfish"] = False
    out["styles"] = []
    for p in sorted(C.COACHES_DIR.glob("*.md")):
        if p.stem.lower() != "readme":
            out["styles"].append({"name": p.stem, **{k: v for k, v in fm(p.read_text()).items() if k in ("title", "description")}})
    out["skills"] = []
    for p in sorted((C.ROOT / ".claude" / "skills").glob("*/SKILL.md")):
        meta = fm(p.read_text())
        out["skills"].append({"name": meta.get("name", p.parent.name), "description": meta.get("description", "")})
    conns = []
    games = []
    if C.DB_PATH.exists():
        con = C.db()
        for src, user in cfg["accounts"].items():
            r = con.execute("SELECT MAX(fetched_at) f FROM sync_state WHERE key LIKE ?", (f"{src}:%",)).fetchone()
            n = con.execute("SELECT COUNT(*) FROM games WHERE source=?", (src,)).fetchone()[0]
            conns.append({"name": {"chesscom": "Chess.com", "lichess": "Lichess"}.get(src, src), "user": user,
                          "ok": bool(r and r["f"]), "detail": f"{n} partidas · sync {ago(r['f'] if r else None)}"})
        t = con.execute("SELECT COUNT(*) n, SUM(analyzed=0) p FROM games").fetchone()
        out["pending"] = t["p"] or 0
        out["total"] = t["n"]
        for g in con.execute("SELECT * FROM games ORDER BY end_time DESC LIMIT 6"):
            games.append({"uuid": g["uuid"], "date": g["date"], "opp": g["opp_name"], "opp_rating": g["opp_rating"],
                          "result": g["my_result"], "color": g["my_color"], "tc": g["time_class"],
                          "opening": g["opening_family"] or g["eco"] or "?", "url": g["url"]})
    conns.append({"name": "Stockfish", "ok": out["stockfish"], "detail": "local" if out["stockfish"] else "falta: brew install stockfish"})
    conns.append({"name": "Explorador Lichess", "ok": out["explorer"], "detail": "con token" if out["explorer"] else "sin token (opcional)"})
    out["connections"] = conns
    out["games"] = games
    dec = C.ESTADO / "decisiones.md"
    out["decisions"] = [ln[2:].strip() for ln in dec.read_text().splitlines() if ln.startswith("- ")][-4:] if dec.exists() else []
    cur = C.ESTADO / "actual.md"
    nxt = ""
    if cur.exists():
        m = re.search(r"## Siguiente paso\s*\n(.*?)(\n## |\Z)", cur.read_text(), re.S)
        nxt = " ".join(m.group(1).split()) if m else ""
    out["next_step"] = nxt
    an = C.ESTADO / "anomalias.md"
    out["anomalies_open"] = an.read_text().count("Estado: pendiente") if an.exists() else 0
    out["train_active"] = (C.TRAIN_DIR / "active.json").exists()
    out["worker"] = WORKER.state
    out["models"] = MODELS
    out["claude_path"] = claude_bin()
    return out


# ───────────────────────── conversaciones ─────────────────────────

def conv_path(cid: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{12}", cid or ""):
        raise ValueError("id inválido")
    return CONV_DIR / f"{cid}.json"


def load_conv(cid):
    return json.loads(conv_path(cid).read_text())


def save_conv(c):
    CONV_DIR.mkdir(parents=True, exist_ok=True)
    conv_path(c["id"]).write_text(json.dumps(c, ensure_ascii=False, indent=1))


def list_convs():
    CONV_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for p in CONV_DIR.glob("*.json"):
        try:
            c = json.loads(p.read_text())
            out.append({"id": c["id"], "title": c["title"], "updated": c.get("updated", 0),
                        "cost": round(sum(m.get("cost") or 0 for m in c["messages"]), 4)})
        except Exception:
            continue
    return sorted(out, key=lambda c: -c["updated"])


def new_conv(title="Nueva conversación"):
    c = {"id": uuid.uuid4().hex[:12], "title": title, "created": time.time(), "updated": time.time(),
         "claude_session": None, "messages": []}
    save_conv(c)
    return c


def describe_tool(block) -> str:
    inp = block.get("input") or {}
    name = block.get("name", "")
    if name == "Bash":
        cmd = inp.get("command", "")
        return "Consultando: " + cmd.replace('"$CLAUDE_PROJECT_DIR"/', "")[:120]
    if name in ("Read", "Edit", "Write"):
        path = str(inp.get("file_path", "")).replace(str(C.ROOT) + "/", "")
        return {"Read": "Leyendo ", "Edit": "Actualizando ", "Write": "Escribiendo "}[name] + path
    if name == "Skill":
        return "Usando skill: " + str(inp.get("skill") or inp.get("name") or "")
    return f"Herramienta: {name}"


# ───────────────────────── tablero ─────────────────────────

def train_state() -> dict:
    t = T.load_session(required=False)
    if not t:
        return {"active": False}
    b = T.board_of(t)
    mine = T.my_turn(t, b)
    last = t["moves"][-1] if t["moves"] else None
    return {"active": True, "id": t["id"], "fen": b.fen(), "color": t["color"], "mode": t["mode"],
            "opening": t.get("opening"), "pgn": T.pgn_moves(t), "my_turn": mine and not b.is_game_over(),
            "game_over": b.is_game_over(), "result": b.result() if b.is_game_over() else None,
            "legal": [m.uci() for m in b.legal_moves] if mine else [],
            "last": {"uci": last["uci"], "san": last["san"], "by": last["by"]} if last else None,
            "suggestion": t.get("suggestion") if (t.get("suggestion") or {}).get("ply") == len(t["moves"]) + 1 else None,
            "check": b.is_check(), "moves": t["moves"]}



# ───────────────────────── configuración inicial (onboarding) ─────────────────────────

def has_config() -> bool:
    try:
        raw = json.loads(C.CFG_PATH.read_text())
        return any((v or "").strip() for v in (raw.get("accounts") or {}).values())
    except Exception:
        return False


def checks() -> dict:
    cfg = {}
    try:
        cfg = json.loads(C.CFG_PATH.read_text())
    except Exception:
        pass
    try:
        sf = C.find_stockfish(cfg)
    except SystemExit:
        sf = None
    return {"claude": claude_bin(), "stockfish": sf, "platform": sys.platform, "python": sys.version.split()[0],
            "explorer": bool(os.environ.get("LICHESS_TOKEN"))}


def setup_state() -> dict:
    raw = {}
    if C.CFG_PATH.exists():
        try:
            raw = json.loads(C.CFG_PATH.read_text())
        except Exception:
            raw = {}
    styles = []
    for p in sorted(C.COACHES_DIR.glob("*.md")):
        if p.stem.lower() != "readme":
            styles.append({"name": p.stem, **{k: v for k, v in fm(p.read_text()).items() if k in ("title", "description")}})
    return {"configured": has_config(), "accounts": raw.get("accounts", {}), "email": raw.get("contact_email", ""),
            "style": raw.get("coach_style", "profe"), "repertoire": raw.get("repertoire", {}), "rep_labels": C.REP_KEYS,
            "styles": styles, "token_set": bool(os.environ.get("LICHESS_TOKEN")), "checks": checks()}


def verify_user(src: str, user: str) -> str | None:
    """Devuelve un error legible si el usuario no existe (o None si está ok / no se pudo verificar)."""
    import requests

    url = {"chesscom": f"{C.CHESSCOM_API}/player/{user.lower()}", "lichess": f"{C.LICHESS_API}/user/{user}"}[src]
    try:
        r = requests.get(url, headers={"User-Agent": "chess-coach/1.0 (setup)"}, timeout=8)
    except Exception:
        return None  # sin red: no bloqueamos la configuración
    if r.status_code == 404:
        return f"El usuario '{user}' no existe en {'Chess.com' if src == 'chesscom' else 'Lichess'}."
    return None


def save_setup(b: dict) -> dict:
    acc = {k: (b.get(k) or "").strip() for k in ("chesscom", "lichess")}
    acc = {k: v for k, v in acc.items() if v}
    if not acc:
        return {"ok": False, "error": "Pon al menos una cuenta: Chess.com o Lichess."}
    for src, user in acc.items():
        err = verify_user(src, user)
        if err:
            return {"ok": False, "error": err}
    raw = json.loads(C.CFG_PATH.read_text()) if C.CFG_PATH.exists() else json.loads((C.ROOT / "config.example.json").read_text())
    first = not has_config()
    prev = {"accounts": raw.get("accounts"), "style": raw.get("coach_style"), "rep": raw.get("repertoire")}
    raw["accounts"] = acc
    raw["contact_email"] = (b.get("email") or "").strip()
    if b.get("style"):
        raw["coach_style"] = b["style"]
    rep = b.get("repertoire") or {}
    raw["repertoire"] = {k: [x.strip() for x in str(rep.get(k, "")).split(",") if x.strip()] for k in C.REP_KEYS}
    raw.setdefault("analysis", {})["threads"] = max(1, min(8, (os.cpu_count() or 4) - 2))
    raw.pop("lichess_token", None)
    C.CFG_PATH.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n")
    tok = b.get("lichess_token")
    if tok is not None and (tok.strip() or b.get("clear_token")):
        C.ENV_PATH.write_text(f"# Secretos locales: no se suben al repo\nLICHESS_TOKEN={tok.strip()}\n")
        C.ENV_PATH.chmod(0o600)
        os.environ["LICHESS_TOKEN"] = tok.strip()
    C.ESTADO.mkdir(exist_ok=True)
    tpl = C.ROOT / "templates" / "estado"
    for f in tpl.glob("*.md"):
        if not (C.ESTADO / f.name).exists():
            shutil.copy(f, C.ESTADO / f.name)
    C.ENTREGABLES.mkdir(exist_ok=True)
    reps = "; ".join(f"{C.REP_KEYS[k]}: {', '.join(v)}" for k, v in raw["repertoire"].items() if v) or "sin repertorio"
    if first:
        C.log_decision(f"Configuración inicial: estilo {raw['coach_style']}; repertorio {reps}", "instalación (UI)")
    else:
        if prev["style"] != raw["coach_style"]:
            C.log_decision(f"Estilo de coach: {raw['coach_style']} (antes: {prev['style']})", "configuración (UI)")
        if prev["rep"] != raw["repertoire"]:
            C.log_decision(f"Repertorio: {reps}", "configuración (UI)")
    WORKER.kick()
    return {"ok": True, "first": first}


def claude_test() -> dict:
    """Prueba mínima del chat (gasta muy pocos tokens)."""
    exe = claude_bin()
    if not exe:
        return {"ok": False, "error": "No encuentro Claude Code. Instálalo con: curl -fsSL https://claude.ai/install.sh | bash"}
    try:
        p = subprocess.run([exe, "-p", "Responde solo con la palabra: listo", "--output-format", "json"], cwd=C.ROOT,
                           capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Claude Code no respondió en 90 s."}
    try:
        j = json.loads(p.stdout or "{}")
    except ValueError:
        j = {}
    if p.returncode == 0 and not j.get("is_error"):
        return {"ok": True, "text": j.get("result", "ok"), "cost": j.get("total_cost_usd")}
    err = j.get("result") or (p.stderr or p.stdout or "").strip()[-400:] or f"código {p.returncode}"
    hint = ""
    if "auth" in err.lower() or "login" in err.lower() or "oauth" in err.lower():
        hint = " → Abre la Terminal, escribe `claude`, dentro `/login`, entra con tu cuenta y vuelve a probar."
    return {"ok": False, "error": err + hint}


# ───────────────────────── worker: sync + análisis en background ─────────────────────────

class Worker(threading.Thread):
    """Mantiene la base al día sin que nadie toque la terminal: sync al partir y cada 15 min, análisis por lotes."""

    def __init__(self):
        super().__init__(daemon=True)
        self.ev = threading.Event()
        self.state = {"phase": "idle", "msg": "", "last_sync": None, "last_error": None}

    def kick(self):
        self.ev.set()

    def _set(self, phase, msg=""):
        self.state.update(phase=phase, msg=msg)

    def run(self):
        while True:
            if has_config():
                try:
                    self._set("syncing", "Bajando tus partidas nuevas…")
                    ok, out = run_cli("sync", timeout=900)
                    self.state["last_sync"] = time.time()
                    self.state["last_error"] = None if ok else out[-300:]
                    for _ in range(6):  # hasta 60 partidas por ciclo, de las más nuevas a las más viejas
                        con = C.db()
                        pend = con.execute("SELECT COUNT(*) FROM games WHERE analyzed=0").fetchone()[0]
                        if not pend:
                            break
                        self._set("analyzing", f"Analizando con Stockfish… ({pend} pendientes)")
                        run_cli("analyze", "--limit", "10", timeout=900)
                        if self.ev.is_set():
                            break
                except Exception as e:
                    self.state["last_error"] = f"{e.__class__.__name__}: {e}"
                self._set("idle")
            self.ev.wait(15 * 60)
            self.ev.clear()


WORKER = Worker()



# ───────────────────────── jugada a jugada en el chat ─────────────────────────

VERD = {"best": "✅ la mejor", "good": "✅ buena", "inaccuracy": "🟡 imprecisión", "mistake": "🟠 error", "blunder": "🔴 error grave"}


def _last(t, by):
    return next((m for m in reversed(t["moves"]) if m["by"] == by), None)


def msg_play(t) -> str:
    m = _last(t, "me")
    if not m:
        return ""
    verdict = "👌 jugable" if m["class"] == "good" and m["drop"] >= 5 else VERD.get(m["class"], m["class"])
    s = f"Jugaste **{m['san']}**: {verdict}"
    if m["drop"] >= 1:
        s += f" (−{m['drop']:.0f}%)"
    if m["class"] not in ("best", "good") and m.get("best"):
        s += f" · mejor era **{m['best']}**"
    elif m.get("suggested") and m["suggested"] != m["san"]:
        s += f" · yo iba con {m['suggested']}"
    return s


def msg_rival(t) -> str:
    m = _last(t, "rival")
    if not m:
        return ""
    src = m.get("src", "")
    if src.startswith("Lichess"):
        pct = src.split(":")[-1].strip().split(" ")[0]
        src = f"la juega el {pct} de tu nivel"
    elif "rivales reales" in src:
        src = "te la han jugado tus rivales"
    elif "Stockfish" in src:
        src = "fuera de libro" if "fuera" in src else "motor"
    return f"Rival: **{m['san']}** · {src}"


def msg_suggest(t) -> str:
    s = t.get("suggestion") or {}
    if s.get("ply") != len(t["moves"]) + 1:
        return ""
    out = f"▶ **{s['san']}** — {s['why']}"
    if s.get("habit"):
        out += f"\n⚠️ {s['habit']}"
    if s.get("book") is False:
        out += "\n📖 Fuera de libro: ahora manda el plan."
    return out


def conv_append(cid, *msgs):
    if not cid:
        return
    with _lock:
        c = load_conv(cid)
        for role, text in msgs:
            if text:
                c["messages"].append({"role": role, "text": text, "ts": time.time(), "kind": "train"})
        if c["title"] == "Nueva conversación":
            t = T.load_session(required=False) or {}
            c["title"] = "♟ " + (t.get("opening") or "Jugada a jugada")
        c["updated"] = time.time()
        save_conv(c)


# ───────────────────────── HTTP ─────────────────────────

class H(BaseHTTPRequestHandler):
    server_version = "ChessCoachUI/1.0"

    def log_message(self, fmt, *args):  # silencioso
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def _guard(self) -> bool:
        """Solo localhost y sin peticiones cruzadas desde otras webs."""
        host = (self.headers.get("Host") or "").split(":")[0]
        origin = self.headers.get("Origin")
        if host not in ("127.0.0.1", "localhost") or (origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost")):
            self._json({"error": "solo local"}, 403)
            return False
        return True

    def do_GET(self):
        if not self._guard():
            return
        path = urlparse(self.path).path
        try:
            if path == "/api/context":
                return self._json(context())
            if path == "/api/setup":
                return self._json(setup_state())
            if path == "/api/status":
                return self._json(WORKER.state)
            if path == "/api/conversations":
                return self._json(list_convs())
            if path.startswith("/api/conversations/"):
                return self._json(load_conv(path.rsplit("/", 1)[1]))
            if path == "/api/train":
                return self._json(train_state())
            if path == "/api/tree":
                from urllib.parse import parse_qs

                q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
                mv = [x for x in (q.get("moves") or "").split(",") if x]
                rep = TR.tree_report(mv, q.get("color", "white"), q.get("tc") or None, int(q["days"]) if q.get("days") else None,
                                     engine=q.get("engine", "1") == "1")
                rep["chat_ctx"] = TR.summary_for_chat(rep)
                return self._json(rep)
            if path == "/api/game":
                ref = (urlparse(self.path).query.split("ref=")[-1] or "last")
                ok, out = run_cli("game", ref)
                return self._json({"ok": ok, "text": out})
        except FileNotFoundError:
            return self._json({"error": "no existe"}, 404)
        except SystemExit as e:
            return self._json({"error": f"config: {e}"}, 500)
        except Exception as e:
            return self._json({"error": f"{e.__class__.__name__}: {e}"}, 500)
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        f = (WEB / rel).resolve()
        if WEB.resolve() not in f.parents or not f.is_file():
            return self._json({"error": "no existe"}, 404)
        data = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(f.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if not self._guard():
            return
        path = urlparse(self.path).path
        try:
            b = self._body()
            if path == "/api/setup":
                return self._json(save_setup(b))
            if path == "/api/claude-test":
                return self._json(claude_test())
            if path == "/api/chat":
                return self._chat(b)
            if path == "/api/conversations":
                return self._json(new_conv())
            if path == "/api/feedback":
                return self._json(self._feedback(b))
            if path == "/api/sync":
                WORKER.kick()
                return self._json({"ok": True, "text": "Sincronizando en background"})
            if path == "/api/style":
                ok, out = run_cli("style", "set", b["name"], "--why", b.get("why") or "elegido en la UI")
                return self._json({"ok": ok, "text": out})
            if path == "/api/repertoire":
                ok, out = run_cli("repertoire", "set", b["key"], b.get("value") or "", "--why", b.get("why") or "fijado en la UI")
                return self._json({"ok": ok, "text": out})
            if path.startswith("/api/train/"):
                return self._json(self._train(path.rsplit("/", 1)[1], b))
        except Exception as e:
            return self._json({"error": f"{e.__class__.__name__}: {e}"}, 500)
        self._json({"error": "ruta desconocida"}, 404)

    # ── tablero: todo vía ./coach (mismos efectos que en terminal, sin LLM) ──
    def _train(self, action, b):
        """Jugada a jugada sin LLM: todo vía ./coach (eventos, bitácora, entregables) y respuesta en 1–3 líneas."""
        cid = b.get("conv")
        if action == "start":
            args = ["train", "start", "--color", b.get("color", "white"), "--mode", b.get("mode", "recomendar"), "--force"]
            if b.get("opening"):
                args += ["--opening", b["opening"]]
            if b.get("moves"):
                args += ["--moves", b["moves"]]
            ok, out = run_cli(*args)
            if not ok:
                return {"ok": False, "text": out.splitlines()[-1] if out else "error", "state": train_state()}
            t = T.load_session()
            lines = [f"♟ **{t.get('opening') or 'Partida libre'}** con {'blancas' if t['color'] == 'white' else 'negras'}"
                     f" · modo {t['mode']}. Mueve en el tablero o escribe la jugada (ej. Nf3). «fin» para cerrar."]
            st = train_state()
            if st["active"] and not st["my_turn"] and not st["game_over"]:
                run_cli("train", "rival")
                lines.append(msg_rival(T.load_session()))
            st = train_state()
            if st["my_turn"] and t["mode"] == "recomendar":
                run_cli("train", "suggest", timeout=90)
                lines.append(msg_suggest(T.load_session()))
            text = "\n".join(x for x in lines if x)
            conv_append(cid, ("assistant", text))
            return {"ok": True, "text": text, "state": train_state()}
        if action == "turn":
            move = b["move"]
            ok, out = run_cli("train", "play", move)
            if not ok:
                err = next((ln for ln in reversed(out.splitlines()) if ln.strip()), "jugada inválida")
                return {"ok": False, "text": err.replace("ERROR: ", ""), "state": train_state()}
            t = T.load_session()
            lines = [msg_play(t)]
            st = train_state()
            if not st["game_over"] and not st["my_turn"]:
                run_cli("train", "rival")
                lines.append(msg_rival(T.load_session()))
            st = train_state()
            if st["game_over"]:
                lines.append(f"Fin: {st['result']}. Escribe «fin» para guardar el informe.")
            elif st["my_turn"] and t["mode"] == "recomendar":
                run_cli("train", "suggest", timeout=90)
                lines.append(msg_suggest(T.load_session()))
            elif t["mode"] == "guia" and (_last(t, "me") or {}).get("drop", 0) >= 10:
                lines.append("👀 Ojo con esa. ¿Quieres que te explique? Escríbeme «¿por qué?».")
            text = "\n".join(x for x in lines if x)
            played = _last(T.load_session(), "me")
            conv_append(cid, ("user", f"Jugué {played['san'] if played else move}"), ("assistant", text))
            return {"ok": True, "text": text, "state": train_state()}
        if action == "suggest":
            run_cli("train", "suggest", timeout=90)
            text = msg_suggest(T.load_session()) or "No tengo sugerencia para esta posición."
            conv_append(cid, ("user", "Pista"), ("assistant", text))
            return {"ok": True, "text": text, "state": train_state()}
        if action == "end":
            t = T.load_session(required=False)
            if not t:
                return {"ok": False, "text": "No hay sesión activa.", "state": train_state()}
            mine = [m for m in t["moves"] if m["by"] == "me"]
            bad = [m for m in mine if m["class"] in ("mistake", "blunder")]
            avg = sum(m["drop"] for m in mine) / len(mine) if mine else 0
            ok, out = run_cli("train", "end")
            f = next((ln.split("Guardado en ")[1].split(" y")[0] for ln in out.splitlines() if "Guardado en" in ln), "entregables/")
            worst = max(mine, key=lambda m: m["drop"]) if mine else None
            text = (f"⏹ Sesión cerrada: {len(mine)} jugadas tuyas · caída media {avg:.0f}% · {len(bad)} errores."
                    + (f"\nLa que más costó: **{worst['san']}** (mejor {worst['best']})." if worst and worst["drop"] >= 10 else "")
                    + f"\nInforme: `{f}`")
            conv_append(cid, ("user", "Fin"), ("assistant", text))
            return {"ok": ok, "text": text, "state": train_state()}
        return {"ok": False, "text": "acción desconocida", "state": train_state()}

    def _feedback(self, b):
        c = load_conv(b["conv"])
        i = int(b["index"])
        m = c["messages"][i]
        m["feedback"] = {"value": b["value"], "note": b.get("note", ""), "ts": time.time()}
        save_conv(c)
        C.ESTADO.mkdir(exist_ok=True)
        p = C.ESTADO / "feedback.md"
        if not p.exists():
            p.write_text("# Feedback del alumno\n<!-- Lo escribe la UI. El coach lo usa para mejorar; cambios de reglas se acuerdan con el alumno. -->\n")
        prev = c["messages"][i - 1]["text"][:120].replace("\n", " ") if i > 0 else ""
        with p.open("a") as f:
            tag = "✅ me sirvió" if b["value"] == "up" else "✏️ hay que corregir"
            f.write(f"- {datetime.now().strftime('%Y-%m-%d %H:%M')} · {tag} · pedido: «{prev}»"
                    + (f" · nota: {b['note']}" if b.get("note") else "") + "\n")
        return {"ok": True}

    # ── chat por SSE ──
    def _sse(self, obj):
        self.wfile.write(f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode())
        self.wfile.flush()

    def _chat(self, b):
        text = (b.get("text") or "").strip()
        if not text:
            return self._json({"error": "mensaje vacío"}, 400)
        cid = b.get("conv")
        c = load_conv(cid) if cid else new_conv()
        if not c["messages"]:
            c["title"] = text[:70]
        c["messages"].append({"role": "user", "text": text, "ts": time.time()})
        c["updated"] = time.time()
        save_conv(c)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self._sse({"type": "conv", "conv": c["id"], "title": c["title"]})
        exe = claude_bin()
        if not exe:
            msg = "No encuentro `claude` (Claude Code). Instálalo o define CHESS_COACH_CLAUDE con su ruta."
            c["messages"].append({"role": "assistant", "text": msg, "ts": time.time(), "error": True})
            save_conv(c)
            return self._sse({"type": "done", "text": msg, "error": True})
        model = b.get("model") if b.get("model") in MODELS else "sonnet"
        prompt = text
        tr = train_state()
        ctx = (b.get("ctx") or "")[:1500]
        if ctx.startswith("[Contexto UI: árbol"):
            prompt = ctx + "\n\n" + text
        elif tr.get("active"):
            sug = tr.get("suggestion") or {}
            prompt = (f"[Contexto UI: sesión jugada a jugada activa · {tr.get('opening') or ''} · juega {tr['color']} · "
                      f"jugadas: {tr['pgn'] or '(inicio)'} · FEN: {tr['fen']}"
                      + (f" · sugerencia vigente: {sug.get('san')} ({sug.get('why')})" if sug else "") + "]\n\n" + text)
        cmd = [exe, "-p", "--output-format", "stream-json", "--verbose", "--model", model, "--max-turns", "12",
               "--append-system-prompt", UI_PROMPT]
        if c.get("claude_session"):
            cmd += ["--resume", c["claude_session"]]
        env = {**os.environ, "CHESS_COACH_UI": "1"}
        t0 = time.time()
        parts, final, cost, sid, err = [], None, None, c.get("claude_session"), None
        try:
            proc = subprocess.Popen(cmd, cwd=C.ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, env=env, bufsize=1)
            proc.stdin.write(prompt)
            proc.stdin.close()
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                et = ev.get("type")
                if et == "system" and ev.get("session_id"):
                    sid = ev["session_id"]
                elif et == "assistant":
                    for blk in (ev.get("message") or {}).get("content", []):
                        if blk.get("type") == "text" and blk.get("text", "").strip():
                            parts.append(blk["text"])
                            self._sse({"type": "text", "text": blk["text"]})
                        elif blk.get("type") == "tool_use":
                            self._sse({"type": "status", "text": describe_tool(blk)})
                elif et == "result":
                    final = ev.get("result")
                    cost = ev.get("total_cost_usd")
                    sid = ev.get("session_id") or sid
                    if ev.get("is_error"):
                        err = final or ev.get("subtype")
            proc.wait(timeout=30)
            if proc.returncode and not final:
                err = (proc.stderr.read() or "").strip()[-600:] or f"claude terminó con código {proc.returncode}"
        except BrokenPipeError:
            return
        except Exception as e:
            err = f"{e.__class__.__name__}: {e}"
        answer = final if final else ("\n\n".join(parts) if parts else "")
        if err and not answer:
            answer = f"⚠️ Error al llamar a Claude Code: {err}"
        msg = {"role": "assistant", "text": answer, "ts": time.time(), "cost": cost, "secs": round(time.time() - t0, 1)}
        if err:
            msg["error"] = True
        with _lock:
            c = load_conv(c["id"])
            c["messages"].append(msg)
            c["claude_session"] = sid
            c["updated"] = time.time()
            save_conv(c)
        try:
            self._sse({"type": "done", "text": answer, "cost": cost, "secs": msg["secs"], "index": len(c["messages"]) - 1,
                       "error": bool(err)})
        except BrokenPipeError:
            pass


def cmd_ui(a):
    C.load_env()
    url = f"http://127.0.0.1:{a.port}/"
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    except OSError:
        print(f"La UI ya está corriendo en {url} (¿servicio activo?). La abro.")
        if not a.no_open:
            webbrowser.open(url)
            return
        time.sleep(15)  # bajo launchd: no reintentar en loop apretado
        sys.exit(1)
    print(f"♞ Chess Coach UI en {url}  (solo este equipo · Ctrl-C para cerrar)")
    if not has_config():
        print("Primera vez: configura tus cuentas en el navegador.")
    WORKER.start()
    if not claude_bin():
        print("aviso: no encuentro `claude`; el chat no va a responder (el tablero sí funciona).")
    if not a.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nchao 👋")
