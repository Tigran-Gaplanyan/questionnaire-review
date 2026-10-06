# Dataset: supplied seed and additions

All data is fictional. `data/starter/` is a verbatim copy of `tasks/evidence/` from starter pack
version `2026-10-02` and is never edited.

## Generation method
The additions are **handwritten**, with no random generation and therefore no seed. I used the
starter pack's `generate-assignment-data` skill as a checklist (copied to
`.claude/skills/generate-assignment-data/SKILL.md`):
- keep the seed IDs and rules fixed;
- add only what the brief's checks need;
- keep expected results in a separate file that links each one to its input and rule;
- flag deliberate invalid cases separately.

The brief says to add questions or passages "only if needed for the checks", so each addition
exists to cover one scenario the seed does not:

| File | Addition | Why it exists | Rule exercised |
|---|---|---|---|
| `additions/documents.json` | EXPORT-SCHEDULE-v1 (2026-07-01): "Scheduled exports run once a week." | One half of a conflict that metadata does not resolve | Rule 2 |
| `additions/documents.json` | EXPORT-FAQ-v1 (2026-09-01): "Scheduled exports run every day." | Newer date, but no `supersedes` field, so it must **not** win | Rule 2 |
| `additions/questionnaire-b.json` | QB1 = exact text of Q1 | Approved-answer reuse | Rule 3 |
| `additions/questionnaire-b.json` | QB2 = exact text of Q3 | Reuse, later invalidated by a source change | Rule 3 |
| `additions/questionnaire-b.json` | QB3 = exact text of Q4 | An unapproved edit must not be reused | Rule 3 |
| `additions/questionnaire-b.json` | QB4 "How often do scheduled exports run?" | Unresolved conflict that must go to review | Rule 2 |
| `additions/questionnaire-b.json` | QB5 "Can users sign in with single sign-on (SSO)?" | Undocumented feature next to a related documented one; tests that silence is not read as "no" | Rule 1 |
| `additions/questionnaire-b.json` | QB6 reworded Q1 | Shows the limit of exact-match reuse | Brief: exact match is sufficient |
| `additions/source-change-support-v1-version-2.json` | SUPPORT-v1 version 2 (hours 08:00–18:00) | Changed-source scenario; applied only during that demonstration | Rule 3 |

The new documents use existing topics (`exports`), so the supplied owner mapping covers them and no
new reviewer role was invented. They do not mention CSV, JSON, support, access or billing, so the
seed's expected results for Q1–Q3 are unchanged and were not recalculated.

## Questionnaire files
- `starter/seed.json` → questionnaire id `seed` (Q1–Q8). Questions are read directly from the seed file.
- `additions/questionnaire-b.json` → questionnaire id `buyer-b` (QB1–QB6).

The application receives only `id`, `topic` and `text`. The reason each question exists is recorded
here and in `reference/`, never in the input files, so the model cannot see the answer key.
