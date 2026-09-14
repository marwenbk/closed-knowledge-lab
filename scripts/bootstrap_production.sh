#!/usr/bin/env bash
set -euo pipefail

TOPMED_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "${APP_ENV:-}" != "production" ]]; then
  echo "error: production bootstrap requires APP_ENV=production" >&2
  exit 1
fi
if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
  echo "error: DEEPSEEK_API_KEY is required" >&2
  exit 1
fi
if [[ -z "${ADMIN_BOOTSTRAP_EMAIL:-}" || -z "${ADMIN_BOOTSTRAP_PASSWORD:-}" ]]; then
  echo "error: administrator bootstrap credentials are required" >&2
  exit 1
fi

alembic -c "${TOPMED_PROJECT_ROOT}/backend/alembic.ini" upgrade head
python -m app.cli admin bootstrap
python -m app.cli kb import --manifest "${TOPMED_PROJECT_ROOT}/knowledge_base/manifest.json"
python -m app.cli kb embed
if ! python -m app.cli kb status 2>/dev/null | python -c \
  'import json, sys; raise SystemExit(0 if json.load(sys.stdin).get("language") == "en-US" else 1)'; then
  python -m app.cli kb activate
fi
python -m app.cli system ready

echo "✓ Production database and runtime projection are ready"
