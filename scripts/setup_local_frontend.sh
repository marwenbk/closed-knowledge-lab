#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

command -v node >/dev/null
command -v pnpm >/dev/null
CI=true pnpm --dir frontend install --frozen-lockfile
