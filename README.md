# Questionnaire Evidence & Review Workspace

Take-home **Alternative C · Answer & approve**. Starter pack version `2026-10-02`. All data is fictional.

A sales team receives buyer questionnaires. This local app drafts answers from the company's
fictional product documents using Claude. It shows the supporting passages side by side, flags
missing or conflicting evidence, and routes unresolved questions to the reviewer named in the
topic mapping. A person corrects and approves answers. An exact repeat of an approved question then
reuses the approved wording together with its approval and source records. Approvals go back to
review when a cited document's version changes.

> **Results with the real model:** all **5/5 reference cases** and **6/6 additional checks** pass
> (see [`evaluation/results.md`](evaluation/results.md)). They come from 21 real `claude-opus-5-5`
> responses recorded on 2026-10-05 through the LangChain integration, about 37k input and 3.7k
> output tokens (≈ $0.22), with no refusals or fallbacks. The responses are saved in `replays/`. In a fresh clone with no `.env` file,
> `python -m qra evaluate --mode replay` reproduces the same result without an API key.

---

## Quick start

Requirements: Python 3.10+ (developed on 3.11.4) and no other services.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

Run the offline test suite (no API key; uses a labelled fake model client):

```bash
.venv/bin/python -m pytest -q
```

### Record real responses (needs an API key)

```bash
cp .env.example .env          # then put your key in .env (git-ignored)
```

```bash
.venv/bin/python -m qra evaluate --mode auto
```

`auto` replays a saved response when one exists and otherwise calls the API and saves it to
`replays/`. One full run makes about 20–25 calls to `claude-opus-5-5` (about $1 at list price).
The command runs all reference cases in a throwaway database and writes `evaluation/results.md`
and `evaluation/results.json`. Commit `replays/` and `evaluation/` afterwards.

### Replay without an API key

```bash
.venv/bin/python -m qra evaluate --mode replay
```

This uses only the saved real responses. Each model call is labelled `replay` in the results and
the UI, with the recording time and the replay file path.

### Use the review workspace

```bash
.venv/bin/python -m qra init --reset
```

```bash
.venv/bin/python -m qra demo --mode replay
```

```bash
.venv/bin/python -m qra serve --mode replay
```

Open http://127.0.0.1:5050. The `demo` command runs the seed questionnaire, makes the scripted
reviewer actions (approve a corrected Q1, approve Q3, save an *unapproved* edit of Q4) and runs
questionnaire B. To see invalidation, go to **Documents → Publish**
(`source-change-support-v1-version-2.json`); the Q3 approval and the items that reused it move to
review. Use `--mode live` or `--mode auto` to let the UI make new model calls.

Other commands: `run FILE [--simulate Q5=invalid_output]`, `ask "text" --topic support`,
`approve seed/Q1 --by NAME [--answer ...] [--cite PASSAGE]`, `edit`, `unresolve`, `redraft`,
`publish FILE`, `export seed` (approved answers with evidence, plus open items; also linked from
the queue when a questionnaire filter is selected), `status`, `export-state`. Run `python -m qra -h` for details.

---

## Architecture

```
data/starter/seed.json ─┐                              ┌─ replays/<purpose>/<hash>.json (real responses)
data/additions/*.json ──┤ corpus.py: load + validate   │
                        ▼                              │
                  store.py (SQLite: document revisions, items, drafts, model calls, approvals, events)
                        ▲
questionnaire.json ─► workflow.py ── 1. exact-match lookup of an ACTIVE approval whose sources still match
                        │               └─ yes → reuse wording + approval record (no model call)
                        │            2. llm.py  draft_answer  (Claude, JSON schema)  ─┐
                        │            3. evidence.py  deterministic checks (code)       │ record / replay
                        │            4. llm.py  verify_answer (Claude, JSON schema)  ─┘
                        │            5. status + reasons + owner from topic mapping
                        ▼
              web.py (Flask) review queue · cli.py · evaluate.py (reference cases → evaluation/)
```

