"""Fixtures: cada test corre en un proyecto temporal aislado (nunca toca tus datos reales)."""
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(REPO / "tools"))

import core  # noqa: E402

PATHS = ["DATA", "DB_PATH", "STATE_PATH", "CFG_PATH", "KB_DIR", "ESTADO", "ENTREGABLES", "EVENTS_PATH",
         "SESSION_PATH", "TRAIN_DIR", "ENV_PATH", "COACHES_DIR"]


def stockfish_path():
    for p in (os.environ.get("STOCKFISH_PATH"), shutil.which("stockfish"), "/opt/homebrew/bin/stockfish",
              "/usr/local/bin/stockfish", "/usr/games/stockfish"):
        if p and os.path.exists(p):
            return p
    return None


@pytest.fixture
def proj(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    for d in ("knowledge", "coaches", ".claude"):
        shutil.copytree(REPO / d, root / d)
    (root / "estado").mkdir()
    for f in (REPO / "templates" / "estado").glob("*.md"):
        shutil.copy(f, root / "estado" / f.name)
    cfg = json.loads((REPO / "config.example.json").read_text())
    cfg["accounts"] = {"chesscom": "tester", "lichess": "tester"}
    cfg["stockfish_path"] = stockfish_path() or ""
    cfg["analysis"].update(seconds_per_move=0.03, threads=1, hash_mb=16, max_games_per_run=10)
    (root / "config.json").write_text(json.dumps(cfg))
    new = {
        "ROOT": root, "DATA": root / "data", "DB_PATH": root / "data" / "games.db", "STATE_PATH": root / "data" / "state.json",
        "CFG_PATH": root / "config.json", "KB_DIR": root / "knowledge", "ESTADO": root / "estado",
        "ENTREGABLES": root / "entregables", "EVENTS_PATH": root / "data" / "events.jsonl",
        "SESSION_PATH": root / "data" / "session.json", "TRAIN_DIR": root / "data" / "train",
        "ENV_PATH": root / ".env", "COACHES_DIR": root / "coaches",
    }
    for k, v in new.items():
        monkeypatch.setattr(core, k, v)
    monkeypatch.delenv("LICHESS_TOKEN", raising=False)
    # el análisis en background del brief lanzaría el CLI real sobre el repo: en tests, no
    monkeypatch.setattr(core, "spawn_background", lambda *a, **k: None)
    return root


class FakeResp:
    def __init__(self, status=200, data=None, lines=None, headers=None):
        self.status_code, self._data, self._lines, self.headers = status, data, lines or [], headers or {}

    def json(self):
        return self._data

    def iter_lines(self):
        return iter(self._lines)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture
def fake_api(monkeypatch):
    """Simula Chess.com (con ETag/304) y Lichess (ndjson con filtro since)."""
    calls = []
    cc = json.loads((FIX / "chesscom_archive.json").read_text())
    li = [line.encode() for line in (FIX / "lichess_games.ndjson").read_text().splitlines() if line.strip()]
    arch = "https://api.chess.com/pub/player/tester/games/2025/11"

    def http_get(url, cfg, *, etag=None, params=None, accept=None, timeout=20, stream=False):
        calls.append((url, etag, dict(params or {})))
        if url.endswith("/games/archives"):
            return FakeResp(data={"archives": [arch]})
        if url == arch:
            return FakeResp(304) if etag == "E1" else FakeResp(data=cc, headers={"ETag": "E1"})
        if "lichess.org/api/games/user" in url:
            since = (params or {}).get("since", 0)
            return FakeResp(lines=[x for x in li if json.loads(x)["createdAt"] >= since])
        return FakeResp(404)

    monkeypatch.setattr(core, "http_get", http_get)
    return calls


needs_engine = pytest.mark.skipif(stockfish_path() is None, reason="Stockfish no instalado")
