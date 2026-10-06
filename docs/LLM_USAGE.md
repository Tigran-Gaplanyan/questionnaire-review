# LLM usage note

## In short
- **Claude Code wrote nearly all of the code, data additions and documentation.** I directed the
  work, made the product and technical decisions below, set the guardrails, and treated every AI
  output as unverified until something independent confirmed it: an answer key written first,
  tests that are shown to fail when a rule is broken, a second reviewer, or reading the real
  outputs myself.
- **Inside the application, the model only proposes.** Code checks every quote and every conflict,
  and a person approves before anything is reused.
- **Every real model response is saved and replayable without an API key.** The full real-model
  evaluation (21 calls) cost about $0.22.

Configuration details: [`ai-workflow/README.md`](../ai-workflow/README.md) and
[`ai-workflow/manifest.json`](../ai-workflow/manifest.json).

## Tools and models
| Where | Tool / model | Used for |
|---|---|---|
| Development | Claude Code desktop app, `claude-opus-5-5` (default settings, auto permission mode) | Writing code, data, tests and docs; running checks |
| Development | `claude-api` skill (bundled with Claude Code) | Current model IDs and SDK features, instead of relying on the model's memory of an API that changes often |
| Development | `generate-assignment-data` skill (starter pack) | Checklist for extending the fixtures without inventing policy |
| Development | A separate read-only review agent | Independent code review against `domain.md` |
| Development | Built-in browser pane | Looking at the running UI |
| Application | `claude-opus-5-5` through LangChain (`ChatPromptTemplate \| ChatAnthropic`, `langchain-anthropic==1.7.5`) over `anthropic==1.11.0` | Pass 1: draft with quotes. Pass 2: check each claim. Structured JSON output, effort medium, refusal fallback |

## Guardrails I set up for the AI tools
- **Project rules for the agent (`CLAUDE.md`).**
  - Starter data is read-only.
  - The answer key is never changed to make a test pass.
  - No hardcoded answers.
  - Simulated failures are never saved as real responses.
  - `.env` is never read.
- **Least privilege (`.claude/settings.json`).** The coding agent can run the tests and the app,
  but cannot read `.env` or `git push`. I put the API key in `.env` myself and pushed the code
  myself. When the agent once tried to search shell configuration for API-key names, the
  permission system blocked it, and the key was provided through `.env` instead.
- **No accidental spending.** The app defaults to `replay` mode. Live calls happen only with
  `--mode live` or `--mode auto`.
- **Disclosure.** AI use is documented here and in `ai-workflow/`, as the brief requires.

## Generated components
Claude Code generated these; I reviewed them and can explain them:
- **Fixtures:** `data/additions/*`, `data/GENERATION.md`.
- **Answer key:** `reference/*`.
- **Application:** `qra/*`, including the prompts and templates.
- **Tests:** `tests/*`.
- **Documentation:** `README.md`, `docs/*`, `ai-workflow/*`.

`data/starter/` is unchanged.

## Representative workflow
My first instruction was the brief and the starter pack, with the task to complete the assignment.
I chose **Alternative C** and the **Anthropic API**. The loop Claude Code used for every part of the work:

1. **Specify.** Read `domain.md`, the seed and the expected results; turn the four rules into
   concrete checks.
2. **Answer key first.** Write the five reference cases from the source passages before any
   application code existed, and check the fixtures with a small independent script (for
   example, that no passage mentions JSON or SSO).
3. **Generate.** Have Claude Code build each module with offline tests, using a fake model.
4. **Verify the verifiers.** Prove the tests and the evaluator can fail (see below), then review
   the code with a second agent.
5. **Run for real and read the outputs.** Record the real model responses and read every answer
   against its passage, not just the pass marks.
6. **Change safely.** Before any refactor, take a baseline; after it, require identical results.

## Decisions I made, and why
| Decision | Reason |
|---|---|
| Two model passes plus code checks, instead of one call | Code can confirm a quote exists, but not that it *supports* the claim. The second pass judges each claim, and code turns its verdict into pass or fail. |
| Structured output (JSON schema) plus pydantic validation | Malformed output becomes a visible `failed` status, never a guessed answer. |
| Replay keyed on a fingerprint of the exact request | A saved answer can only be replayed for exactly the same prompt, documents and question. Reviewers need no API key. |
| LangChain for the model calls | I switched to LangChain to use the standard framework. I did **not** use `with_structured_output()`: it forces a tool call, which `claude-opus-5-5` rejects. I passed the API's own JSON-schema setting through `ChatAnthropic` instead, and added a guard that refuses to call if LangChain's request differs from the one being recorded. I then re-recorded and re-verified everything. |
| No RAG (retrieval) | The brief says full context is sufficient. More importantly, retrieval could drop exactly the superseded or conflicting passage a reviewer must see. I documented how I would add retrieval safely at scale (README, "How this would scale"). |
| A readability rewrite before submitting | Easier to explain and maintain. I required identical behaviour, proven by comparing every answer, status and quote with a baseline replay. |

## Verification, and what it caught
These checks ran in my Claude Code sessions. What matters is the process, which I can rerun and
explain.

1. **The evaluator can fail.** A fake app that answers "Yes, JSON export is available" fails RC2,
   and only RC2.
2. **Bug caught on re-reading AI-written code.** A contradiction reported by the verifier was
   classified alone, so a conflict between two current documents would have slipped through.
   Fixed, with a test.
3. **Bug caught by looking at the UI.** After approving Q1, the replaced EXPORT-v1 text vanished
   from the page, but rule 2 requires it to stay visible. Fixed, with a regression test confirmed
   to fail without the fix.
4. **Bug caught by taking a baseline before refactoring.** The baseline unexpectedly passed only
   4/5. The cause was the AI-written `.env.example`: a relative `QRA_DB` path crashed any fresh
   process. Fixed, with a regression test, and settings now load from `.env` before use.
5. **The LangChain switch, verified twice.** Offline, a test checks that LangChain builds exactly
   the recorded request. Live, one real call produced the same request fingerprint as the existing
   recording. Then all 21 responses were re-recorded; that run also passed 5/5 and 6/6, and every answer was
   read again.
6. **Reading the real outputs.** All Q1–Q8 and QB1–QB6 answers match their passages. JSON and SSO
   stay unresolved with no yes/no claim, and after the document change the fresh draft uses the
   new 08:00–18:00 hours. The model also added unrequested (but supported) detail to the
   live-chat answer. That is why approvals are invalidated on *any* version change of a cited
   document.

## Testing it myself
I used the running app with my own questions:
- **Replay mode.** A question that was never recorded produced a visible `failed (replay_miss)`,
  not an invented answer.
- **Auto mode.** I asked *"Can users who don't have an account invite team members?"*. The model
  left it unresolved: the documents say account owners can invite, but not whether anyone else
  can. I decided **not** to approve a "No" answer, because that would infer "no" from silence,
  which is exactly what rule 1 forbids. The right fix is to document the policy, redraft, and then
  approve, so the approval rests on a source and is re-checked if that source changes.

## Limits and next steps
- The verifier uses the same model family as the drafter, so their errors can be correlated. Next
  I would add harder questions designed to tempt over-answering.
- I would try a lower `effort` for the verifier and compare it on the reference cases.
- I would add a pre-commit check that `replays/` contains no `simulated` responses.

## How to check this yourself
- Read `evaluation/results.md` and compare a few model answers with the passages in
  `data/starter/seed.json`.
- Change an input and watch the effect: in the web app, publish a new version of a document on
  the Documents page. Approved answers that cite it move to "needs review" and are no longer
  reused.