| Module | Responsibility |
|---|---|
| `qra/corpus.py` | Load documents and questionnaires; validate IDs, duplicates, `supersedes` targets and cycles; derive authority **only** from `supersedes` |
| `qra/drafting.py` | Prompts (`qra/prompts/*.v1.md`), JSON schemas, message building. Document text goes in the user message inside escaped `<documents>` tags, separate from the system instructions |
| `qra/llm.py` | Live calls through LangChain (`ChatPromptTemplate` piped into `ChatAnthropic`); `replay` / `live` / `auto` modes, error handling, refusal and truncation detection, and labelled simulated failures |
| `qra/evidence.py` | Code checks: citations exist, excerpts appear in the passage, no superseded support, conflicts resolved only by metadata, interpretation of the verifier's verdicts |
| `qra/workflow.py` | Processing, reuse, approval, unapproved edits, leave-unresolved notes, document revisions, approval invalidation, analytics |
| `qra/store.py` | SQLite schema. Model drafts, reviewer edits and approvals are separate tables |
| `qra/web.py` + `templates/` | Queue with status filters and counts; item page with draft, checks and evidence side by side; approvals library; documents and revisions |
| `qra/evaluate.py` | Runs the reference cases and compares them with `reference/reference-cases.json` |

### Statuses

| Status | Meaning |
|---|---|
| `answered` | The model draft passed every check and is waiting for a reviewer. It is **not** approved knowledge |
| `unresolved` | Needs a reviewer: missing evidence, an unresolved conflict, a failed check, an approval whose source changed, or a reviewer note. Routed to the owner from the topic mapping |
| `approved` | Approved by a reviewer here, or an exact repeat that reused an active approval (`reused`) |
| `failed` | Model or API failure, invalid output, or a missing replay. The reason is shown and nothing is invented |

### How each rule is enforced

| Domain rule | Mechanism |
|---|---|
| 1. Only supplied documents; undocumented means unknown | Prompt rule plus a second model pass that marks claims `not_supported` (for example, a "no" inferred from silence). Unresolved drafts carry no answer text; failed claims route to review |
| 2. Authority only through `supersedes`; keep replaced text | Authority is derived in code from `supersedes`; `status` and dates are informational. Citing a superseded passage fails a check. A disagreement between *current* documents is never auto-resolved. Replaced text and earlier revisions stay visible |
| 3. Only approved answers are reused; source change means review | Approvals live in their own table with source `(doc, version, revision, text hash)`. Reuse requires `status = active` and matching sources. Edits are drafts. Publishing a revision re-checks every approval, including replaced ones that items still rely on. Unchanged wording drafted against an older document version cannot be approved until it is redrafted or edited |
| 4. Topic → reviewer mapping | Owner comes from `seed.json` `owners`. An unknown topic gets "no owner mapped" instead of an invented owner |

### Model configuration (application)

| Setting | Value |
|---|---|
| Provider / library | Anthropic Messages API through LangChain: `ChatPromptTemplate | ChatAnthropic` (`langchain-anthropic` 1.7.5, `langchain-core` 1.6.6) on top of the `anthropic` Python SDK 1.11.0 |
| Model | `claude-opus-5-5` (override with `QRA_MODEL`) |
| Output | Structured outputs: `output_config.format = json_schema` (schemas in `qra/drafting.py`), then validated again with pydantic |
| Thinking / effort | Adaptive thinking (always on for this model); `effort: medium` for both passes (`QRA_DRAFT_EFFORT`, `QRA_VERIFY_EFFORT`) |
| Limits | `max_tokens` 16000, timeout 120 s, 2 retries |
| Refusal handling | `fallbacks: "default"` with beta `server-side-fallback-2026-07-01`; `stop_reason` `refusal` / `max_tokens` become visible failures |
| Temperature | Not set (the API does not accept sampling parameters for this model) |
| Prompts | `qra/prompts/draft_answer.v1.md`, `qra/prompts/verify_answer.v1.md` |

Why LangChain's `output_config` rather than `with_structured_output()`? By default
`with_structured_output()` forces a tool call, and `claude-opus-5-5` rejects forced tool calls. So the
API's own structured-output setting is passed through `ChatAnthropic(output_config=...)` and the JSON is
validated with pydantic. Before every live call, `llm.py` asks LangChain for the exact request it will
send and refuses to call if it differs from the request being fingerprinted and saved. A unit test
checks the same thing offline. All 21 recordings in `replays/` were made through this LangChain
integration. An earlier set, made with a direct SDK call before the switch, passed the same checks.

