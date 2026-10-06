# Evaluation results

Generated 2026-10-05T13:51:31+00:00 by `python -m qra evaluate --mode auto` (model `claude-opus-5-5`).
Expected values come from `reference/reference-cases.json`; observed values come from the application's saved state.

**Reference cases: 5/5 passed. Additional checks: 6/6 passed.**

Model-call provenance per item is listed under each case: `live` = new API call, `replay` = saved real response, `simulated` = injected failure (not a model response). Five reference cases are a small demonstration and do not establish accuracy on other questions or documents.

| Case | Result | Brief check |
|---|---|---|
| RC1-supported | PASS | A supported question receives a draft answer whose cited passages exist and support its claims. |
| RC2-unsupported | PASS | The unsupported question stays unresolved and offers an appropriate review route without inventing a product capability. |
| RC3-superseded-conflict | PASS | The outdated policy's conflict is visible, and the answer uses the document that explicitly supersedes it. |
| RC4-approved-reuse | PASS | A reviewer's approved correction appears when the same question is asked again; an unapproved edit is not reused as approved knowledge. |
| RC5-reload-and-source-change | PASS | Reloading preserves approval and evidence. Changing a referenced source version makes its approved answer require review. |
| AC1-unresolved-conflict-no-supersedes | PASS | domain.md rule 2: a newer date alone does not establish authority. Neither document supersedes the other. |
| AC2-undocumented-not-negative | PASS | domain.md rule 1: ACCESS-v1:p1 documents email/password sign-in but says nothing about SSO, so SSO is unknown, not unsupported. |
| AC3-reworded-question-not-reused | PASS | Exact-match reuse only (brief). The wording differs from Q1, so a fresh draft is produced; EXPORT-v2:p1 still answers it. |
| AC4-simulated-invalid-output (simulated) | PASS | Simulated failure (labelled SIMULATED; not a real model response) |
| AC5-simulated-api-failure (simulated) | PASS | Simulated failure (labelled SIMULATED; not a real model response) |
| AC6-simulated-fabricated-citation (simulated) | PASS | Simulated failure (labelled SIMULATED; not a real model response) |

## RC1-supported — PASS

| Check | Expected | Observed | |
|---|---|---|---|
| status | answered | answered | ✅ |
| cites required passages | ["SUPPORT-v1:p1"] | ["SUPPORT-v1:p1"] | ✅ |
| cites nothing else | ["SUPPORT-v1:p1"] | ["SUPPORT-v1:p1"] | ✅ |
| answer mentions key facts | [["Monday"], ["Friday"], ["09:00", "9:00", "9 am", "9am", "9 a.m."], ["17:00", "5 pm", "5pm", "5 p.m."], ["UTC"]] | Email support is available Monday to Friday, 09:00 to 17:00 UTC. | ✅ |
| excerpts appear in the source files | all | all | ✅ |
| independent support check (2nd model pass) | supported | supported | ✅ |

- **seed/Q3** “When is email support available?” → `answered`
  - answer: Email support is available Monday to Friday, 09:00 to 17:00 UTC.
  - cited: SUPPORT-v1:p1; replaced text shown: (none)
  - model calls: draft_answer=live, verify_answer=live

## RC2-unsupported — PASS

| Check | Expected | Observed | |
|---|---|---|---|
| status | unresolved | unresolved | ✅ |
| suggested owner | Product reviewer | Product reviewer | ✅ |
| no yes/no capability claim | not starting with ['yes', 'no'] | (empty) | ✅ |
| reason explains why | ["JSON"] | The documents describe CSV exports (cited here as related context only, not as support) but never mention JSON export, so its availability is unknown. | ✅ |
| not approved | true | true | ✅ |

- **seed/Q2** “Is JSON export available?” → `unresolved`
  - answer: (none)
  - cited: EXPORT-v2:p1; replaced text shown: EXPORT-v1:p1
  - model calls: draft_answer=live
  - reason [unresolved_conflict]: The documents describe CSV exports (cited here as related context only, not as support) but never mention JSON export, so its availability is unknown.

## RC3-superseded-conflict — PASS

| Check | Expected | Observed | |
|---|---|---|---|
| status | answered | answered | ✅ |
| cites required passages | ["EXPORT-v2:p1"] | ["EXPORT-v2:p1"] | ✅ |
| does not cite as support | ["EXPORT-v1:p1"] | ["EXPORT-v2:p1"] | ✅ |
| answer polarity | starts with 'no' | No. CSV exports are available on paid plans only, and free-plan users cannot export CSV. | ✅ |
| answer mentions | ["paid"] | No. CSV exports are available on paid plans only, and free-plan users cannot export CSV. | ✅ |
| replaced text visible to reviewer | ["EXPORT-v1:p1"] | ["EXPORT-v1:p1"] | ✅ |

