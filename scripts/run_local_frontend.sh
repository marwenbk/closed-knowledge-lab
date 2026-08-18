#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

exec pnpm --dir frontend exec next dev \
  --hostname "${FRONTEND_BIND_HOST:-127.0.0.1}" \
  --port "${FRONTEND_PORT:-3000}"
