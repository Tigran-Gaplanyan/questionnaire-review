# AI workflow used during this exercise

Machine-readable record: [`manifest.json`](manifest.json). Statuses are `used`, `default`,
`not-used` or `not-exportable`.

## Tools and models

**Development time**
- **Claude Code desktop app (Code tab)**, model `claude-opus-5-5`, default model settings. The
  desktop app's bundled skills are version 2.1.286; the `claude` CLI on PATH reports 2.1.282.
  It generated the fixtures, application code, tests and documentation, and ran the tests and
  checks. The repository is submitted as a single commit under the candidate's name.
- **Claude Browser pane**, built into the desktop app. I used it to look at the running review UI;
  this is how a real defect was found (see "One workflow example").
- **Permission mode:** auto. This user-level setting is not copied.

**Inside the application** (see `qra/config.py`)
- Anthropic Messages API through LangChain: `ChatPromptTemplate | ChatAnthropic` (`langchain-anthropic==1.7.5`,
  `langchain-core==1.6.6`) over `anthropic==1.11.0`. Structured output goes through
  `ChatAnthropic(output_config=...)`, not `with_structured_output()`, which forces a tool call that
  this model rejects. The request LangChain sends is checked before every live call and in
  `tests/test_llm.py`. All 21 recordings were made through LangChain on 2026-10-05; an earlier set
  made with the direct SDK passed the same checks.
- Model `claude-opus-5-5`; structured outputs (`output_config.format: json_schema`); adaptive
  thinking (always on for this model); `effort: medium`; `max_tokens: 16000`; 120 s timeout; 2
  retries; `fallbacks: "default"` with beta `server-side-fallback-2026-07-01` for safety-classifier
  refusals. Temperature is not set because the API rejects sampling parameters for this model.
- Two prompts: `qra/prompts/draft_answer.v1.md` and `qra/prompts/verify_answer.v1.md`.

## Configuration files

| File | Kind | Status | Notes |
|---|---|---|---|
| `CLAUDE.md` | Agent instructions | used | Project rules for any coding agent: starter data is read-only, answer keys stay independent, no hardcoded answers, simulated output never goes in `replays/`, never read `.env` |
| `.claude/settings.json` | Permissions | used | Allows the test and app commands; denies reading `.env` and `git push` |
| `.claude/skills/generate-assignment-data/SKILL.md` | Skill | used | From the starter pack, unchanged; used as the fixture checklist |
| claude-api skill | Skill | not-exportable | Bundled with Claude Code 2.1.286; used as the SDK and model reference before writing `qra/llm.py` |
| `qra/prompts/*.v1.md` | Prompts | used | Application system prompts; the prompt name is stored with each model call |
| `qra/config.py` | Model settings | used | Model ID, effort, limits, fallback beta, modes |
| `.env.example`, `ai-workflow/.env.example` | Env names | used | `ANTHROPIC_API_KEY` plus optional `QRA_*` overrides; values blank |
| Subagent (built-in general-purpose) | Subagent | used | One read-only review of `qra/` against `domain.md`. No custom definition file; the prompt is summarised in `manifest.json` |
| Hooks, MCP servers | — | not-used | None were needed for a single-module app |
| `.claude/launch.json` (starter-pack root, outside this repo) | Preview launcher | default | Not committed because it contains absolute local paths; it only ran `python -m qra serve` |

**Restoring the setup.** Clone the repo and open it in Claude Code. `CLAUDE.md`,
`.claude/settings.json` and `.claude/skills/` are picked up automatically from their normal
locations. These files have not changed since they were first written: `CLAUDE.md`, the
prompts (`v1`) and `.claude/settings.json`. The model integration (`qra/llm.py`) changed once,
from a direct Anthropic SDK call to LangChain, sending a byte-identical request.

**Redactions and omissions.** No user-level configuration was copied: no personal settings files,
account details or unrelated MCP servers. The API key was never seen by the coding assistant or
written to the repository. The app reads it from the environment or a local, git-ignored `.env`,
and the project permissions deny the assistant from reading that file.

## One workflow example

**Instruction (from the candidate):** complete Alternative C from the attached brief and starter
pack, using the Anthropic API.

**Configuration that affected it:**
- the claude-api skill, which supplied the current model ID, structured outputs and the refusal
  fallback;
- the `generate-assignment-data` skill, which said to write expected results separately and to
  invent no policy;
- `CLAUDE.md`.

**How the result was checked:**
1. **Answer key before code.** `reference/reference-cases.json` was written before any
   application code existed. A small independent script confirmed that no passage mentions JSON
   or SSO and that every ID referenced in the key exists.
2. **Tests with teeth.** After the first test run (23/23 passing), I disabled the version
   comparison and the conflict classification in turn. Each mutation made two tests fail, which
   showed the tests check the rules and not just run the code.
3. **Testing the evaluator.** `tests/test_evaluate_harness.py` gives the evaluator a fake
   application that says "Yes" to JSON export. The evaluator correctly fails RC2 and nothing else.
4. **Independent review.** A read-only subagent reviewed the code against `domain.md` and reported
   3 bugs plus 4 accepted concerns. Each was first reproduced by a failing test, then fixed
   (`tests/test_review_findings.py`).
5. **Real failure path.** The first live call ran without credentials. The app recorded it as
   `failed (api_error)` with the SDK's message and invented no answer.

**Correction:** while viewing the item page in the Browser pane, I noticed that after Q1 was
approved, the "replaced text" panel for EXPORT-v1 disappeared. The current draft had become the
reviewer edit, which carries no conflict data, and RC3 requires the outdated policy to stay
visible. The fix derives the replaced text from whatever the current draft cites and
takes conflicts from the latest model draft. A regression test was added and confirmed to fail
without the fix (`git stash` of the fix → `1 failed`).

## Reproduce or replay

| What | Command | Needs |
|---|---|---|
| Install | `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` | Python 3.10+ |
| Offline tests | `.venv/bin/python -m pytest -q` | nothing |
| Replay saved real responses | `.venv/bin/python -m qra evaluate --mode replay` | recordings in `replays/` |
| Record (or top up) responses | `.venv/bin/python -m qra evaluate --mode auto` | `ANTHROPIC_API_KEY` in `.env` |
| UI on replays | `python -m qra init --reset && python -m qra demo --mode replay && python -m qra serve` | recordings |

Replays are keyed by a SHA-256 hash of the full request: model, prompt, documents, question,
schema and settings. Each file in `replays/` holds the exact request and the unedited API response,
labelled `recorded-live-response`. Every model call in the UI and in `evaluation/results.md` is
labelled `live`, `replay`, `simulated` or `test-fake`. No hooks run anything.

## Decisions and limitations

- **A default setup was enough.** One focused `CLAUDE.md`, a deny rule for `.env`, the starter
  skill and the bundled API reference covered the task. The only extra agent was a one-off
  read-only reviewer, used because a second reader finds different bugs than the author; it found
  three. Custom agents or hooks would have added nothing to a one-module app.
- **Why the Anthropic API and these settings:** the hiring team's agreed provider. I chose
  structured outputs so that malformed JSON is rare and is caught by pydantic when it does occur.
  Medium effort is the model's default and is enough for short extraction and judgement over
  seven passages.
- **What I would change next:** try `effort: low` for the verifier and compare it on the reference
  cases (the recorded run used about 37k input and 3.7k output tokens, so cost is already small);
  add a few harder questions where the model is more likely to over-answer; add a pre-commit check
  that `replays/` contains no `simulated` provenance.