- **seed/Q1** “Can free-plan users export CSV?” → `answered`
  - answer: No. CSV exports are available on paid plans only, and free-plan users cannot export CSV.
  - cited: EXPORT-v2:p1; replaced text shown: EXPORT-v1:p1
  - model calls: draft_answer=live, verify_answer=live

## RC4-approved-reuse — PASS

| Check | Expected | Observed | |
|---|---|---|---|
| approve corrected Q1 | accepted | accepted | ✅ |
| approve Q3 as drafted | accepted | accepted | ✅ |
| save unapproved Q4 edit | accepted | accepted | ✅ |
| QB1 status | approved | approved | ✅ |
| QB1 reused | true | true | ✅ |
| QB1 shows approved wording | No. CSV export is available on paid plans only, so free-plan users cannot export CSV. | No. CSV export is available on paid plans only, so free-plan users cannot export CSV. | ✅ |
| approval record fields | ["approved_by", "approved_at", "sources"] | {"approved_by": "Product reviewer", "approved_at": "2026-10-05T13:51:07+00:00"} | ✅ |
| approval sources with document version | [{"passage_id": "EXPORT-v2:p1", "doc_id": "EXPORT-v2", "version": 2}] | [{"passage_id": "EXPORT-v2:p1", "doc_id": "EXPORT-v2", "version": 2}] | ✅ |
| no model call for reused answer | 0 | 0 | ✅ |
| QB3 not approved | status != approved | answered | ✅ |
| QB3 not reused | false | false | ✅ |
| QB3 does not show the unapproved edit | != 'No - live chat is not offered (UNAPPROVED reviewer edit).' | No, live chat is not offered; support is provided by email Monday to Friday, 09:00 to 17:00 UTC. | ✅ |

- **buyer-b/QB1** “Can free-plan users export CSV?” → `approved` (reused approval)
  - answer: No. CSV export is available on paid plans only, so free-plan users cannot export CSV.
  - cited: EXPORT-v2:p1; replaced text shown: (none)
  - model calls: none (reused)

- **buyer-b/QB3** “Is live chat offered?” → `answered`
  - answer: No, live chat is not offered; support is provided by email Monday to Friday, 09:00 to 17:00 UTC.
  - cited: SUPPORT-v1:p1; replaced text shown: (none)
  - model calls: draft_answer=replay, verify_answer=replay

## RC5-reload-and-source-change — PASS

| Check | Expected | Observed | |
|---|---|---|---|
| state readable by a new process | exit 0 | 0 | ✅ |
| after restart: approval active for 'Can free-plan users export CSV?' | active | active | ✅ |
| after restart: approval active for 'When is email support available?' | active | active | ✅ |
| after restart: seed/Q1 status | approved | approved | ✅ |
| after restart: seed/Q3 status | approved | approved | ✅ |
| after restart: buyer-b/QB1 status | approved | approved | ✅ |
| after restart: buyer-b/QB2 status | approved | approved | ✅ |
| after restart: sources kept for 'Can free-plan users export CSV?' | ["EXPORT-v2:p1"] | ["EXPORT-v2:p1"] | ✅ |
| after restart: sources kept for 'When is email support available?' | ["SUPPORT-v1:p1"] | ["SUPPORT-v1:p1"] | ✅ |
| after change: approval 'When is email support available?' | needs_review | needs_review | ✅ |
| after change: approval 'Can free-plan users export CSV?' | active | active | ✅ |
| change reason names the source | ["SUPPORT-v1"] | SUPPORT-v1 version changed from 1 to 2 | ✅ |
| after change: seed/Q3 status | unresolved | unresolved | ✅ |
| after change: buyer-b/QB2 status | unresolved | unresolved | ✅ |
| after change: seed/Q1 status | approved | approved | ✅ |
| asked again: status | unresolved | unresolved | ✅ |
| asked again: not reused | false | false | ✅ |
| asked again: owner | Support reviewer | Support reviewer | ✅ |
| asked again: reason | ["review", "changed"] | Approved answer #2 for this question requires review: SUPPORT-v1 version changed from 1 to 2 | ✅ |

- **adhoc/ASK-1** “When is email support available?” → `unresolved`
  - answer: Email support is available Monday to Friday, 08:00 to 18:00 UTC.
  - cited: SUPPORT-v1:p1; replaced text shown: (none)
  - model calls: draft_answer=live, verify_answer=live
  - reason [approval_needs_review]: Approved answer #2 for this question requires review: SUPPORT-v1 version changed from 1 to 2