Why two passes? The drafting model can cite a real passage and still overstate it, for example
answering "No" to SSO because sign-in is "email and password". Code can confirm that a citation
exists and that its excerpt is real, but not that the passage *supports* the claim. The second pass
judges each claim against only the cited passages and lists any current passage that disagrees.
Its verdicts are then interpreted in code.

---

## Data, reference cases and results

- `data/starter/`: a verbatim copy of `tasks/evidence/` (never edited).
- `data/additions/`: handwritten additions, each tied to a check. See [`data/GENERATION.md`](data/GENERATION.md).
- `reference/reference-cases.json`: five reference cases plus six additional checks, written from
  source inspection **before** the app existed.
  [`reference/VERIFICATION.md`](reference/VERIFICATION.md) explains each expected value.
- `evaluation/results.md`: expected versus observed results with the provenance of every model call
  (generated by `python -m qra evaluate`).

The five reference cases match the brief's minimum demonstration:

| Case | Input | Expected |
|---|---|---|
| RC1 supported | Q3 email-support hours | `answered`; cites SUPPORT-v1:p1 only; excerpts verbatim; second pass says supported |
| RC2 unsupported | Q2 JSON export | `unresolved`; Product reviewer; no yes/no claim |
| RC3 superseded conflict | Q1 free-plan CSV | `answered` "No"; cites EXPORT-v2:p1, not v1; EXPORT-v1 shown as replaced text |
| RC4 reuse | Approve corrected Q1, unapproved edit of Q4, then questionnaire B | QB1 reuses the exact approved wording with approval and sources and no model call; QB3 does not get the unapproved edit |
| RC5 reload + source change | New process reads state; SUPPORT-v1 v1→v2; ask Q3 again | Approvals survive restart; Q3 approval `needs_review`, Q1 approval stays `active`; the repeat is `unresolved`, not reused |

Additional checks: an unresolved conflict between two current documents (date alone does not win),
SSO not inferred as "no", a reworded question not reused, and labelled simulated failures (invalid
output, API error, fabricated citation).

## Ambiguities and assumptions

1. **IDs that contain versions.** The seed IDs embed a version (`EXPORT-v1`) and also have a
   `version` field. I treat `id` as the stable identity. A change of `version` on the same ID, or a
   new document that supersedes it, both count as a source version change.
   `source-change-support-v1-version-2.json` therefore publishes "SUPPORT-v1, version 2".
2. **`status` vs `supersedes`.** Rule 2 gives authority only to `supersedes`, so the seed's
   `status` field is informational. If the two disagree, a validation warning is shown. They agree
   in the supplied data.
3. **Exact question matching.** Reuse matches the question text after trimming, collapsing
   whitespace and ignoring letter case. Any other wording difference, including punctuation, is a
   different question (QB6 shows this).
