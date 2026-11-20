#!/usr/bin/env bash
# ♞ Chess Coach — instalador de una línea (macOS y Linux; en Windows usa WSL)
#
#   curl -fsSL https://raw.githubusercontent.com/andresdelacruz/chess-coach/main/install.sh | bash
#
# Instala o actualiza en ~/chess-coach sin tocar tus datos (config, partidas, estado), deja la UI
# corriendo en http://127.0.0.1:4520 y la abre. Las cuentas se configuran en el navegador.
# Gratis: Stockfish, APIs de Chess.com/Lichess. El chat usa tu cuenta de Claude (Claude Code).
set -euo pipefail

REPO="${CHESS_COACH_REPO:-andresdelacruz/chess-coach}"
BRANCH="${CHESS_COACH_BRANCH:-main}"
DEST="${CHESS_COACH_DIR:-$HOME/chess-coach}"
URL="http://127.0.0.1:4520/"
OS="$(uname -s)"
say() { printf "\n\033[1m▶ %s\033[0m\n" "$*"; }
die() { printf "\n\033[31m✗ %s\033[0m\n" "$*"; exit 1; }

# ── 1. Código ─────────────────────────────────────────────
SELF="${BASH_SOURCE[0]:-}"
if [ -n "$SELF" ] && [ -f "$(dirname "$SELF")/tools/coach.py" ]; then
  DEST="$(cd "$(dirname "$SELF")" && pwd)"          # ./install.sh desde una copia local
  say "Usando la copia local en $DEST"
else
  say "Bajando Chess Coach ($REPO) a $DEST"
  TMP="$(mktemp -d)"
  curl -fsSL "https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH" | tar -xz -C "$TMP" \
    || die "No pude bajar el código de github.com/$REPO"
  mkdir -p "$DEST"
  # el paquete no trae datos personales (están en .gitignore): copiar encima es seguro
  cp -R "$TMP"/*/. "$DEST"/
  rm -rf "$TMP"
fi
cd "$DEST"
case "$DEST" in "$HOME/Documents"*|"$HOME/Desktop"*)
  echo "aviso: en ~/Documents o ~/Desktop macOS/iCloud pueden bloquear el servicio. Recomendado: ~/chess-coach";;
esac

# ── 2. Stockfish (gratis) ─────────────────────────────────
say "Stockfish"
if command -v stockfish >/dev/null 2>&1 || [ -x /opt/homebrew/bin/stockfish ] || [ -x /usr/local/bin/stockfish ] || [ -x /usr/games/stockfish ]; then
  echo "ok"
elif [ "$OS" = "Darwin" ]; then
  BREW="$(command -v brew || ls /opt/homebrew/bin/brew /usr/local/bin/brew 2>/dev/null | head -1 || true)"
  [ -n "$BREW" ] || die "Necesitas Homebrew (gratis): https://brew.sh  → instálalo y vuelve a pegar el comando."
  "$BREW" install stockfish
elif command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update -q && sudo apt-get install -y stockfish
else
  die "Instala Stockfish (https://stockfishchess.org/download/) y vuelve a correr esto."
fi

# ── 3. Python (entorno propio en .venv) ───────────────────
say "Python"
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then PY="$(command -v "$c")"; break; fi
done
[ -n "$PY" ] || die "Necesitas Python 3.9+ (Mac: brew install python · Linux: sudo apt install python3-venv)"
if ! .venv/bin/python -c 'import chess, requests' 2>/dev/null; then
  rm -rf .venv
  "$PY" -m venv .venv || die "No pude crear el entorno (Linux: sudo apt install python3-venv)"
  .venv/bin/pip install -q --disable-pip-version-check -r requirements.txt
fi
chmod +x coach tools/coach.py setup.sh install.sh 2>/dev/null || true
echo "ok ($(.venv/bin/python --version))"

# ── 4. Claude Code (para el chat con el coach) ────────────
say "Claude Code"
CLAUDE="$(command -v claude || ls "$HOME/.local/bin/claude" "$HOME/.claude/local/claude" 2>/dev/null | head -1 || true)"
if [ -n "$CLAUDE" ]; then
  echo "ok ($CLAUDE)"
elif [ "${CHESS_COACH_SKIP_CLAUDE:-0}" = "1" ]; then
  echo "omitido (el tablero funciona igual; el chat no)"
else
  echo "No está instalado: lo instalo con el instalador oficial (gratis; el uso del chat va por tu cuenta de Claude)."
  curl -fsSL https://claude.ai/install.sh | bash || echo "aviso: no pude instalar Claude Code; el tablero funciona igual."
  export PATH="$HOME/.local/bin:$PATH"
fi

# ── 5. Servicio web ───────────────────────────────────────
say "Levantando la UI en $URL"
mkdir -p data
if [ "$OS" = "Darwin" ]; then
  ./coach service on                      # LaunchAgent: parte al iniciar sesión y se reinicia solo
else
  pkill -f "tools/coach.py ui" 2>/dev/null || true
  nohup .venv/bin/python tools/coach.py ui --no-open > data/ui.log 2>&1 &
  echo "(Linux: corre mientras tengas la sesión abierta; para dejarlo fijo usa un servicio systemd --user)"
fi
for _ in $(seq 1 20); do curl -fsS -o /dev/null "${URL}api/status" 2>/dev/null && break; sleep 0.5; done
if [ "$OS" = "Darwin" ]; then open "$URL"; else (xdg-open "$URL" >/dev/null 2>&1 || true); fi

cat <<MSG

✅ Listo: $URL  (guárdalo en favoritos)
   1. En el navegador pon tu usuario de Chess.com y/o Lichess y elige tu coach.
   2. Para el chat: si nunca usaste Claude Code, abre la Terminal, escribe  claude  y dentro  /login
   Actualizar: vuelve a pegar el mismo comando de instalación (no borra tus datos).
   Apagar: cd $DEST && ./coach service off
MSG
