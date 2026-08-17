#!/usr/bin/env bash
set -euo pipefail

TOPMED_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOPMED_ENV_FILE="${TOPMED_PROJECT_ROOT}/.env"
TOPMED_CONTAINER_NAME="topmed-postgres"
TOPMED_VOLUME_NAME="topmed-postgres-data"
TOPMED_POSTGRES_IMAGE="pgvector/pgvector:0.8.6-pg17-bookworm"

if [[ ! -f "${TOPMED_ENV_FILE}" ]]; then
  echo "error: .env is missing; copy .env.example to .env" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "${TOPMED_ENV_FILE}"
set +a

POSTGRES_USER="${POSTGRES_USER:-topmed}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-topmed}"
POSTGRES_DB="${POSTGRES_DB:-topmed}"
POSTGRES_HOST_PORT="${POSTGRES_HOST_PORT:-5433}"
TOPMED_POSTGRES_PROVIDER=""

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  if docker compose --env-file "${TOPMED_ENV_FILE}" -f "${TOPMED_PROJECT_ROOT}/docker-compose.yml" up -d postgres; then
    TOPMED_POSTGRES_PROVIDER="docker"
  fi
fi

if [[ -z "${TOPMED_POSTGRES_PROVIDER}" ]] && command -v container >/dev/null 2>&1; then
  container system start >/dev/null
  if container inspect "${TOPMED_CONTAINER_NAME}" >/dev/null 2>&1; then
    container start "${TOPMED_CONTAINER_NAME}" >/dev/null 2>&1 || true
  else
    if ! container volume inspect "${TOPMED_VOLUME_NAME}" >/dev/null 2>&1; then
      container volume create "${TOPMED_VOLUME_NAME}" >/dev/null
    fi
    container run \
      --detach \
      --name "${TOPMED_CONTAINER_NAME}" \
      --publish "${POSTGRES_HOST_PORT}:5432" \
      --env "POSTGRES_USER=${POSTGRES_USER}" \
      --env "POSTGRES_PASSWORD=${POSTGRES_PASSWORD}" \
      --env "POSTGRES_DB=${POSTGRES_DB}" \
      --env "PGDATA=/var/lib/postgresql/data/pgdata" \
      --env "TZ=UTC" \
      --volume "${TOPMED_VOLUME_NAME}:/var/lib/postgresql/data" \
      "${TOPMED_POSTGRES_IMAGE}" >/dev/null
  fi
  TOPMED_POSTGRES_PROVIDER="container"
fi

if [[ -z "${TOPMED_POSTGRES_PROVIDER}" ]]; then
  echo "error: Docker Compose or Apple container is required" >&2
  exit 1
fi

for _ in {1..60}; do
  if command -v pg_isready >/dev/null 2>&1; then
    if pg_isready \
      --host localhost \
      --port "${POSTGRES_HOST_PORT}" \
      --username "${POSTGRES_USER}" \
      --dbname "${POSTGRES_DB}" >/dev/null 2>&1; then
      echo "✓ PostgreSQL is ready via ${TOPMED_POSTGRES_PROVIDER}"
      exit 0
    fi
  elif [[ "${TOPMED_POSTGRES_PROVIDER}" == "docker" ]]; then
    if docker compose -f "${TOPMED_PROJECT_ROOT}/docker-compose.yml" exec -T postgres \
      pg_isready --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" >/dev/null 2>&1; then
      echo "✓ PostgreSQL is ready via Docker Compose"
      exit 0
    fi
  elif container exec "${TOPMED_CONTAINER_NAME}" \
    pg_isready --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" >/dev/null 2>&1; then
    echo "✓ PostgreSQL is ready via Apple container"
    exit 0
  fi
  sleep 1
done

echo "error: PostgreSQL did not become ready within 60 seconds" >&2
exit 1
