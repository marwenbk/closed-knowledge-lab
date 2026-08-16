#!/usr/bin/env bash
set -euo pipefail

TOPCARE_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOPCARE_DATA_VENV="${TOPCARE_PROJECT_ROOT}/.venv"
TOPCARE_PYTHON_BIN="${TOPCARE_PYTHON_BIN:-python3}"

"${TOPCARE_PYTHON_BIN}" -m venv "${TOPCARE_DATA_VENV}"
"${TOPCARE_DATA_VENV}/bin/python" -m pip install --upgrade pip
"${TOPCARE_DATA_VENV}/bin/python" -m pip install -r "${TOPCARE_PROJECT_ROOT}/requirements-data.txt"
"${TOPCARE_DATA_VENV}/bin/python" "${TOPCARE_PROJECT_ROOT}/scripts/bootstrap_demo.py" "$@"
