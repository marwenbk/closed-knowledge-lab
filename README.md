# TopMed Demo

## Local Demo Data

The data layer is built before the application. Canonical rules, stable facts, corpus templates, evaluations, conflict fixtures, and demo scenarios form one versioned contract.

### Data layout

Source-controlled inputs:

- `data/seed_rules.yaml`: the only source of business rules;
- `data/fact_catalog.yaml`: stable fact IDs and canonical sources;
- `data/templates/`: the 15 deterministic Markdown templates;
- `data/eval_blueprints.yaml`: curated evaluation definitions;
- `data/conflict_fixtures.yaml`: isolated contradiction fixtures;
- `demo/scenarios.yaml`: conversations for later API replay.

Generated outputs:

- `knowledge_base/*.md` and `knowledge_base/manifest.json`;
- `evals/cases.yaml`.

### First-time setup

Requires Python 3.11 or newer.

```bash
bash scripts/setup_local_data.sh --force
```

This command:

1. creates an isolated `.venv` virtual environment;
2. installs the pinned data-generation dependencies;
3. renders all 15 knowledge-base documents;
4. writes `knowledge_base/manifest.json`;
5. validates 76 catalog facts, policy IDs, intentional gaps, corpus size, and checksums;
6. generates and validates 100 evaluation cases;
7. validates the runtime scenario contract without calling an API.

### Run again

After the environment exists, regenerate and validate the local data with:

```bash
.venv/bin/python scripts/bootstrap_demo.py
```

If a canonical input changed intentionally, review the change and allow generated files to be replaced:

```bash
.venv/bin/python scripts/bootstrap_demo.py --force
```

The generators refuse to overwrite changed outputs unless `--force` is supplied. Files under `knowledge_base/` and `evals/cases.yaml` are generated and should not be edited directly.

### Run individual steps

```bash
.venv/bin/python scripts/generate_demo_kb.py
.venv/bin/python scripts/validate_demo_kb.py
.venv/bin/python scripts/generate_evals.py
.venv/bin/python scripts/seed_demo_runtime.py --dry-run
```

Generation is deterministic: the manifest contains checksums of the canonical seed, templates, and every generated document, without a changing timestamp.

### Replay scenarios after the API exists

The data-only bootstrap does not require a backend. Once the protected re-index and chat endpoints are implemented, index the generated dataset, wait for readiness, and replay the enabled scenarios with:

```bash
export ADMIN_TOKEN="replace-with-the-protected-api-token"

.venv/bin/python scripts/bootstrap_demo.py \
  --force \
  --api-url http://localhost:8000 \
  --admin-token "$ADMIN_TOKEN" \
  --seed-runtime
```

Runtime replay always uses `POST /api/chat`; it never inserts conversations directly into a database. The local report is written to `demo/seed-report.json` and ignored by Git.

Conflict scenarios are disabled by default. They may be included only after their declared `eval-conflict-*` fixture namespace has been prepared and isolated from the canonical knowledge-base version.

## PostgreSQL and Backend Knowledge Foundation

Phase 1A projects the generated Markdown corpus into PostgreSQL and exposes only operational status endpoints. It does not add authentication, conversations, answer generation, RAG endpoints, or frontend code.

### Prerequisites

- Python 3.11 or newer;
- either Docker with Docker Compose or Apple's `container` CLI;
- internet access during the first dependency and container-image installation;
- ports `5433` and `8000` available locally.

Both Phase 0 and the backend use the root `.venv`. PostgreSQL runs from the pinned `pgvector/pgvector:0.8.6-pg17-bookworm` image and keeps its data in the `topmed-postgres-data` volume.

### First-time backend setup

```bash
bash scripts/setup_local_backend.sh
```

The setup command creates `.env` from `.env.example` when needed, creates or reuses `.venv`, installs pinned data and backend dependencies, regenerates and validates the 15-document dataset, starts PostgreSQL, applies Alembic migrations, imports and activates `topmed-demo:2.0.0`, and verifies readiness.

The command is idempotent. Running it again reuses the environment and database, leaves an identical imported version unchanged, and reports `"no_op": true` for the import.

### Run the API

```bash
bash scripts/run_local_backend.sh
```

The development server listens on `http://localhost:8000` and reloads when backend Python files change.

In another terminal, check the three available endpoints:

```bash
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/ready
curl -fsS http://localhost:8000/api/v1/kb/status
```

`/health` checks only the API process. `/ready` checks PostgreSQL, the current migration, required extensions, the active knowledge base, and lexical indexes. The semantic section intentionally reports `pending`: vector storage is available, but embeddings and semantic retrieval belong to the next backend phase.

### Migrations and knowledge imports

Apply all migrations:

```bash
.venv/bin/alembic -c backend/alembic.ini upgrade head
```

Import and atomically activate the canonical generated dataset:

```bash
.venv/bin/python -m app.cli kb import \
  --manifest knowledge_base/manifest.json \
  --activate
```

Inspect backend readiness or the active dataset without starting the HTTP server:

```bash
.venv/bin/python -m app.cli system ready
.venv/bin/python -m app.cli kb status
```

Knowledge mutation is CLI-only in this phase. The generated Markdown and manifest remain the canonical inputs; PostgreSQL is their immutable runtime projection.

### Tests and static checks

With PostgreSQL running and `.env` loaded, run the complete suite:

```bash
set -a
source .env
set +a

.venv/bin/pytest backend/tests
.venv/bin/ruff check backend
.venv/bin/ruff format --check backend
.venv/bin/mypy backend/app
.venv/bin/python -m compileall -q backend/app backend/tests
```

The PostgreSQL test creates a uniquely named disposable test database through `TOPMED_TEST_DATABASE_URL` and removes only that database afterward.

### Stop PostgreSQL

Use the command matching the local provider:

```bash
docker compose down
```

or:

```bash
container stop topmed-postgres
```

Both commands preserve the named database volume.

### Troubleshooting

- If `.env` is missing, copy `.env.example` to `.env` or rerun the setup command.
- If PostgreSQL cannot bind port `5433`, stop the process using that port or change `POSTGRES_HOST_PORT` and the port in `DATABASE_URL` and `TOPMED_TEST_DATABASE_URL` together.
- If `/health` succeeds but `/ready` returns `503`, inspect the individual readiness checks, then rerun the migration and import commands above.
- A semantic status of `pending` is expected in Phase 1A and does not make lexical readiness fail.
- If neither supported container provider is installed, install Docker with Compose or Apple's `container` CLI before running the backend setup.
