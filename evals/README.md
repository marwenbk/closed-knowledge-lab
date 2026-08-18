# Evaluation Data

`cases.yaml` is generated from `data/eval_blueprints.yaml` and `data/fact_catalog.yaml`.

Do not edit the generated file directly. Regenerate it with:

```bash
.venv/bin/python scripts/generate_evals.py --force
```

The generator verifies unique IDs, stable fact references, expected source documents, isolated conflict fixtures, a minimum of 95 total cases, and every category minimum defined in `data-generation.md`.

Run the repeatable local retrieval gate and write its machine-readable report with:

```bash
.venv/bin/python -m app.cli eval run \
  --output evals/retrieval.report.json
```

Add `--live` only when intentionally running all pipeline and adversarial cases through DeepSeek. Live runs consume API credit. Evaluation reports match `evals/*.report.json` and are ignored by Git.
