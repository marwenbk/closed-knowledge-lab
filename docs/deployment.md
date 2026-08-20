# TopMed Render Deployment

TopMed deploys as one modular monolith split into two runtime processes and one managed database:

```text
Browser / external host
        |
        v
topmed-demo-web-marwen (Next.js)
        |  same-origin /api, /health, /ready proxy
        v
topmed-demo-api-marwen (FastAPI + ONNX)
        |
        v
topmed-demo-db-marwen (Render PostgreSQL 17)
```

The external `topmed-demo-embed-marwen` static site proves that `widget.js` and the iframe work from a separate origin. All resources use free Render instance types. Runtime services and PostgreSQL live in Frankfurt; the static site is global.

## Provisioning

1. Push `develop` and create a Render Blueprint from the repository's `render.yaml`.
2. Confirm that every planned resource uses a free instance type.
3. Supply only the two secrets requested by Render:
   - `DEEPSEEK_API_KEY`
   - `ADMIN_BOOTSTRAP_PASSWORD`
4. Render generates `WIDGET_TOKEN_SECRET`; the widget integration identifier is public by design.
5. Wait for the backend startup command to migrate PostgreSQL, create the administrator, import the canonical corpus, generate embeddings, activate the initial KB, verify readiness, and then start FastAPI.

The pre-deploy bootstrap is idempotent. Subsequent deploys preserve governed active KB, prompt, and settings versions.

## Runtime configuration

The frontend proxies API and SSE traffic to FastAPI's public Render URL. This keeps administrator cookies same-origin even before custom domains are added. FastAPI still validates the canonical frontend `Origin`, CSRF token, session, and role.

The backend image contains a pinned, checksum-verified 16M-parameter Model2Vec distillation of multilingual-e5-small. Its native vectors are normalized and zero-padded to the schema's 384 physical dimensions. Retrieval remains lexical-first hybrid and adds bounded cross-document companion routing for the evaluated policy intents. Local development and release evaluation continue to exercise the full ONNX model. No mutable model cache or persistent web-service disk is required.

Free-service constraints are intentional for this demo:

- web services sleep after 15 idle minutes and can take about one minute to wake;
- sleeping or redeploying terminates SSE connections, which clients reconnect automatically;
- startup reruns the idempotent bootstrap because pre-deploy commands are paid-only;
- free PostgreSQL is limited to 1 GB and expires after 30 days;
- free web services do not accept private-network traffic.

## Release verification

Verify these URLs after every release:

```text
https://topmed-demo-api-marwen.onrender.com/health
https://topmed-demo-api-marwen.onrender.com/ready
https://topmed-demo-web-marwen.onrender.com/chat
https://topmed-demo-web-marwen.onrender.com/admin/login
https://topmed-demo-embed-marwen.onrender.com
```

Required checks:

- database migration reports `0009_audit_feedback`;
- `vector` and `pg_trgm` are ready;
- 15 documents and 30 embedded chunks are active;
- DeepSeek and the local embedding runtime are ready;
- direct chat returns a verified answer with exact citations;
- external iframe launcher opens and reconnects through SSE;
- human takeover suppresses AI and returns a human reply;
- review-before-send keeps proposals private until approval;
- audit and feedback records are visible only to authorized roles.

## Rollback and recovery

- Application rollback: use Render's service rollback to the previous successful image.
- Database rollback: do not downgrade automatically. Restore through Render Postgres point-in-time recovery when data recovery is required.
- Knowledge or tuning rollback: use the authenticated TopMed admin workflow, which preserves provenance and audit events.
- Failed startup: inspect the backend deploy logs, correct configuration, and redeploy. The database bootstrap is idempotent.

Never place DeepSeek credentials, administrator passwords, database URLs, session secrets, or generated release credentials in Git, build arguments, logs, or evaluation reports.
