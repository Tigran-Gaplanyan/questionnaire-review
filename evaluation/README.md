# Evaluation output

`python -m qra evaluate --mode {replay|auto|live}` writes `results.md` (human-readable) and
`results.json` (machine-readable) here. Expected values come from `reference/reference-cases.json`;
observed values come from the application's saved state in a throwaway database.

**Current state:** `results.md` and `results.json` come from the real-model run on 2026-10-05 (recorded through LangChain):
5/5 reference cases and 6/6 additional checks. To reproduce them from the saved responses,
without an API key:

```bash
.venv/bin/python -m qra evaluate --mode replay
```

The model-call provenance in this run reads `live` for new calls. QB3 shows `replay` because it is
the same request as Q4 earlier in the same run, so the response recorded minutes earlier was reused.

The evaluator itself is tested offline in `tests/test_evaluate_harness.py`. A correct fake
application passes every case, and a fake application that answers "Yes" to the undocumented
JSON question fails RC2.