4. **What counts as a change.** Any version change of a cited document invalidates the approval,
   even when the cited sentence itself is unchanged (the brief's "simple version comparison"). As a
   safeguard, a text change without a version bump is also detected by hash.
5. **A documented "no" is not "undocumented".** "Live chat is not offered" supports "No" (Q4).
   JSON export (Q2) and SSO (QB5) are not mentioned, so they are unknown.
6. **Superseded passages cannot support an approval.** The rules do not say this explicitly for
   reviewers, but rule 2 makes the superseding document authoritative. Approval is rejected if a
   superseded passage is selected as support.
7. **Items that reused a now-stale approval** go back to `unresolved` so they appear in the
   queue; the approval history is kept.
8. **Matching answers in the answer key.** Model wording varies, so the expected results check
   status, citations, polarity ("No") and key facts (for example "09:00", "UTC") rather than exact
   sentences. The reviewer-approved wording *is* compared exactly.
9. **Reviewer identity** is free text. Authentication is out of scope.
10. **"Leave unresolved" on an approved answer** retires that approval (`needs_review`), so it is no
    longer reused. The rules do not say this explicitly; I treated a reviewer withdrawing an answer
    as withdrawing it from reuse too.
11. **Republishing without a version bump** (for example, removing a `supersedes` link) also sends
    approvals that cite the document to review, because the document's authority may have changed.

## Known limitations

- The real-model results come from one recorded run. A new live run could word answers
  differently; the checks are designed for that (status, citations, polarity, key facts), but a
  live re-run may not match the recordings word for word.
- Five reference cases and six extra checks are a small demonstration. They do not establish
  accuracy on other questions or documents.
- The verifier is the same model family as the drafter, so their errors can be correlated. The
  code checks catch structural errors (missing, fabricated or superseded citations) but not every
  semantic mistake.
- Reviewer edits are not re-checked by the model. Citations in edits are checked in code, and the
  approving reviewer is accountable for the wording.
- Reuse is exact-match only. Suggesting approved answers for reworded questions was optional and is
  not implemented.
- The full corpus goes into every prompt, which suits seven short documents; a larger corpus would
  need retrieval (see [How this would scale](#how-this-would-scale)). Replays are keyed by the
  exact request hash, so any prompt or document change needs re-recording (a missing replay is
  reported, never guessed).
- Single-user local SQLite with one connection; the web server handles one request at a time, and
  there is no authentication. Multi-step writes such as publishing a revision and then re-checking
  approvals are not wrapped in a single transaction. Reuse re-checks sources at lookup time as a
  second line of defence. Document revisions published in the UI live in the database only;
  `init --reset` restores the seed.

## How this would scale

**Why there is no retrieval (RAG) today.** The brief says the collection fits in one request and
that "keyword lookup or full-context input is sufficient". Full context also protects the rules:
- **Conflicts stay visible.** Rule 2 is about conflicts between documents. A retriever that
  returns only the top matches could drop exactly the superseded or conflicting passage a reviewer
  needs to see. For example, it might return EXPORT-v2 without EXPORT-v1 for Q1, or one of the two
  scheduled-export documents for QB4, and the conflict would silently disappear.
- **The second pass needs everything.** It looks for *any* passage that disagrees with the answer.
- **"Undocumented" stays trustworthy.** It really means no document mentions the feature, not that
  a search missed it.

**With thousands of documents**, I would add retrieval with these safeguards:
1. **Build the candidate set in code.** Use the top-k passages from a search over the question,
   plus every document linked to them through `supersedes`, in both directions. That way
   superseded text and its replacement always travel together.
2. **Search for disagreement separately.** Before the second pass, search for passages similar to
   the draft *answer*, not just the question, so possible contradictions reach the verifier.
3. **Record what was retrieved** with each draft: query, passage IDs and scores. Reviewers can then
   see what the model was shown, and replays stay reproducible.
4. **Treat a weak retrieval as missing evidence.** If no passage scores above a threshold, return
   `unresolved` instead of letting the model answer from weak matches.
5. **Measure retrieval on its own.** For each reference case, check that the passages in the answer
   key appear among the retrieved passages, before judging the answers.

The rest of the design would not change: authority from `supersedes`, the code checks, approval
records with source versions, and invalidation when a version changes.

## Time spent

**Total: about 7 hours**, from 2026-10-05 to 2026-10-06, within the 8-hour limit.

What the time covered:
- **2026-10-05:**
  - reading the brief, data preparation and the answer key, building the application
  and tests with Claude Code, an independent code review and fixes, documentation, and recording
  the real model responses (85 seconds of API time) and reading them.
  - a readability rewrite with the same behaviour;
  - switching the model calls to LangChain and re-recording all responses;
  - the scaling section;
  - docstrings for every function.
- **2026-10-06:** final checks and preparing the submission.
- **Throughout:** going through the code module by module to understand it.

## Repository layout

```
qra/                 application package (CLI: python -m qra)
qra/prompts/         versioned model prompts
data/starter/        supplied seed, rules and templates (unchanged)
data/additions/      added documents, questionnaire B, source-change scenario
reference/           answer key + how it was verified
evaluation/          generated results (expected vs observed)
replays/             saved real model responses (request + response) for offline replay
tests/               offline tests (fake model client)
docs/                LLM usage note and written walkthrough
ai-workflow/         AI configuration manifest and README
CLAUDE.md, .claude/  coding-agent instructions, permissions and the data-generation skill
```
