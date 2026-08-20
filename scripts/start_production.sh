#!/usr/bin/env bash
set -euo pipefail

TOPMED_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

bash "${TOPMED_PROJECT_ROOT}/scripts/bootstrap_production.sh"

exec uvicorn app.main:app \
  --app-dir "${TOPMED_PROJECT_ROOT}/backend" \
  --host 0.0.0.0 \
  --port "${PORT:-10000}"
