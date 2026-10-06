# How the expected results were verified

The expected values in `reference-cases.json` were written by reading the source passages and the
rules in `data/starter/domain.md`. They were written before the application code existed, so
they could not have been copied from application output. The evaluator in `qra/evaluate.py` reads
this file and compares it with the observed results.

Corpus used: `data/starter/seed.json` (5 documents) + `data/additions/documents.json` (2 documents).

| Passage | Text | Authority (from explicit `supersedes`) |
|---|---|---|
| EXPORT-v1:p1 | CSV exports are available on every plan. | superseded by EXPORT-v2 |
| EXPORT-v2:p1 | CSV exports are available on paid plans only. Free-plan users cannot export CSV. | current, `supersedes: EXPORT-v1` |
| SUPPORT-v1:p1 | Email support is available Monday to Friday, 09:00 to 17:00 UTC. Live chat is not offered. | current |
| ACCESS-v1:p1 | Users can sign in with email and password. Account owners can invite team members. | current |
| BILLING-v1:p1 | Paid subscriptions are billed monthly in USD. Account owners can download billing invoices. | current |
| EXPORT-SCHEDULE-v1:p1 *(addition)* | Scheduled exports run once a week. | current (date 2026-07-01) |
| EXPORT-FAQ-v1:p1 *(addition)* | Scheduled exports run every day. | current (date 2026-09-01), **no `supersedes`** |

Owners (seed): exports → Product reviewer, support → Support reviewer, access → Product reviewer,
billing → Billing reviewer.

## Reference cases

**RC1 – supported (Q3, "When is email support available?").** SUPPORT-v1:p1 is the only passage
that mentions email support, and it states "Monday to Friday, 09:00 to 17:00 UTC". This matches the
seed's expected answer "Monday to Friday, 09:00–17:00 UTC" with source SUPPORT-v1:p1. Expected:
status `answered`, the answer cites SUPPORT-v1:p1 (and nothing else), every excerpt appears
verbatim in the passage, and the answer mentions the days, both times and UTC. Spelling variants
such as "9:00" and "5 pm" are accepted.

**RC2 – unsupported (Q2, "Is JSON export available?").** I searched all seven passages and none
contains "JSON". Under rule 1 the feature is unknown. The seed expects status `unresolved`, owner
`Product reviewer` (topic `exports`) and reason "JSON export is undocumented". The draft must not
assert either "yes" or "no".

**RC3 – superseded conflict (Q1, "Can free-plan users export CSV?").** EXPORT-v2 has
`supersedes: "EXPORT-v1"`, which is the only thing that gives it authority under rule 2. Its
passage says free-plan users cannot export CSV, so the answer is "No" (paid plans only). It must
cite EXPORT-v2:p1, must not cite EXPORT-v1:p1 as support, and must show EXPORT-v1:p1 to the
reviewer as replaced text. This matches the seed's `show_conflict_with`.

**RC4 – approval and reuse.** These are reviewer actions, so they are scripted rather than
inferred. A reviewer approves a corrected wording of Q1 citing EXPORT-v2:p1 and approves Q3 as
drafted. They also save an edit to Q4 *without* approving it. Questionnaire B then repeats:
- Q1's text exactly as QB1. Expected: the identical approved wording, status `approved`, a link to
  the approval record (approver, time, source passage with document version 2) and no model call.
- Q4's text exactly as QB3. Expected: no reuse, because only approved answers are reusable
  (rule 3), so QB3 must not be `approved` and must not carry the unapproved edit text.

**RC5 – reload and source change.** After a restart, read from a new process: both approvals are
still `active` with their sources, and the approved items are still `approved`. Then apply
`source-change-support-v1-version-2.json`, which bumps SUPPORT-v1 from version 1 to 2 and changes
the hours to 08:00–18:00. The Q3 approval cites SUPPORT-v1 at version 1, so it becomes
`needs_review`, and items that rely on it (Q3, and QB2 which reused it) return to `unresolved`. The
Q1 approval cites only EXPORT-v2 and stays `active`. Asking "When is email support available?"
again must not reuse the stale approval: expected status `unresolved`, owner `Support reviewer`,
and a reason that mentions review or the change.

## Additional checks (beyond the five)

- **AC1 (QB4).** EXPORT-SCHEDULE-v1:p1 says "once a week" and EXPORT-FAQ-v1:p1 says "every day".
  Neither document supersedes the other, and FAQ is newer only by date, which rule 2 says does not
  establish authority. Expected: `unresolved`, Product reviewer, both passages listed as conflicting.
- **AC2 (QB5, SSO).** ACCESS-v1:p1 describes email/password sign-in but does not say "only". Under
  rule 1, SSO is unknown, so the expected status is `unresolved` with no yes/no claim. This guards
  against a model inferring "no" from silence.
- **AC3 (QB6).** The reworded Q1 is not an exact match, so it is not reused. EXPORT-v2:p1 still
  answers it ("No").
- **AC4–AC6 (simulated, clearly labelled).** These inject an invalid model output, an API error and
  a fabricated citation (`EXPORT-v9:p1`) to check that failures are visible, do not stop the batch,
  and are never presented as success.

## Ambiguities noticed while writing the key
See the "Ambiguities and assumptions" section of the top-level README.
