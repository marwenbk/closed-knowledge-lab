# Evaluation Data

`cases.yaml` is generated from `data/eval_blueprints.yaml` and `data/fact_catalog.yaml`.

Do not edit the generated file directly. Regenerate it with:

```bash
.venv/bin/python scripts/generate_evals.py --force
```

The generator verifies unique IDs, stable fact references, expected source documents, isolated conflict fixtures, a minimum of 95 total cases, and every category minimum defined in `data-generation.md`.
