# Written walkthrough (about 5 minutes)

## 1. The problem
A sales team answers buyer questionnaires from company documents. There are three ways to get it
wrong:
- **saying too much:** inventing a capability, or reading "no" into silence;
- **trusting the wrong document:** following an outdated policy, or picking the newer of two
  documents that disagree;
- **reusing stale knowledge:** reusing an unapproved edit, or an approved answer whose source
  has since changed.

## 2. Approach: the model proposes, code and people decide
- **Two model passes.** Pass 1 drafts a short answer with verbatim excerpts, or declares the
  question unresolved and names what is missing. Pass 2 independently splits the draft into claims,
  judges each claim only against the cited passages, and lists any passage that disagrees.
- **Code-enforced rules.**
  - Citations must exist, and excerpts must appear in the passage.
  - Authority comes only from `supersedes` (never from dates), and superseded passages cannot be
    support.
  - A disagreement between two *current* documents always goes to a person.
  - Owners come from the topic mapping.
- **People approve.** Only an approval record is reusable. It stores the exact wording, the
  approver, the time, and each source's document version and text hash. Edits are drafts.
  Publishing a new document version re-checks every approval that cites it.
- **Evidence for every claim.** Every model call is saved with its exact request and response. The
  UI and the report label each call `live`, `replay`, `simulated` or `test-fake`.

## 3. Demo script
1. `python -m qra init --reset && python -m qra demo --mode replay && python -m qra serve`, then
   open http://127.0.0.1:5050.
2. **Queue.** Counts for answered, unresolved, approved (with the number reused) and failed; a
   status filter; and the reasons for review, grouped by type and by owner.
3. **Q3 (supported).** The answer sits beside SUPPORT-v1:p1 with the excerpt highlighted; all
   checks pass and the second pass says "supported".
4. **Q2 (JSON, undocumented).** Unresolved, routed to the Product reviewer, with no answer text.
5. **Q1 (CSV).** The answer is "No" from EXPORT-v2. The old "every plan" text from EXPORT-v1 is
   shown as *replaced by EXPORT-v2*. The reviewer's corrected wording is approved, and the page
   shows the model's original suggestion for comparison.
6. **Questionnaire B.**
   - QB1 repeats Q1: the approved wording is reused with approval #1 and its sources, with no model
     call.
   - QB3 repeats Q4: Q4 has only an *unapproved* edit, so QB3 gets a fresh draft.
   - QB4: two current documents disagree on the export schedule. The newer FAQ does not win, so
     the question is unresolved.
   - QB5 (SSO) is unknown, not "no".
   - QB6 is reworded, so it is not reused.
7. **Restart** the server: the approvals and the evidence are still there.
8. **Documents → Publish** the SUPPORT-v1 version-2 scenario. Approval #2 (Q3) becomes
   *needs review* and names the version change; Q3 and QB2 return to the queue; the Q1 approval
   stays active. Asking Q3 again produces a fresh draft with the new hours and does not reuse the
   stale answer.

## 4. Findings
- **From building and testing (done):**
  - Structural checks in code catch fabricated or superseded citations.
  - A rule that "date alone does not win" must be enforced in code and not only in the prompt;
    the conflict classifier and verifier contradictions handle it.
  - Several bugs were caught by deliberate checking rather than by tests that already existed:
    the verifier classification, the disappearing replaced text, and three invalidation and
    display bugs found by an independent review subagent. Each was reproduced by a failing test
    before it was fixed (see `docs/LLM_USAGE.md`).
- **From the real model (21 responses, `claude-opus-5-5`):** 5/5 reference cases and 6/6
  additional checks passed on the first recorded run.
  - **SSO.** The model did *not* fall into the trap of reading "email and password" as ruling SSO
    out. It left the question unresolved and said why.
  - **Scheduled exports.** It reported the two current documents as conflicting instead of
    preferring the newer FAQ.
  - **CSV.** It answered from EXPORT-v2 and reported EXPORT-v1 as the replaced conflict without
    being told which passage mattered.
  - **Extra detail.** For "Is live chat offered?" it added the email-support hours, which weren't
    asked for. The second pass judged this supported. But it shows why approvals are invalidated
    on *any* version change of a cited document: after the hours change, that answer would be
    partly stale even though the live-chat sentence is unchanged.
  - **Caution.** One passing run on 14 questions is not an accuracy estimate. The simulated
    failures show what happens when the model does go wrong (visible `failed` or `unresolved`,
    never a fabricated success), but they are not evidence of how often it will.

## 5. Limitations
- Five cases are a demonstration, not an accuracy estimate.
- The verifier shares the drafter's model family.
- Reuse is exact-match only.
- There is no authentication.
- See README → *Known limitations*.
