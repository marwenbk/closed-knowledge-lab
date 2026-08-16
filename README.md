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
