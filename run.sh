#!/usr/bin/env bash
# MindContinuum local launcher — macOS / Linux.
#
# Creates a venv, installs deps, then runs the server.
# Override storage location via MINDCONTINUUM_DATA_DIR.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

PYTHON="${PYTHON:-python3}"

if [ ! -d ".venv" ]; then
  echo "[mindcontinuum] creating virtual environment .venv"
  "$PYTHON" -m venv .venv
fi

# shellcheck disable=SC1091
. .venv/bin/activate

if [ ! -f ".venv/.installed" ] || [ pyproject.toml -nt .venv/.installed ] || [ requirements.txt -nt .venv/.installed ]; then
  echo "[mindcontinuum] installing dependencies"
  pip install --quiet --upgrade pip
  pip install --quiet -e .
  touch .venv/.installed
fi

exec python -m mindcontinuum "$@"
