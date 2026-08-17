#!/usr/bin/env bash
set -euo pipefail

TOPMED_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -x "${TOPMED_PROJECT_ROOT}/.venv/bin/uvicorn" ]]; then
  echo "error: backend dependencies are missing; run scripts/setup_local_backend.sh" >&2
  exit 1
fi

bash "${TOPMED_PROJECT_ROOT}/scripts/start_local_postgres.sh"
exec "${TOPMED_PROJECT_ROOT}/.venv/bin/uvicorn" app.main:app \
  --app-dir "${TOPMED_PROJECT_ROOT}/backend" \
  --host 0.0.0.0 \
  --port 8000 \
  --reload

