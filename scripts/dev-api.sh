#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="$ROOT_DIR/apps/api"
VENV_DIR="$API_DIR/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Error: $PYTHON_BIN is not installed or not available in PATH." >&2
  exit 1
fi

cd "$API_DIR"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "Creating Python virtual environment..."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"

if ! "$VENV_PYTHON" -m pip --version >/dev/null 2>&1; then
  echo "Repairing broken Python virtual environment..."
  "$PYTHON_BIN" -m venv --clear "$VENV_DIR"
fi

REQUIREMENTS_HASH="$("$VENV_PYTHON" -c 'import hashlib, pathlib; print(hashlib.sha256(pathlib.Path("requirements.txt").read_bytes()).hexdigest())')"
STAMP_FILE="$VENV_DIR/.requirements.sha256"
INSTALLED_HASH="$(cat "$STAMP_FILE" 2>/dev/null || true)"

if [[ "$REQUIREMENTS_HASH" != "$INSTALLED_HASH" ]] || ! "$VENV_PYTHON" -c "import fastapi, uvicorn, httpx, google_auth_oauthlib" >/dev/null 2>&1; then
  echo "Installing API dependencies..."
  "$VENV_PYTHON" -m pip install --upgrade pip
  "$VENV_PYTHON" -m pip install -r requirements.txt
  printf '%s' "$REQUIREMENTS_HASH" > "$STAMP_FILE"
fi

if [[ ! -f .env ]]; then
  echo "Warning: apps/api/.env was not found."
  echo "Create it with: cp ../../.env.example .env"
fi

echo "Starting Creative Asset Manager API at http://127.0.0.1:8000"
exec "$VENV_PYTHON" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 "$@"
