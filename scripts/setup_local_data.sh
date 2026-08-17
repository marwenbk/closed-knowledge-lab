#!/usr/bin/env bash
set -euo pipefail

TOPMED_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOPMED_DATA_VENV="${TOPMED_PROJECT_ROOT}/.venv"
TOPMED_PYTHON_BIN="${TOPMED_PYTHON_BIN:-python3}"

if [[ ! -x "${TOPMED_DATA_VENV}/bin/python" ]]; then
  "${TOPMED_PYTHON_BIN}" -m venv "${TOPMED_DATA_VENV}"
fi

if ! "${TOPMED_DATA_VENV}/bin/python" -c \
  'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
  echo "error: TopMed requires Python 3.12 or newer" >&2
  exit 1
fi

"${TOPMED_DATA_VENV}/bin/python" -m pip install -r "${TOPMED_PROJECT_ROOT}/requirements-data.txt"
"${TOPMED_DATA_VENV}/bin/python" "${TOPMED_PROJECT_ROOT}/scripts/bootstrap_demo.py" "$@"
