#!/usr/bin/env bash
# Create and use a venv in the repo root. Run from repo root: ./scripts/setup_venv.sh

set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="${REPO_ROOT}/venv"

cd "$REPO_ROOT"
if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
  echo "Created venv at $VENV_DIR"
fi
# Activate and install (caller can source venv/bin/activate for interactive use)
"${VENV_DIR}/bin/pip" install -e .
"${VENV_DIR}/bin/pip" install -r requirements.txt
echo "Done. Activate with: source venv/bin/activate"