## AC1-unresolved-conflict-no-supersedes — PASS

| Check | Expected | Observed | |
|---|---|---|---|
| status | unresolved | unresolved | ✅ |
| suggested owner | Product reviewer | Product reviewer | ✅ |
| conflicting passages listed | ["EXPORT-SCHEDULE-v1:p1", "EXPORT-FAQ-v1:p1"] | ["EXPORT-FAQ-v1:p1", "EXPORT-SCHEDULE-v1:p1"] | ✅ |

- **buyer-b/QB4** “How often do scheduled exports run?” → `unresolved`
  - answer: (none)
  - cited: EXPORT-FAQ-v1:p1, EXPORT-SCHEDULE-v1:p1; replaced text shown: (none)
  - model calls: draft_answer=live
  - reason [unresolved_conflict]: The frequency of scheduled exports is conflicting between two current documents (daily in EXPORT-FAQ-v1 vs. weekly in EXPORT-SCHEDULE-v1).

## AC2-undocumented-not-negative — PASS

| Check | Expected | Observed | |
|---|---|---|---|
| status | unresolved | unresolved | ✅ |
| suggested owner | Product reviewer | Product reviewer | ✅ |
| no yes/no capability claim | not starting with ['yes', 'no'] | (empty) | ✅ |

- **buyer-b/QB5** “Can users sign in with single sign-on (SSO)?” → `unresolved`
  - answer: (none)
  - cited: ACCESS-v1:p1; replaced text shown: (none)
  - model calls: draft_answer=live
  - reason [missing_evidence]: The documents do not say whether single sign-on (SSO) is supported; they mention only email and password sign-in, which does not rule out other sign-in methods.

## AC3-reworded-question-not-reused — PASS

| Check | Expected | Observed | |
|---|---|---|---|
| status | answered | answered | ✅ |
| reused approved answer | false | false | ✅ |
| cites required passages | ["EXPORT-v2:p1"] | ["EXPORT-v2:p1"] | ✅ |
| answer polarity | starts with 'no' | No. CSV exports are available on paid plans only, and free-plan users cannot export CSV. | ✅ |

- **buyer-b/QB6** “Can users on the free plan export CSV files?” → `answered`
  - answer: No. CSV exports are available on paid plans only, and free-plan users cannot export CSV.
  - cited: EXPORT-v2:p1; replaced text shown: EXPORT-v1:p1
  - model calls: draft_answer=live, verify_answer=live

## AC4-simulated-invalid-output — PASS

| Check | Expected | Observed | |
|---|---|---|---|
| status | failed | failed | ✅ |
| error type | invalid_output | invalid_output | ✅ |
| labelled as simulated | simulated | simulated | ✅ |
| batch continued | 5 other questions processed | 5 processed | ✅ |

- **seed/Q5** “How do users sign in?” → `failed`
  - answer: (none)
  - cited: (none); replaced text shown: (none)
  - model calls: draft_answer=simulated (invalid_output)
  - reason [model_failure]: Model draft failed (invalid_output): SIMULATED: Output failed schema validation: [{'type': 'json_invalid', 'loc': (), 'msg': 'Invalid JSON: EOF while parsing a string at line 1 column 58', 'input': '{"status": "answered", "answer": "Subscriptions are billed', 'ctx': {'error': 'EOF while parsing a string at line 1 column 58'}, 'url': 'https://errors.pydantic.dev/2.13/v/json_invalid'}]

## AC5-simulated-api-failure — PASS

| Check | Expected | Observed | |
|---|---|---|---|
| status | failed | failed | ✅ |
| error type | api_error | api_error | ✅ |
| labelled as simulated | simulated | simulated | ✅ |
| batch continued | 5 other questions processed | 5 processed | ✅ |

- **seed/Q6** “Who can invite team members?” → `failed`
  - answer: (none)
  - cited: (none); replaced text shown: (none)
  - model calls: draft_answer=simulated (api_error)
  - reason [model_failure]: Model draft failed (api_error): SIMULATED: model API unavailable (connection refused).

## AC6-simulated-fabricated-citation — PASS

| Check | Expected | Observed | |
|---|---|---|---|
| status | unresolved | unresolved | ✅ |
| failed checks | ["citations_exist"] | citations_exist: Cited passages not in the corpus: EXPORT-v9:p1 | ✅ |

- **seed/Q7** “How often are subscriptions billed?” → `unresolved`
  - answer: Subscriptions are billed weekly.
  - cited: EXPORT-v9:p1; replaced text shown: (none)
  - model calls: draft_answer=simulated
  - reason [evidence_check_failed]: citations_exist: Cited passages not in the corpus: EXPORT-v9:p1
