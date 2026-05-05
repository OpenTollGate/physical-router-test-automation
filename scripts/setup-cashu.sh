#!/usr/bin/env bash
set -euo pipefail

VENV_DIR="$HOME/.cashu-venv"

echo "==> Creating Python 3.12 venv at ${VENV_DIR}..."
python3.12 -m venv "$VENV_DIR"

echo "==> Installing cashu..."
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install cashu

echo "==> Pinning marshmallow to 3.x..."
"$VENV_DIR/bin/pip" install 'marshmallow<4'

MODELS_FILE="$VENV_DIR/lib/python3.12/site-packages/cashu/core/models.py"
if [ ! -f "$MODELS_FILE" ]; then
  echo "ERROR: models.py not found at $MODELS_FILE" >&2
  exit 1
fi

echo "==> Patching models.py: active: bool -> active: bool = True"
sed -i '' 's/    active: bool$/    active: bool = True/' "$MODELS_FILE"

echo "==> Creating symlink at /usr/local/bin/cashu..."
sudo ln -sf "$VENV_DIR/bin/cashu" /usr/local/bin/cashu

echo "==> Verifying..."
"$VENV_DIR/bin/cashu" --version 2>/dev/null || echo "(cashu installed, version check non-critical)"
echo "==> setup-cashu complete."
