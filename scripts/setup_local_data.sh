#!/usr/bin/env bash
set -euo pipefail

TOPMED_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOPMED_DATA_VENV="${TOPMED_PROJECT_ROOT}/.venv"
TOPMED_PYTHON_BIN="${TOPMED_PYTHON_BIN:-python3}"

"${TOPMED_PYTHON_BIN}" -m venv "${TOPMED_DATA_VENV}"
"${TOPMED_DATA_VENV}/bin/python" -m pip install --upgrade pip
"${TOPMED_DATA_VENV}/bin/python" -m pip install -r "${TOPMED_PROJECT_ROOT}/requirements-data.txt"
"${TOPMED_DATA_VENV}/bin/python" "${TOPMED_PROJECT_ROOT}/scripts/bootstrap_demo.py" "$@"
