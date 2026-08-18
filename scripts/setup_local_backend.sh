#!/usr/bin/env bash
set -euo pipefail

TOPMED_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOPMED_VENV="${TOPMED_PROJECT_ROOT}/.venv"

if [[ ! -f "${TOPMED_PROJECT_ROOT}/.env" ]]; then
  cp "${TOPMED_PROJECT_ROOT}/.env.example" "${TOPMED_PROJECT_ROOT}/.env"
  echo "✓ Created .env from local demo defaults"
fi

set -a
# shellcheck disable=SC1090
source "${TOPMED_PROJECT_ROOT}/.env"
set +a
if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
  echo "error: set DEEPSEEK_API_KEY in .env before backend setup" >&2
  exit 1
fi
if [[ -z "${ADMIN_BOOTSTRAP_EMAIL:-}" || -z "${ADMIN_BOOTSTRAP_PASSWORD:-}" ]]; then
  echo "error: set ADMIN_BOOTSTRAP_EMAIL and ADMIN_BOOTSTRAP_PASSWORD in .env" >&2
  exit 1
fi

bash "${TOPMED_PROJECT_ROOT}/scripts/setup_local_data.sh"
"${TOPMED_VENV}/bin/python" -m pip install --editable "${TOPMED_PROJECT_ROOT}/backend[dev]"
"${TOPMED_VENV}/bin/pre-commit" install --install-hooks
bash "${TOPMED_PROJECT_ROOT}/scripts/start_local_postgres.sh"
"${TOPMED_VENV}/bin/alembic" -c "${TOPMED_PROJECT_ROOT}/backend/alembic.ini" upgrade head
"${TOPMED_VENV}/bin/python" -m app.cli admin bootstrap
"${TOPMED_VENV}/bin/python" -m app.cli kb import \
  --manifest "${TOPMED_PROJECT_ROOT}/knowledge_base/manifest.json"
"${TOPMED_VENV}/bin/python" -m app.cli kb embed --download
"${TOPMED_VENV}/bin/python" -m app.cli kb activate
"${TOPMED_VENV}/bin/python" -m app.cli system ready

echo "✓ PostgreSQL, grounded answering, and human takeover are ready"
