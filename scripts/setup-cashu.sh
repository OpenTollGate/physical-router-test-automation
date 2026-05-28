#!/usr/bin/env bash
set -euo pipefail

VENV_DIR="$HOME/.cashu-venv"

PYTHON=$(command -v python3 2>/dev/null || command -v python3.12 2>/dev/null)
if [ -z "$PYTHON" ]; then
  echo "ERROR: python3 not found" >&2
  exit 1
fi
PY_VER=$("$PYTHON" -c 'import sys; print(f"python{sys.version_info.major}.{sys.version_info.minor}")')
echo "==> Creating ${PY_VER} venv at ${VENV_DIR}..."
"$PYTHON" -m venv "$VENV_DIR"

echo "==> Installing cashu..."
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install cashu

echo "==> Pinning marshmallow to 3.x..."
"$VENV_DIR/bin/pip" install 'marshmallow<4'

MODELS_FILE=$(find "$VENV_DIR/lib/" -path "*/cashu/core/models.py" | head -1)
if [ -z "$MODELS_FILE" ]; then
  echo "ERROR: models.py not found under $VENV_DIR" >&2
  exit 1
fi

echo "==> Patching models.py: active: bool -> active: bool = True"
sed -i 's/    active: bool$/    active: bool = True/' "$MODELS_FILE"

echo "==> Symlinking cashu to ~/.local/bin/cashu (no sudo)..."
mkdir -p "$HOME/.local/bin"
ln -sf "$VENV_DIR/bin/cashu" "$HOME/.local/bin/cashu"

echo "==> Verifying..."
"$VENV_DIR/bin/cashu" --version 2>/dev/null || echo "(cashu installed, version check non-critical)"
echo "==> setup-cashu complete."
