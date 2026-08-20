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

Requires Python 3.12 or newer.

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

### Replay scenarios through the widget API

The data-only bootstrap does not require a backend. After the local backend setup is ready and the API is running, replay the ten enabled scenarios through the real session, conversation, and message endpoints:

```bash
set -a
source .env
set +a

.venv/bin/python scripts/bootstrap_demo.py \
  --force \
  --api-url http://localhost:8000 \
  --assistant-key "$WIDGET_ASSISTANT_KEY" \
  --origin http://localhost:3000 \
  --seed-runtime
```

Runtime replay always uses the public `/api/v1/widget` HTTP APIs; it never inserts sessions, conversations, messages, or RAG runs directly into PostgreSQL. The local report is written to `demo/seed-report.json` and ignored by Git.

Conflict scenarios are disabled by default. They may be included only after their declared `eval-conflict-*` fixture namespace has been prepared and isolated from the canonical knowledge-base version.

## PostgreSQL and Backend Knowledge Foundation

Phase 1 projects the generated Markdown corpus into PostgreSQL and adds local hybrid retrieval. Phase 2 adds verified DeepSeek answers. Phase 3 adds persistent conversations and replayable SSE. Phase 4 adds the customer widget. Phase 5 adds authenticated human takeover. Phase 6 adds the Refine operations back office. Phase 7 completes governed knowledge publishing, evaluated tuning, review-before-send, auditing, and feedback.

### Prerequisites

- Python 3.12 or newer;
- Node.js 20.9 or newer and pnpm 10 or newer for the customer widget;
- either Docker with Docker Compose or Apple's `container` CLI;
- a DeepSeek API key with available credit;
- internet access for dependency/model installation and grounded-answer requests;
- ports `3000`, `5433`, and `8000` available locally.

Both Phase 0 and the backend use the root `.venv`. PostgreSQL runs from the pinned `pgvector/pgvector:0.8.6-pg17-bookworm` image and keeps its data in the `topmed-postgres-data` volume.

### First-time backend setup

```bash
cp .env.example .env
# Set DEEPSEEK_API_KEY and a private ADMIN_BOOTSTRAP_PASSWORD in .env, then run:
bash scripts/setup_local_backend.sh
```

The setup command creates or reuses `.venv`, installs pinned dependencies, regenerates and validates the 15-document dataset, starts PostgreSQL, applies migrations, idempotently bootstraps the configured local administrator, imports and embeds `topmed-demo:2.0.0`, activates the complete version, and verifies readiness.

The command is idempotent. Running it again reuses the environment, database, and model cache; identical import and embedding operations both report `"no_op": true`.

### Run the API

```bash
bash scripts/run_local_backend.sh
```

The development server listens on `http://127.0.0.1:8000` and reloads when backend Python files change. Both local services bind to loopback; `POSTGRES_BIND_HOST` is available only for an intentional PostgreSQL override.

In another terminal, check operational status and run a retrieval query:

```bash
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/ready
curl -fsS http://localhost:8000/api/v1/kb/status
curl -fsS -X POST http://localhost:8000/api/v1/kb/retrieve \
  -H 'Content-Type: application/json' \
  -d '{"query":"Quantos dependentes o nível Gold permite?"}'
curl -fsS -X POST http://localhost:8000/api/v1/kb/answer \
  -H 'Content-Type: application/json' \
  -d '{"query":"Quantos dependentes o nível Gold permite?"}'
```

`/health` checks only the API process. `/ready` checks PostgreSQL, migrations, extensions, the active knowledge base, lexical indexes, current embeddings, the embedding runtime, the conversation event store, and authenticated access to the configured DeepSeek model. The current 30-chunk corpus uses exact pgvector search; an ANN index is unnecessary at this size.

### Migrations and knowledge imports

Apply all migrations:

```bash
.venv/bin/alembic -c backend/alembic.ini upgrade head
```

Import the canonical generated dataset as a draft:

```bash
.venv/bin/python -m app.cli kb import \
  --manifest knowledge_base/manifest.json
```

Download the pinned model if needed, verify its checksums, embed the draft, then activate it atomically:

```bash
.venv/bin/python -m app.cli kb embed --download
.venv/bin/python -m app.cli kb activate
```

Run hybrid retrieval without starting the HTTP server:

