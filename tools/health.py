"""
health.py — verificación y observabilidad (pieza 9 del harness).

- events.jsonl : un evento por comando (sin secretos)
- doctor       : chequeo de salud de todo el harness
- anomalies    : "casos imposibles" en los datos → estado/anomalias.md (formato ANO-xxx)
- hook-stop    : no deja cerrar una sesión con coaching sin actualizar la bitácora
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

import core as C

COACHING_CMDS = {"game", "positions", "check", "move-stats", "train"}


# ───────────────────────── eventos ─────────────────────────

def session_id() -> str | None:
    try:
        return json.loads(C.SESSION_PATH.read_text()).get("session_id")
    except Exception:
        return None


def log_event(cmd: str, args: dict, ok: bool, ms: int, ts: float, err: str | None = None):
    safe = {k: v for k, v in args.items() if k not in ("f",) and not callable(v) and v not in (None, False, [])}
    ev = {"ts": datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds"), "t": round(ts, 3),
          "cmd": cmd, "args": safe, "ok": ok, "ms": ms, "session": session_id()}
    if err:
        ev["err"] = err[:200]
    try:
        C.DATA.mkdir(exist_ok=True)
        with C.EVENTS_PATH.open("a") as f:
            f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def read_events(since: float = 0) -> list[dict]:
    if not C.EVENTS_PATH.exists():
        return []
    out = []
    for line in C.EVENTS_PATH.read_text().splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("t", 0) >= since:
            out.append(e)
    return out


def cmd_events(a):
    evs = read_events(time.time() - a.days * 86400)
    if not evs:
        print("Sin eventos.")
        return
    by = {}
    for e in evs:
        d = by.setdefault(e["cmd"], {"n": 0, "err": 0, "ms": 0})
        d["n"] += 1
        d["err"] += 0 if e["ok"] else 1
        d["ms"] += e["ms"]
    print(f"# Eventos últimos {a.days} días ({len(evs)})")
    for k, d in sorted(by.items(), key=lambda kv: -kv[1]["n"]):
        print(f"- {k}: {d['n']}× · errores {d['err']} · {d['ms'] / d['n'] / 1000:.1f}s promedio")
    errs = [e for e in evs if not e["ok"]][-5:]
    if errs:
        print("Últimos errores:")
        for e in errs:
            print(f"  · {e['ts']} {e['cmd']}: {e.get('err', '')}")


# ───────────────────────── hook Stop ─────────────────────────

def cmd_hook_stop(a):
    """Stop hook de Claude Code. Bloquea UNA vez si hubo coaching y no se actualizó la bitácora."""
    try:
        inp = json.loads(sys.stdin.read() or "{}")
    except Exception:
        inp = {}
    if inp.get("stop_hook_active"):
        return  # ya bloqueamos una vez; no generar loops
    try:
        started = json.loads(C.SESSION_PATH.read_text()).get("started", 0)
    except Exception:
        started = 0
    coaching = [e for e in read_events(started) if e["cmd"] in COACHING_CMDS and e["ok"]]
    if not coaching:
        return
    last = max(e["t"] for e in coaching)
    bit = C.ESTADO / "bitacora.md"
    if bit.exists() and bit.stat().st_mtime >= last:
        return
    print(json.dumps({
        "decision": "block",
        "reason": ("Antes de cerrar: hubo coaching en esta sesión y estado/bitacora.md no se actualizó. "
                   "Agrega 1 línea a estado/bitacora.md (fecha, qué se vio → tarea). Si aplica: actualiza "
                   "estado/actual.md (siguiente paso), estado/errores-recurrentes.md (patrón visto ≥2 veces) y guarda "
                   "el informe en entregables/AAAA-MM-DD-<tipo>-<tema>.md. Luego termina."),
    }, ensure_ascii=False))


# ───────────────────────── anomalías ─────────────────────────

def detect(con) -> list[dict]:
    """Casos imposibles o sospechosos. Son señales a investigar, no fallos confirmados."""
    out = []
    now = int(time.time())

    for g in con.execute("SELECT uuid, url, my_accuracy, opp_accuracy FROM games WHERE my_accuracy>100 OR my_accuracy<0 "
                         "OR opp_accuracy>100 OR opp_accuracy<0"):
        out.append(dict(key=f"acc:{g['uuid']}", title="Precisión fuera de 0–100", source=g["url"],
                        observed=f"tú {g['my_accuracy']} / rival {g['opp_accuracy']}", rule="La precisión es un % (0–100)",
                        hyp="cambio de formato en la API; parseo equivocado de campo", impact="estadísticas de precisión"))

    for g in con.execute("SELECT uuid, url, end_time FROM games WHERE end_time > ?", (now + 3600,)):
        out.append(dict(key=f"future:{g['uuid']}", title="Partida con fecha futura", source=g["url"],
                        observed=f"end_time={g['end_time']}", rule="Una partida terminada no puede ser futura",
                        hyp="ms vs s en el timestamp; zona horaria", impact="filtros por fecha y 'nuevas desde la última sesión'"))

    prev = {}
    for g in con.execute("SELECT uuid, url, source, time_class, my_rating, end_time FROM games WHERE my_rating IS NOT NULL "
                         "AND source!='pgn' ORDER BY source, time_class, end_time"):
        k = (g["source"], g["time_class"])
        p = prev.get(k)
        if p and g["end_time"] - p["end_time"] < 86400 and abs(g["my_rating"] - p["my_rating"]) > 300:
            out.append(dict(key=f"rjump:{g['uuid']}", title=f"Salto de rating >300 en <24h ({k[0]}/{k[1]})", source=g["url"],
                            observed=f"{p['my_rating']} → {g['my_rating']}", rule="Una partida mueve el rating ~5–30 pts",
                            hyp="rating provisional; cuenta compartida; dos cuentas mezcladas", impact="tendencia de rating"))
        prev[k] = g

    seen = {}
    for g in con.execute("SELECT uuid, url, source, pgn FROM games WHERE source IN ('chesscom','lichess','pgn')"):
        body = C.re.sub(r"\[.*?\]|\{.*?\}|\d+\.+|\s+", " ", g["pgn"] or "", flags=C.re.S).split()
        if len(body) < 20:
            continue
        sig = " ".join(body[:60])
        if sig in seen and seen[sig][1] != g["source"]:
            out.append(dict(key=f"dup:{seen[sig][0]}:{g['uuid']}", title="La misma partida en dos fuentes", source=f"{seen[sig][0]} y {g['uuid']}",
                            observed="mismas primeras 60 jugadas", rule="Una partida se cuenta una vez",
                            hyp="partida OTB importada que también se subió a la web; coincidencia de teoría larga",
                            impact="las estadísticas cuentan doble"))
        seen.setdefault(sig, (g["uuid"], g["source"]))

    for g in con.execute("SELECT g.uuid, g.url, g.n_plies, COUNT(m.ply) n FROM games g LEFT JOIN moves m ON m.game_uuid=g.uuid "
                         "WHERE g.analyzed=1 GROUP BY g.uuid HAVING n != g.n_plies"):
        out.append(dict(key=f"plies:{g['uuid']}", title="Análisis incompleto", source=g["url"],
                        observed=f"n_plies={g['n_plies']} pero {g['n']} jugadas analizadas", rule="Cada jugada tiene su fila",
                        hyp="análisis interrumpido; PGN con variantes", impact="errores por fase subcontados"))

    for g in con.execute("SELECT uuid, url FROM games WHERE analyzed=-1"):
        out.append(dict(key=f"fail:{g['uuid']}", title="Partida que Stockfish no pudo analizar", source=g["url"],
                        observed="analyzed=-1", rule="Toda partida estándar es analizable",
                        hyp="PGN corrupto; posición inicial no estándar", impact="queda fuera de los patrones"))

    for g in con.execute("SELECT g.uuid, g.url, g.my_result, g.my_color, m.eval_after FROM games g JOIN moves m "
                         "ON m.game_uuid=g.uuid AND m.ply=g.n_plies WHERE g.analyzed=1 AND g.result_detail='checkmated'"):
        mate_for_me = (g["eval_after"] >= C.MATE) if g["my_color"] == "white" else (g["eval_after"] <= -C.MATE)
        mate_vs_me = (g["eval_after"] <= -C.MATE) if g["my_color"] == "white" else (g["eval_after"] >= C.MATE)
        ok = (g["my_result"] == "win" and mate_for_me) or (g["my_result"] == "loss" and mate_vs_me)
        if not ok:
            out.append(dict(key=f"mate:{g['uuid']}", title="Dice jaque mate pero la posición final no lo es", source=g["url"],
                            observed=f"resultado {g['my_result']} por checkmated, eval final {g['eval_after']}",
                            rule="Si terminó en mate, la última posición es mate", hyp="mapeo de resultado; PGN truncado",
                            impact="'cómo pierdes' mal clasificado"))

    months = {}
    for r in con.execute("SELECT strftime('%Y-%m', date) ym, COUNT(*) n FROM games WHERE source!='pgn' GROUP BY 1"):
        months[r["ym"]] = r["n"]
    if len(months) >= 3:
        keys = sorted(months)
        y, m = map(int, keys[0].split("-"))
        allm = []
        while f"{y:04d}-{m:02d}" <= keys[-1]:
            allm.append(f"{y:04d}-{m:02d}")
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        for i in range(1, len(allm) - 1):
            if allm[i] not in months and months.get(allm[i - 1], 0) >= 10 and months.get(allm[i + 1], 0) >= 10:
                out.append(dict(key=f"gap:{allm[i]}", title=f"Mes sin partidas entre meses activos ({allm[i]})", source="sync",
                                observed=f"{allm[i - 1]}: {months[allm[i - 1]]} · {allm[i]}: 0 · {allm[i + 1]}: {months[allm[i + 1]]}",
                                rule="Si jugaste antes y después, debería haber partidas", hyp="vacaciones reales; archivo mensual que falló al bajar",
                                impact="historial incompleto"))
    return out


def cmd_anomalies(a):
    con = C.db()
    found = detect(con)
    idx_path = C.DATA / "anomalies.json"
    idx = json.loads(idx_path.read_text()) if idx_path.exists() else {}
    new = [x for x in found if x["key"] not in idx]
    C.ESTADO.mkdir(exist_ok=True)
    md = C.ESTADO / "anomalias.md"
    if not md.exists():
        md.write_text("# Registro de casos imposibles\n<!-- Señales a investigar, no fallos confirmados. El coach actualiza Estado y Resultado. -->\n")
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    with md.open("a") as f:
        for x in new:
            n = len(idx) + 1
            idx[x["key"]] = f"ANO-{n:03d}"
            f.write(f"\n### ANO-{n:03d} · {x['title']}\n- Detectado: {stamp}\n- Fuente: {x['source']}\n- Observado: {x['observed']}\n"
                    f"- Regla esperada: {x['rule']}\n- Hipótesis alternativas: {x['hyp']}\n- Impacto posible: {x['impact']}\n"
                    f"- Validación siguiente: revisar la partida en la web y el dato crudo\n- Estado: pendiente\n- Resultado y fecha: —\n")
    idx_path.write_text(json.dumps(idx, indent=1))
    print(f"Anomalías: {len(found)} detectadas · {len(new)} nuevas registradas en estado/anomalias.md")
    for x in new[:10]:
        print(f"- {idx[x['key']]} {x['title']} — {x['observed']}")
    return len(new)


# ───────────────────────── doctor ─────────────────────────

def cmd_doctor(a):
    ok_all = True

    def check(name, ok, detail=""):
        nonlocal ok_all
        ok_all &= bool(ok) or ok is None
        mark = "✅" if ok else ("➖" if ok is None else "❌")
        print(f"{mark} {name}" + (f" — {detail}" if detail else ""))

    try:
        cfg = C.load_cfg()
        check("config.json", True, ", ".join(f"{k}={v}" for k, v in cfg["accounts"].items()))
    except SystemExit:
        check("config.json", False, "falta: configúralo en http://127.0.0.1:4520 (⚙ Configuración)")
        return
    style = cfg.get("coach_style", "profe")
    check("estilo de coach", (C.COACHES_DIR / f"{style}.md").exists(), style)
    rep = {k: v for k, v in (cfg.get("repertoire") or {}).items() if v}
    check("repertorio declarado", True if rep else None, "; ".join(f"{k}: {', '.join(v)}" for k, v in rep.items()) or "vacío (opcional)")

    import signal

    def _timeout(*_):
        raise TimeoutError("Stockfish no respondió en 20 s")

    old_handler = signal.signal(signal.SIGALRM, _timeout)
    signal.alarm(20)
    eng = None
    try:
        path = C.find_stockfish(cfg)
        import chess
        import chess.engine

        t0 = time.time()
        eng = C.get_engine(cfg)
        eng.analyse(chess.Board(), chess.engine.Limit(time=0.1))
        check("Stockfish", True, f"{path} ({time.time() - t0:.1f}s)")
    except SystemExit:
        check("Stockfish", False, "no encontrado → brew install stockfish")
    except Exception as e:
        check("Stockfish", False, (str(e) or e.__class__.__name__)[:100])
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)
        if eng:
            try:
                eng.close()
            except Exception:
                pass

    check("token Lichess (explorador)", True if os.environ.get("LICHESS_TOKEN") else None,
          "ok" if os.environ.get("LICHESS_TOKEN") else "sin token: jugada a jugada funciona sin explorador (ver README)")

    if not C.DB_PATH.exists():
        check("base de datos", False, "no existe → ./coach sync")
    else:
        con = C.db()
        qc = con.execute("PRAGMA quick_check").fetchone()[0]
        t = con.execute("SELECT COUNT(*) n, SUM(analyzed=1) an, SUM(analyzed=0) p, SUM(analyzed=-1) f FROM games").fetchone()
        check("base de datos", qc == "ok", f"{t['n']} partidas · {t['an'] or 0} analizadas · {t['p'] or 0} pendientes · {t['f'] or 0} fallidas")
        for src in cfg["accounts"]:
            r = con.execute("SELECT MAX(fetched_at) f FROM sync_state WHERE key LIKE ?", (f"{src}:%",)).fetchone()
            last = r["f"] if r else None
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() / 3600 if last else None
            check(f"sync {src}", last is not None and age < 72, f"último: {last or 'nunca'}" + (f" ({age:.0f}h)" if age is not None else ""))

    if a.net:
        import requests

        for name, url in (("red Chess.com", "https://api.chess.com/pub/player/hikaru"), ("red Lichess", "https://lichess.org/api/status")):
            try:
                r = requests.get(url, headers={"User-Agent": C.user_agent(cfg)}, timeout=6)
                check(name, r.status_code < 500, f"HTTP {r.status_code}")
            except Exception as e:
                check(name, False, e.__class__.__name__)

    for f in ("actual.md", "decisiones.md", "bitacora.md", "perfil.md", "errores-recurrentes.md", "plan.md", "objetivos.md"):
        if not (C.ESTADO / f).exists():
            check("estado/", False, f"falta {f} → abre la UI y guarda la configuración")
            break
    else:
        check("estado/", True)

    sj = C.ROOT / ".claude" / "settings.json"
    try:
        hooks = json.loads(sj.read_text()).get("hooks", {})
        check("hooks Claude Code", "SessionStart" in hooks and "Stop" in hooks, ", ".join(hooks))
    except Exception:
        check("hooks Claude Code", False, "no pude leer .claude/settings.json")

    evs = read_events(time.time() - 7 * 86400)
    errs = sum(1 for e in evs if not e["ok"])
    check("eventos 7 días", errs == 0 or None, f"{len(evs)} comandos · {errs} con error (./coach events)")

    if C.DB_PATH.exists():
        n = len(detect(C.db()))
        check("anomalías", n == 0 or None, f"{n} detectadas (./coach anomalies las registra)")
    print("\nResultado: " + ("TODO OK" if ok_all else "hay cosas que arreglar (❌)"))
    if not ok_all:
        sys.exit(1)
