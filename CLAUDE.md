# Project instructions for coding agents

This repository is a take-home submission (Alternative C, "Questionnaire Evidence & Review
Workspace"). These instructions apply to any coding assistant working in it.

## Source of truth
- `data/starter/domain.md` and `data/starter/seed.json` are the fixed, fictional rules and seed data.
  Do not edit anything under `data/starter/`. Do not add real-world security, legal or compliance rules.
- If a rule is unclear, record the ambiguity in `README.md` ("Ambiguities") instead of inventing policy.

## Answer keys stay independent
- Expected results live in `reference/` and are written from source inspection, never from
  application output. Do not "fix" a failing check by editing `reference/reference-cases.json`;
  investigate the application instead and record unresolved failures in `evaluation/`.

## Model integration
- Every model call goes through `qra/llm.py`, which records real responses under `replays/`.
- Never hardcode answers, passage IDs or approvals for the seed questions in application code.
- Simulated failures must be labelled `simulated` and must never be written to `replays/`.
- Never read, print or commit `.env` or API keys. Variable names belong in `.env.example`.

## Checks before committing
- `.venv/bin/python -m pytest -q` (offline; uses a fake model client)
- `.venv/bin/python -m qra evaluate --mode replay` (replays saved real responses; no API key)