```bash
.venv/bin/python -m app.cli kb retrieve \
  --query "Qual é a regra TM-REF-014?"
```

Run the complete grounded-answer pipeline from the CLI:

```bash
.venv/bin/python -m app.cli kb answer \
  --query "Quantos dependentes o nível Gold permite?"
```

Inspect backend readiness or the active dataset without starting the HTTP server:

```bash
.venv/bin/python -m app.cli system ready
.venv/bin/python -m app.cli kb status
```

Knowledge mutation remains CLI-only. The generated Markdown and manifest are canonical; PostgreSQL is their immutable runtime projection.

### Local embeddings and closed-KB retrieval

The embedding runtime uses the 384-dimensional [`intfloat/multilingual-e5-small`](https://huggingface.co/intfloat/multilingual-e5-small) model at the immutable revision recorded in `.env.example`. Setup downloads the 118 MB quantized ONNX model and its tokenizer through the standard Hugging Face cache, validates both SHA-256 checksums, and never commits model artifacts to Git. Documents use the required `passage:` prefix and queries use `query:`; vectors are mean-pooled and L2-normalized before storage.

Each query stays inside the active TopMed dataset and runs:

1. exact semantic search with pgvector, top 10;
2. strict Portuguese full-text search, filled by a bounded broad lexical query when needed;
3. an exact employer-tier mapping candidate when the query names Silver, Gold, or Platinum;
4. weighted reciprocal-rank fusion with `k=60`, returning the best 6 chunks;
5. trigram typo fallback only when strict lexical search has no result and semantic confidence is weak;
6. at most one deterministic second hop from an employer tier to its consumer plan.

The retrieval response includes source path, document key, section path, stable chunk key, ordering, per-channel ranks and scores, and the fused score.

The API loads the local embedding model on the first readiness or retrieval request and keeps it cached in the process. The first load is slower; subsequent requests use the warm runtime.

### DeepSeek grounded answering

Grounded generation uses the official DeepSeek API with `deepseek-v4-flash`, non-thinking mode, temperature `0`, and JSON output validated against Pydantic schemas. The API key stays in the ignored local `.env` file and is never returned by the backend.

Each answer sends the question and at most six retrieved fictional TopMed chunks to DeepSeek. The provider receives no tools, browser, or web-search capability; PostgreSQL remains the only factual source used by the pipeline.

For each question, the backend:

1. retrieves at most six approved chunks from the active dataset;
2. classifies the request as answerable, partial, ambiguous, unsupported, or conflicting;
3. skips generation for ambiguous, unsupported, and conflicting requests;
4. generates structured claims and normalized exact evidence quotes for supported requests;
5. rejects missing, invented, unselected, or non-matching citations server-side;
6. verifies every factual statement against the selected evidence;
7. permits one constrained regeneration, then returns a limitation response if verification still fails.

Malformed provider JSON receives one format-only retry before failing closed. The direct knowledge answer endpoint never streams or persists draft model tokens; the widget message endpoint below persists only the final verified result.

### Conversations and realtime events

Widget access uses a short-lived signed session bound to an allowed `Origin`. For local development, `.env.example` permits `http://localhost:3000` and `http://127.0.0.1:3000`. Change `WIDGET_ASSISTANT_KEY`, `WIDGET_TOKEN_SECRET`, and `WIDGET_ALLOWED_ORIGINS` together for any non-local environment; production configuration rejects the documented local credentials.

Create a session and conversation using only the Python standard library to extract response IDs:

```bash
SESSION_JSON="$(curl -fsS -X POST http://localhost:8000/api/v1/widget/sessions \
  -H 'Content-Type: application/json' \
  -H 'Origin: http://localhost:3000' \
  -d '{"assistant_key":"topmed-local-demo"}')"
WIDGET_TOKEN="$(printf '%s' "$SESSION_JSON" | \
  .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["token"])')"

CONVERSATION_JSON="$(curl -fsS -X POST http://localhost:8000/api/v1/widget/conversations \
  -H 'Content-Type: application/json' \
  -H 'Origin: http://localhost:3000' \
  -H "Authorization: Bearer $WIDGET_TOKEN" \
  -d '{}')"
CONVERSATION_ID="$(printf '%s' "$CONVERSATION_JSON" | \
  .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["conversation_id"])')"
```

Submit a message with a client-generated idempotency key:

```bash
CLIENT_MESSAGE_ID="$(.venv/bin/python -c 'import uuid; print(uuid.uuid4())')"
curl -fsS -X POST \
  "http://localhost:8000/api/v1/widget/conversations/$CONVERSATION_ID/messages" \
  -H 'Content-Type: application/json' \
  -H 'Origin: http://localhost:3000' \
  -H "Authorization: Bearer $WIDGET_TOKEN" \
  -d "{\"content\":\"Quantos dependentes o plano Família permite?\",\
       \"client_message_id\":\"$CLIENT_MESSAGE_ID\"}"
```

Repeating that request with the same ID and content returns the original committed AI message and RAG run without generating again. Reusing the ID with different content is rejected.

Listen for persisted public events and replay from the last received numeric event ID:

```bash
curl -N \
  "http://localhost:8000/api/v1/widget/conversations/$CONVERSATION_ID/events" \
  -H 'Accept: text/event-stream' \
  -H 'Origin: http://localhost:3000' \
  -H "Authorization: Bearer $WIDGET_TOKEN" \
  -H 'Last-Event-ID: 0'
```

The server commits the customer message and `processing.started` before calling DeepSeek. Model inference runs without holding a database transaction. Only a verified answer is then committed with its AI message, exact citations, versioned RAG metadata, and delivery events. SSE is only a delivery channel: reconnecting clients replay the append-only PostgreSQL event log, bounded by `SSE_REPLAY_LIMIT`.

Customer messages submitted in `HUMAN_REQUESTED`, `HUMAN_ASSIGNED`, or `HUMAN_ACTIVE` are committed to PostgreSQL and returned with `delivery_mode: "HUMAN_QUEUE"`; they never start a RAG run or load an embedding or LLM provider. Returning control to AI affects only future customer messages.

### Authenticated human takeover

Request a person from the widget session:

```bash
curl -fsS -X POST \
  "http://localhost:8000/api/v1/widget/conversations/$CONVERSATION_ID/request-human" \
  -H 'Origin: http://localhost:3000' \
  -H "Authorization: Bearer $WIDGET_TOKEN"
```

The local setup creates `ADMIN_BOOTSTRAP_EMAIL` exactly once and never changes an existing password. To bootstrap explicitly after changing configuration:

```bash
.venv/bin/python -m app.cli admin bootstrap
```

Log in with a cookie jar. The HTTP-only session cookie is paired with a CSRF cookie that must be repeated in `X-CSRF-Token` for admin writes:

```bash
curl -fsS -c /tmp/topmed-admin.cookies -X POST \
  http://localhost:8000/api/v1/admin/auth/login \
  -H 'Content-Type: application/json' \
  -H 'Origin: http://localhost:3000' \
  -d "{\"email\":\"$ADMIN_BOOTSTRAP_EMAIL\",\
       \"password\":\"$ADMIN_BOOTSTRAP_PASSWORD\"}"

ADMIN_CSRF_TOKEN="$(awk '$6 == "topmed_admin_csrf" {print $7}' \
  /tmp/topmed-admin.cookies)"
curl -fsS -b /tmp/topmed-admin.cookies \
  http://localhost:8000/api/v1/admin/handoffs \
  -H 'Origin: http://localhost:3000'
curl -fsS -b /tmp/topmed-admin.cookies -X POST \
  "http://localhost:8000/api/v1/admin/conversations/$CONVERSATION_ID/claim" \
  -H 'Origin: http://localhost:3000' \
  -H "X-CSRF-Token: $ADMIN_CSRF_TOKEN"
```

The same authenticated API supports public human replies, private internal notes, return-to-AI, close, conversation detail, and replayable admin SSE. Claims are atomic: a competing agent receives `409 HANDOFF_ALREADY_CLAIMED`. Internal notes are excluded from the widget, public SSE, citations, and LLM context.

## Refine operations back office

The protected Refine v5 application runs under `/admin` and consumes only the typed FastAPI admin API. FastAPI remains authoritative for sessions, roles, CSRF checks, assignment, and state transitions.

Install the pinned frontend dependencies, then run the API and Next.js in separate terminals:

```bash
bash scripts/setup_local_frontend.sh
bash scripts/run_local_backend.sh
bash scripts/run_local_frontend.sh
```

Open `http://127.0.0.1:3000/admin/login` and use `ADMIN_BOOTSTRAP_EMAIL` and `ADMIN_BOOTSTRAP_PASSWORD` from the ignored `.env` file. The back office includes:

- an operational dashboard with handoff, answerability, latency, model, prompt, embedding, and active-dataset status;
- a live handoff queue with atomic claiming;
- a conversation workspace for public replies, private notes, return-to-AI, and closing;
- a structured RAG inspector with retrieval evidence, answerability, citation checks, verification, and version provenance, without model chain-of-thought;
- a governed knowledge workspace for immutable history, draft Markdown revisions, safe previews, generated chunks, validation, indexing, evaluation, publication, and rollback.

The dashboard and queue update from the authenticated admin SSE stream. RAG traces are persisted for runs created after migration `0005_admin_insights`; older runs correctly show no trace.

### Governed knowledge publishing

Knowledge publishing is available under `/admin/knowledge` after migration `0006_knowledge_publishing`. The active and retired versions are immutable at both the API and database layers. A `KNOWLEDGE_EDITOR`, `SUPERVISOR`, or `ADMIN` can:

1. create a semantic-versioned draft from the active version;
2. edit Markdown revisions and inspect the safely rendered document, deterministic chunks, related fact IDs, and dependent evaluation cases;
3. validate front matter, document and chunk counts, canonical evaluation facts, duplicate policy IDs, and archived conflict fixtures;
4. generate the draft embedding index;
5. run the deterministic 63-case retrieval publication gate.

Only `ADMIN` and `SUPERVISOR` roles can publish a passing draft or reactivate a retired version. A successful gate is bound to the exact draft manifest checksum, so any later edit invalidates validation and prevents stale evaluation results from being activated. The publication gate uses the local embedding model and PostgreSQL but does not call DeepSeek or consume API credit. Full live answer evaluation remains an explicit release check.

Re-running `bash scripts/setup_local_backend.sh` preserves whichever governed knowledge version is active; it activates the generated `2.0.0` baseline only when no active version exists.

### Versioned prompt and retrieval tuning

Migration `0007_runtime_tuning` seeds the existing prompt bundle and retrieval defaults as immutable active version `1.0.0`. The `/admin/tuning` workspace lets authorized operators clone semantic-versioned drafts, edit the three system prompts or the nine supported retrieval controls, inspect checksum-bound evaluations, activate a passing candidate, and roll back an evaluated retired version.

Retrieval settings run the complete 63-case local gate without API cost. Prompt evaluation requires an explicit confirmation because it runs all 100 cases through DeepSeek and may first run another 100 cases to establish an exact active baseline. The browser waits for this synchronous operation; do not restart the backend while it is running. Activation is rejected whenever the active KB, counterpart configuration, model identity, embedding revision, or candidate checksum differs from the evaluated tuple.

Every new RAG run snapshots the active KB, prompt, and settings versions before inference. Active and retired runtime versions are database-protected, and the public/widget APIs fail closed if either active configuration is missing or its checksum is invalid.

### Review before send

Set `REVIEW_BEFORE_SEND_ENABLED=true` to hold verified `ANSWERABLE` and `PARTIALLY_ANSWERABLE` proposals for a reviewer after migration `0008_review_before_send`. The widget receives `delivery_mode: "REVIEW_PENDING"`, never receives the proposal text, and shows a waiting banner while the conversation is `AI_REVIEW_PENDING`.

An `ADMIN`, `SUPERVISOR`, or `HUMAN_REVIEWER` can approve unchanged, edit and send, regenerate once, send the conversation to the human queue, or close without sending. Edited text is checked again against the stored citation evidence before publication. Proposals, original and final text, diffs, reviewers, decisions, and timestamps remain append-only and auditable. A customer handoff requested during review rejects the hidden proposal before entering the queue.

### Audit explorer and feedback

Migration `0009_audit_feedback` adds append-only response feedback and the `/admin/audit` workspace. `ADMIN`, `SUPERVISOR`, and `AUDITOR` roles can filter audit events by event, actor, resource, identifier, and time and inspect structured before/after metadata. The same workspace lists feedback by category and links back to its conversation.

`ADMIN`, `SUPERVISOR`, `SUPPORT_AGENT`, and `HUMAN_REVIEWER` roles can classify a RAG run from the conversation inspector as correct, incorrect, missing or conflicting KB information, retrieval or grounding failure, or appropriate/unnecessary escalation. Feedback creates an audit event but never changes knowledge, prompts, settings, or model behavior automatically.

For a populated evaluator view, start both servers and replay the canonical scenarios through the public widget API:

```bash
set -a
source .env
set +a

.venv/bin/python scripts/bootstrap_demo.py \
  --force \
  --api-url http://127.0.0.1:8000 \
  --assistant-key "$WIDGET_ASSISTANT_KEY" \
  --origin http://127.0.0.1:3000 \
  --seed-runtime
```

This replay uses real API calls and DeepSeek, consumes API credit, and writes only the ignored `demo/seed-report.json` report outside PostgreSQL.

## Production deployment

Phase 8 targets Render's Hobby workspace with paid runtime instances: a Standard FastAPI/ONNX service, a Starter Next.js service, a Basic PostgreSQL 17 database, and a free static external embed fixture. The root `render.yaml` is the authoritative infrastructure definition; both application services build from their pinned Dockerfiles.

The Next.js service proxies `/api`, `/health`, and `/ready` to FastAPI over Render's private network. Browser traffic therefore remains same-origin for secure administrator cookies and SSE. The backend Docker image bakes in the checksum-verified embedding model, while PostgreSQL remains the only mutable runtime data store.

See [the Render deployment runbook](docs/deployment.md) for provisioning, secrets, release verification, rollback, and recovery. Never commit production credentials or place them in Docker build arguments.

## Customer chat widget

Phase 4 provides a direct chat at `/chat`, an iframe application at `/widget`, and a small framework-free loader at `/widget.js`. The UI uses assistant-ui's external-store runtime: FastAPI and PostgreSQL remain authoritative, while the browser keeps only the signed session, active conversation ID, and replay cursor in session storage.

Install the pinned frontend dependencies:

```bash
bash scripts/setup_local_frontend.sh
```

Run the backend in one terminal and the frontend in another:

```bash
bash scripts/run_local_backend.sh
bash scripts/run_local_frontend.sh
```

Open `http://127.0.0.1:3000/chat` for the direct experience. To verify the embed loader on a plain host page, run:

```bash
.venv/bin/python -m http.server 3001 \
  --bind 127.0.0.1 \
  --directory demo
```

Then open `http://127.0.0.1:3001/embed-host.html`. The fixture loads:

```html
<script
  src="http://127.0.0.1:3000/widget.js"
  data-api-url="http://127.0.0.1:8000"
  data-assistant-key="topmed-local-demo"
  data-position="bottom-right"
  data-locale="pt-BR"
></script>
```

The loader injects a style-isolated launcher and iframe, validates host messages against the exact chat origin, supports an unread badge and Escape-to-close, and expands to fullscreen on small screens. The chat reconnects to authenticated SSE using its last persisted event ID and falls back to snapshot polling during transient stream failures. Only committed, verified answers are rendered, with expandable exact citations and a permanent fictional-service/privacy warning.

The browser never receives the DeepSeek key or an admin credential. Add every deployed frontend origin to `WIDGET_ALLOWED_ORIGINS`; the signed widget session is bound to that exact origin. “Falar com uma pessoa” requests takeover, keeps the composer usable while waiting, and updates the banner as an agent claims, replies, or returns the conversation to AI.

### Evaluation gates

Run the local, generation-free gate against the active PostgreSQL knowledge base:

```bash
.venv/bin/python -m app.cli eval run \
  --output evals/retrieval.report.json
```

The command validates the complete 100-case contract, then evaluates all 63 non-fixture cases with required facts. It fails unless source Recall@6, fact Recall@6, and required second-hop coverage are all 100%. The JSON report also includes reciprocal-rank, latency, typo-fallback, per-case, and per-category results.

Run the complete pipeline and adversarial suites explicitly:

```bash
.venv/bin/python -m app.cli eval run --live \
  --output evals/full.report.json
```

`--live` executes all 100 cases through DeepSeek and consumes API credit. It checks expected answerability status, verified citations, required source coverage, forbidden-fact citations, ambiguity and fail-closed behavior. The five contradiction fixtures are added only to their individual in-memory evaluation evidence; they are never written to the canonical PostgreSQL knowledge base. Generated `*.report.json` files are local artifacts ignored by Git.

### Tests and static checks

With PostgreSQL running and `.env` loaded, run the complete suite:

```bash
set -a
source .env
set +a

TOPMED_REQUIRE_MODEL_TESTS=1 TOPMED_REQUIRE_LLM_TESTS=1 \
  .venv/bin/pytest backend/tests
.venv/bin/ruff check --config backend/pyproject.toml backend
.venv/bin/ruff format --check --config backend/pyproject.toml backend
.venv/bin/mypy --config-file backend/pyproject.toml backend/app
.venv/bin/python -m compileall -q backend/app backend/tests
pnpm --dir frontend check
pnpm --dir frontend build
```

The PostgreSQL tests create uniquely named disposable databases through `TOPMED_TEST_DATABASE_URL` and remove only those databases afterward. They cover migrations, immutable knowledge history, draft revision and chunk generation, checksum-bound validation and evaluation gates, the complete 63-case retrieval gate, publication and rollback, signed widget sessions, administrator authentication and CSRF, atomic handoff claims, AI suppression, human messages, internal-note privacy, state transitions, persisted RAG traces, admin dashboard and knowledge reads, ordered replay, and append-only events. The two `TOPMED_REQUIRE_*_TESTS` flags make missing embedding artifacts or DeepSeek access fail complete verification instead of silently skipping model tests. Live DeepSeek checks consume API credit and run only when `TOPMED_REQUIRE_LLM_TESTS=1` is explicit; the test suite keeps those checks to eight representative cases, while the explicit `eval run --live` command runs all 100.

### Pre-commit hooks

The backend setup installs the Git hook automatically. For an existing environment, install it once with:

```bash
.venv/bin/python -m pip install --editable "backend[dev]"
.venv/bin/pre-commit install --install-hooks
```

Run every hook manually with:

```bash
.venv/bin/pre-commit run --all-files
```

Commits check whitespace, YAML and TOML syntax, merge markers, large files, private keys, Ruff linting and formatting, MyPy, the backend unit suite, and frontend lint, types, and unit tests. PostgreSQL, embedding-model, live DeepSeek, and production frontend-build checks remain in the explicit complete-suite command above.

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
- If administrator bootstrap fails, set a non-default `ADMIN_BOOTSTRAP_PASSWORD` of 12–128 characters. Repeated setup intentionally does not overwrite an existing password.
- If `/admin` cannot log in or mutate a conversation, use the same host spelling for both local URLs (`127.0.0.1`, not a mix of `localhost` and `127.0.0.1`) and confirm it appears in `ADMIN_ALLOWED_ORIGINS`.
- If PostgreSQL cannot bind port `5433`, stop the process using that port or change `POSTGRES_HOST_PORT` and the port in `DATABASE_URL` and `TOPMED_TEST_DATABASE_URL` together.
- If `/health` succeeds but `/ready` returns `503`, inspect the individual readiness checks, then rerun the migration, import, and embedding commands above.
- If semantic status is `pending`, import the intended version, run `kb embed --download`, then run `kb activate`. The previous active version stays available until the replacement is completely embedded.
- If a draft cannot be published, open its version page and run the gates in order: validate, index, evaluate, then publish. Editing after a successful gate intentionally invalidates the prior result.
- If model download or checksum validation fails, remove only the affected revision directory shown in the error and rerun `kb embed --download`; do not bypass checksum validation.
- If the `llm_runtime` readiness check fails, confirm that `DEEPSEEK_API_KEY` is set in `.env`, the account has credit, and `https://api.deepseek.com` is reachable.
- If widget session creation returns `401`, confirm that the request `Origin` exactly matches an entry in `WIDGET_ALLOWED_ORIGINS` and that the assistant key matches `WIDGET_ASSISTANT_KEY`.
- If SSE replay returns `409`, reload the conversation snapshot and reconnect from its latest event; the missed range exceeded `SSE_REPLAY_LIMIT`.
- If neither supported container provider is installed, install Docker with Compose or Apple's `container` CLI before running the backend setup.
