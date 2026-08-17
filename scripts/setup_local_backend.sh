#!/usr/bin/env bash
set -euo pipefail

TOPMED_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOPMED_VENV="${TOPMED_PROJECT_ROOT}/.venv"

if [[ ! -f "${TOPMED_PROJECT_ROOT}/.env" ]]; then
  cp "${TOPMED_PROJECT_ROOT}/.env.example" "${TOPMED_PROJECT_ROOT}/.env"
  echo "✓ Created .env from local demo defaults"
fi

bash "${TOPMED_PROJECT_ROOT}/scripts/setup_local_data.sh"
"${TOPMED_VENV}/bin/python" -m pip install --editable "${TOPMED_PROJECT_ROOT}/backend[dev]"
bash "${TOPMED_PROJECT_ROOT}/scripts/start_local_postgres.sh"
"${TOPMED_VENV}/bin/alembic" -c "${TOPMED_PROJECT_ROOT}/backend/alembic.ini" upgrade head
"${TOPMED_VENV}/bin/python" -m app.cli kb import \
  --manifest "${TOPMED_PROJECT_ROOT}/knowledge_base/manifest.json" \
  --activate
"${TOPMED_VENV}/bin/python" -m app.cli system ready

echo "✓ Local PostgreSQL and backend knowledge foundation are ready"

