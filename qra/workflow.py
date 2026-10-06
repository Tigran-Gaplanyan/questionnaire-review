"""The workflow: the "manager" that runs everything.

For each question:
  1. If an approved answer exists for exactly this question, and its source documents have not
     changed, reuse it (no model call).
  2. Otherwise ask the model for a draft (pass 1), check it in code, then ask the model to check
     each claim against the quoted passages (pass 2).
  3. Give the question a status:
       answered   - the draft passed every check; waiting for a reviewer to approve it
       unresolved - a reviewer must look: missing evidence, a conflict, a failed check, a changed
                    source, or a reviewer's note. `owner` says who, from the topic mapping.
       approved   - approved by a reviewer here, or reused from an earlier approval (`reused` = 1)
       failed     - the model call failed or gave bad output; nothing was made up

The file has five sections:
  1. Loading data          2. Answering questions          3. Reviewer actions
  4. Approvals and document changes                        5. Counts for the dashboard
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from . import config
from .corpus import Corpus, Finding, documents_from_raw, load_questionnaire, question_key, read_json, validate_documents
from .drafting import DRAFT_PROMPT, DRAFT_SCHEMA, VERIFY_PROMPT, VERIFY_SCHEMA, DraftOutput, VerifyOutput, draft_messages, verify_messages
from .evidence import REASON_FOR_CHECK, Check, check_draft, support_checks
from .store import Store, now

ADHOC = "adhoc"  # questionnaire id for single questions asked with ask()


class WorkflowError(ValueError):
    """A reviewer action was rejected. The message says why."""


def reason(code: str, text: str) -> dict:
    """Why a question needs review. `code` is used for the counts, `text` is shown to people."""
    return {"code": code, "text": text}


class Workspace:
    """Runs the whole process on top of the database (store) and the model client."""
    def __init__(self, store: Store, model):
        """Use this database and this model client (real, replaying, simulated, or a test fake)."""
        self.store = store
        self.model = model  # anything with complete_json(), see llm.py

    def corpus(self) -> Corpus:
        """The current documents, authority and topic mapping."""
        return self.store.corpus()

    # =========================================================================================
    # 1. Loading data
    # =========================================================================================

    def import_corpus(self, seed_path: Path = config.STARTER_SEED, additions=(config.ADDITION_DOCUMENTS,)) -> list[Finding]:
        """Load seed + added documents into the database (only once, and only if they are valid)."""
        seed = read_json(seed_path)
        docs = documents_from_raw(seed.get("documents", []), _relative(seed_path))
        for path in additions:
            docs += documents_from_raw(read_json(path).get("documents", []), _relative(path))
        owners = seed.get("owners", {})

        findings = validate_documents(docs, owners)
        self.store.save_findings("corpus", findings)
        has_errors = any(f.level == "error" for f in findings)
        if has_errors or self.store.has_corpus():
            return findings
        self.store.save_owners(owners)
        for doc in docs:
            self.store.add_revision(doc, source=doc.source, note="initial import")
        return findings

    def import_questionnaire(self, path: Path) -> tuple[str, list[Finding]]:
        """Create one item per question (questions already imported are left as they are)."""
        owners = self.store.owners()
        questionnaire = load_questionnaire(path, owners)
        self.store.save_findings(f"questionnaire:{questionnaire.id}", questionnaire.findings)
        if not questionnaire.questions:
            return questionnaire.id, questionnaire.findings
        self._add_questionnaire(questionnaire.id, questionnaire.name, _relative(Path(path)))
        for q in questionnaire.questions:
            self._add_item(questionnaire.id, q.id, q.topic, q.text, owners.get(q.topic))
        return questionnaire.id, questionnaire.findings

    def _add_questionnaire(self, questionnaire_id: str, name: str, source_path: str) -> None:
        """Register a questionnaire once (later imports of the same id are ignored)."""
        if self.store.get("questionnaires", questionnaire_id) is None:
            self.store.insert("questionnaires", id=questionnaire_id, name=name, source_path=source_path)

    def _add_item(self, questionnaire_id: str, question_id: str, topic: str, text: str, owner: str | None) -> int:
        """Create the item for one question if it does not exist yet. Returns its id."""
        existing = self.store.item_by_ref(questionnaire_id, question_id)
        if existing:
            return existing["id"]
        return self.store.insert("items", questionnaire_id=questionnaire_id, question_id=question_id, topic=topic,
                                 question_text=text, status="pending", owner=owner, reasons=[], created_at=now())

    # =========================================================================================
    # 2. Answering questions
    # =========================================================================================

    def process_questionnaire(self, questionnaire_id: str, reprocess: bool = False) -> list[dict]:
        """Answer every new (or previously failed) question in a questionnaire."""
        results = []
        for item in self.store.items(questionnaire_id=questionnaire_id):
            if reprocess or item["status"] in ("pending", "failed"):
                results.append(self.process_item(item["id"]))
        return results

    def ask(self, text: str, topic: str) -> dict:
        """Ask one question outside a questionnaire (for example, to ask a question again)."""
        if not text.strip():
            raise WorkflowError("Question text is empty.")
        self._add_questionnaire(ADHOC, "Ad-hoc questions", "")
        number = len(self.store.items(questionnaire_id=ADHOC)) + 1
        item_id = self._add_item(ADHOC, f"ASK-{number}", topic, text.strip(), self.corpus().owner_for(topic))
        return self.process_item(item_id)

    def process_item(self, item_id: int, allow_reuse: bool = True) -> dict:
        """Answer one question. An unexpected error marks it failed, so the rest of the batch continues."""
        try:
            self._process(item_id, allow_reuse)
        except Exception as error:
            self.store.update("items", item_id, status="failed",
                              reasons=[reason("internal_error", f"{error.__class__.__name__}: {error}")])
            self.store.log("processing_failed", item_id=item_id, error=str(error))
        return self.store.get("items", item_id)

    def _process(self, item_id: int, allow_reuse: bool) -> None:
        """Answer one question: reuse a valid approval, or draft, check and verify with the model."""
        item = self.store.get("items", item_id)
        corpus = self.corpus()
        owner = corpus.owner_for(item["topic"])
        reasons = [] if owner else [reason("no_owner", f"No reviewer is mapped for topic '{item['topic']}'; assign one manually.")]

        # Step 1: reuse an approved answer if there is a valid one.
        reusable, stale = self.find_reusable(item["question_text"], corpus) if allow_reuse else (None, None)
        if reusable:
            self._reuse(item, reusable, corpus, owner, reasons)
            return

        # Step 2: ask the model, check the answer, and decide the status.
        status, draft_reasons, draft_id = self._draft(item, corpus)
        reasons += draft_reasons

        # An approved answer exists, but its sources changed: a reviewer must look again.
        if stale:
            reasons.insert(0, _stale_reason(stale))
            if status != "failed":
                status = "unresolved"

        self.store.update("items", item_id, status=status, owner=owner, current_draft_id=draft_id, reused=0,
                          approval_id=stale["id"] if stale else item["approval_id"], reasons=reasons)
        self.store.log("drafted", item_id=item_id, status=status)

    def _reuse(self, item: dict, approval: dict, corpus: Corpus, owner: str | None, reasons: list[dict]) -> None:
        """Answer a question by reusing an approved answer (no model call), and record where it came from."""
        draft_id = self._save_draft(
            item, kind="reused_approval", proposed_status="approved", answer=approval["answer"],
            citations=[{"passage_id": s["passage_id"], "excerpt": s["text"]} for s in approval["sources"]],
            checks=[Check("approved_sources_current", True, "All sources of the approved answer still match the current document versions.").as_dict()],
            corpus_versions=_versions(corpus), author=f"approval #{approval['id']}",
            note=f"Reused approved answer #{approval['id']} (approved by {approval['approved_by']} at {approval['approved_at']}). No model call.",
        )
        self.store.update("items", item["id"], status="approved", reused=1, approval_id=approval["id"],
                          current_draft_id=draft_id, owner=owner, reasons=reasons)
        self.store.log("reused_approval", item_id=item["id"], approval_id=approval["id"])

    def _draft(self, item: dict, corpus: Corpus) -> tuple[str, list[dict], int]:
        """Pass 1 (draft), code checks, pass 2 (verify). Returns (status, reasons, draft id)."""
        if self.model is None:
            raise WorkflowError("No model client configured.")
        context = {"questionnaire_id": item["questionnaire_id"], "question_id": item["question_id"], "item_id": item["id"]}

        # Pass 1: the model drafts an answer with quotes.
        system, user = draft_messages(corpus, item["question_text"])
        result = self.model.complete_json(purpose="draft_answer", prompt_name=DRAFT_PROMPT, system=system, user=user,
                                          schema=DRAFT_SCHEMA, output_model=DraftOutput, effort=config.DRAFT_EFFORT, context=context)
        call_ids = [self._save_call(item, result)]
        if not result.ok:
            draft_id = self._save_draft(item, kind="model", proposed_status="failed", model_call_ids=call_ids,
                                        corpus_versions=_versions(corpus), note=f"{result.error_type}: {result.error_message}")
            return "failed", [reason("model_failure", f"Model draft failed ({result.error_type}): {result.error_message}")], draft_id

        draft = result.data
        cited = [c["passage_id"] for c in draft["citations"]]

        # Code checks.
        checks, conflicts, replaced = check_draft(draft, corpus)

        # Pass 2: only for an answer that passed the code checks.
        support = None
        if draft["status"] == "answered" and all(c.passed for c in checks if c.blocking):
            system, user = verify_messages(corpus, item["question_text"], draft["answer"], cited)
            verdict = self.model.complete_json(purpose="verify_answer", prompt_name=VERIFY_PROMPT, system=system, user=user,
                                               schema=VERIFY_SCHEMA, output_model=VerifyOutput, effort=config.VERIFY_EFFORT, context=context)
            call_ids.append(self._save_call(item, verdict))
            if verdict.ok:
                support = verdict.data
                more_checks, more_conflicts, more_replaced = support_checks(support, corpus, cited)
                checks += more_checks
                conflicts += more_conflicts
                already_shown = {r["passage_id"] for r in replaced}
                replaced += [r for r in more_replaced if r["passage_id"] not in already_shown]
            else:
                checks.append(Check("support_check_ran", False, f"Support check could not run ({verdict.error_type}): {verdict.error_message}"))

        status, reasons = _decide_status(draft, checks, conflicts)

        # An unresolved draft never carries answer text; anything the model wrote anyway is kept as a note.
        answer, note = draft["answer"], None
        if draft["status"] != "answered":
            answer = ""
            if draft["answer"].strip():
                note = f"Model text withheld because the draft is unresolved: {draft['answer']}"

        draft_id = self._save_draft(
            item, kind="model", proposed_status=draft["status"], answer=answer, note=note, citations=draft["citations"],
            conflicts=conflicts, replaced_text=replaced, unresolved_reason=draft["unresolved_reason"],
            checks=[c.as_dict() for c in checks], support=support, corpus_versions=_versions(corpus), model_call_ids=call_ids,
        )
        return status, reasons, draft_id

    def redraft(self, item_id: int, actor: str = "reviewer") -> dict:
        """Ask the model for a fresh draft, even if an approved answer exists."""
        self.store.log("redraft_requested", item_id=item_id, actor=actor)
        stale = self.store.get("approvals", self.store.get("items", item_id)["approval_id"])
        item = self.process_item(item_id, allow_reuse=False)
        # Keep the warning if this question's approved answer still needs review.
        if stale and stale["status"] == "needs_review" and item["status"] != "failed":
            self.store.update("items", item_id, status="unresolved", approval_id=stale["id"],
                              reasons=[_stale_reason(stale)] + item["reasons"])
        return self.store.get("items", item_id)

    def _save_draft(self, item: dict, **values) -> int:
        """Save one draft (model suggestion or reviewer edit) for a question. Returns its id."""
        return self.store.insert("drafts", item_id=item["id"], created_at=now(), **values)

    def _save_call(self, item: dict, result) -> int:
        """Save one model call (successful or not) for a question. Returns its id."""
        return self.store.insert("model_calls", item_id=item["id"], created_at=now(), **result.as_record())

    # =========================================================================================
    # 3. Reviewer actions
    # =========================================================================================

    def save_edit(self, item_id: int, author: str, answer: str, citation_ids: list[str] | None = None, note: str = "") -> dict:
        """Save a reviewer's edit as a draft. It is NOT approved and is never reused."""
        if not author.strip():
            raise WorkflowError("Reviewer name is required.")
        item = self.store.get("items", item_id)
        corpus = self.corpus()
        if citation_ids is None:
            current = self.store.get("drafts", item["current_draft_id"]) or {}
            citation_ids = [c["passage_id"] for c in current.get("citations") or []]
        problems = _citation_problems(citation_ids, corpus) if citation_ids else []

        draft_id = self._save_draft(
            item, kind="reviewer_edit", proposed_status="draft", answer=answer.strip(),
            citations=_citations(citation_ids, corpus), corpus_versions=_versions(corpus),
            checks=[Check("citations_valid", not problems, "; ".join(problems) or "Selected passages exist and are current.", blocking=False).as_dict()],
            author=author.strip(), note=note or "Unapproved reviewer edit",
        )
        changes = {"current_draft_id": draft_id, "reused": 0}
        if item["status"] == "approved":
            changes["status"] = "answered"
            changes["reasons"] = [reason("edited_after_approval",
                                         f"Edited after approval #{item['approval_id']}; the approved answer is unchanged until this edit is approved.")]
        self.store.update("items", item_id, **changes)
        self.store.log("edit_saved", item_id=item_id, actor=author, answer=answer.strip(), approved=False)
        return self.store.get("items", item_id)

    def approve(self, item_id: int, approved_by: str, answer: str | None = None, citation_ids: list[str] | None = None, note: str = "") -> dict:
        """Approve the current draft, optionally with corrected wording or different passages."""
        item = self.store.get("items", item_id)
        if item is None:
            raise WorkflowError(f"Item {item_id} does not exist.")
        corpus = self.corpus()
        draft = self.store.get("drafts", item["current_draft_id"]) or {}
        draft_answer = (draft.get("answer") or "").strip()
        draft_ids = [c["passage_id"] for c in draft.get("citations") or []]
        final_answer = draft_answer if answer is None else answer.strip()
        final_ids = draft_ids if citation_ids is None else citation_ids

        # Is this approval allowed?
        problems = []
        if not approved_by.strip():
            problems.append("Reviewer name is required.")
        if not final_answer:
            problems.append("Answer is empty.")
        problems += _citation_problems(final_ids, corpus)
        if not problems and final_answer == draft_answer:
            problems += _stale_draft_problems(draft, final_ids, corpus)
        if problems:
            raise WorkflowError(" ".join(problems))

        # If the reviewer changed anything, keep the corrected version as its own draft (for the history).
        draft_id = draft.get("id")
        if final_answer != draft_answer or final_ids != draft_ids or draft.get("kind") == "reused_approval":
            draft_id = self._save_draft(item, kind="reviewer_edit", proposed_status="approved", answer=final_answer,
                                        citations=_citations(final_ids, corpus), corpus_versions=_versions(corpus),
                                        author=approved_by.strip(), note=note or "Correction made at approval")

        # Save the approval together with the exact document versions it relies on.
        sources = []
        for passage_id in final_ids:
            passage = corpus.passages[passage_id]
            doc = corpus.documents[passage.doc_id]
            sources.append({"passage_id": passage_id, "doc_id": doc.id, "version": doc.version,
                            "revision": doc.revision, "text_sha": passage.text_sha, "text": passage.text})
        key = question_key(item["question_text"])
        older = [a for a in self.store.approvals() if a["question_key"] == key and a["status"] in ("active", "needs_review")]
        model_draft = self.store.latest_model_draft(item_id)
        model_answer = model_draft["answer"] if model_draft else None
        approval_id = self.store.insert(
            "approvals", question_key=key, question_text=item["question_text"], topic=item["topic"], answer=final_answer,
            sources=sources, approved_by=approved_by.strip(), approved_at=now(), from_item_id=item_id, from_draft_id=draft_id,
            model_answer=model_answer, status="active", note=note,
        )
        # Only the newest approval for a question is reused.
        for old in older:
            self.store.update("approvals", old["id"], status="superseded", status_reason=f"Replaced by approval #{approval_id}")

        self.store.update("items", item_id, status="approved", approval_id=approval_id, current_draft_id=draft_id, reused=0, reasons=[])
        self.store.log("approved", item_id=item_id, approval_id=approval_id, actor=approved_by.strip(),
                       answer=final_answer, model_answer=model_answer, sources=final_ids)
        return self.store.get("items", item_id)

    def mark_unresolved(self, item_id: int, author: str, note: str) -> dict:
        """A reviewer leaves the question unresolved, with a note saying why."""
        if not author.strip() or not note.strip():
            raise WorkflowError("Reviewer name and a note are required to leave a question unresolved.")
        item = self.store.get("items", item_id)
        approval = self.store.get("approvals", item["approval_id"])
        if item["status"] == "approved" and approval and approval["status"] == "active":
            # The reviewer no longer stands behind this answer, so it must not be reused either.
            self._invalidate(approval, f"{author.strip()} left '{item['question_text']}' unresolved: {note.strip()}")
            item = self.store.get("items", item_id)
        reasons = [r for r in item["reasons"] if r["code"] != "reviewer_note"]
        reasons.append(reason("reviewer_note", f"{author.strip()}: {note.strip()}"))
        self.store.update("items", item_id, status="unresolved", reviewer_note=note.strip(), reasons=reasons)
        self.store.log("left_unresolved", item_id=item_id, actor=author.strip(), note=note.strip())
        return self.store.get("items", item_id)

    # =========================================================================================
    # 4. Approvals and document changes
    # =========================================================================================

    def find_reusable(self, question_text: str, corpus: Corpus) -> tuple[dict | None, dict | None]:
        """Returns (approval we can reuse, approval that needs review). At most one is not None."""
        approval = self.store.latest_approval_for_key(question_key(question_text))
        if approval is None:
            return None, None
        if approval["status"] == "needs_review":
            return None, approval
        problems = source_problems(approval, corpus)
        if problems:  # a source changed since approval (normally already caught when it was published)
            self._invalidate(approval, "; ".join(problems))
            return None, self.store.get("approvals", approval["id"])
        return approval, None

    def publish_revision(self, raw_doc: dict, source: str, note: str = "", actor: str = "system") -> tuple[int, list[dict]]:
        """Save a new version of a document, then re-check every approval. Returns (revision, approvals now needing review)."""
        new = documents_from_raw([raw_doc], source)[0]
        current = {d.id: d for d in self.store.current_documents()}
        all_docs = [d for d in current.values() if d.id != new.id] + [new]
        findings = validate_documents(all_docs, self.store.owners())
        errors = [f.message for f in findings if f.level == "error"]
        if errors:
            raise WorkflowError("; ".join(errors))
        old = current.get(new.id)
        if old and (old.version, old.passages, old.supersedes) == (new.version, new.passages, new.supersedes):
            raise WorkflowError(f"{new.id} is unchanged; nothing to publish.")

        revision = self.store.add_revision(new, source=source, note=note)
        self.store.save_findings("corpus", findings)
        self.store.log("document_revision_published", actor=actor, doc_id=new.id, revision=revision,
                       old_version=old.version if old else None, new_version=new.version, note=note)
        return revision, self.revalidate_approvals()

    def apply_scenario(self, path: Path, actor: str = "scenario") -> list[dict]:
        """Publish every document revision listed in a scenario file."""
        scenario = read_json(path)
        affected = []
        for raw_doc in scenario.get("revisions", []):
            _, changed = self.publish_revision(raw_doc, source=_relative(path), note=scenario.get("description", ""), actor=actor)
            affected += changed
        return affected

    def revalidate_approvals(self) -> list[dict]:
        """After a document change: approvals whose sources changed go back to review."""
        corpus = self.corpus()
        affected = []
        for approval in self.store.approvals():
            if approval["status"] == "needs_review":
                continue
            problems = source_problems(approval, corpus)
            if not problems:
                continue
            if approval["status"] == "active":
                self._invalidate(approval, "; ".join(problems))
                affected.append(self.store.get("approvals", approval["id"]))
            else:
                # A replaced approval is no longer reused, but questions approved with it still need review.
                self._reopen_items(approval, "; ".join(problems))
        return affected

    def _invalidate(self, approval: dict, why: str) -> None:
        """Mark an approval as needing review and send the questions that use it back to the queue."""
        self.store.update("approvals", approval["id"], status="needs_review", status_reason=why)
        self.store.log("approval_needs_review", approval_id=approval["id"], reason=why)
        self._reopen_items(approval, why)

    def _reopen_items(self, approval: dict, why: str) -> None:
        """Questions that were approved with this approval go back to the review queue."""
        for item in self.store.items_with_approval(approval["id"]):
            if item["status"] == "approved":
                message = f"Approved answer #{approval['id']} requires review: {why}"
                self.store.update("items", item["id"], status="unresolved", reasons=[reason("approval_needs_review", message)])
                self.store.log("status_changed", item_id=item["id"], approval_id=approval["id"], to="unresolved", reason=why)

    # =========================================================================================
    # 5. Counts for the dashboard
    # =========================================================================================

    def summary(self) -> dict:
        """Counts for the dashboard: questions per status, reasons for review, and approvals per status."""
        items = self.store.items()
        by_status = Counter(i["status"] for i in items)
        open_items = [i for i in items if i["status"] in ("unresolved", "failed")]
        reason_codes = Counter(r["code"] for i in open_items for r in i["reasons"])
        routed = Counter(i["owner"] or "(no owner mapped)" for i in items if i["status"] == "unresolved")
        return {
            "total": len(items),
            "answered": by_status["answered"],
            "unresolved": by_status["unresolved"],
            "approved": by_status["approved"],
            "approved_reused": sum(1 for i in items if i["status"] == "approved" and i["reused"]),
            "failed": by_status["failed"],
            "pending": by_status["pending"],
            "reasons": dict(reason_codes.most_common()),
            "unresolved_by_owner": dict(routed.most_common()),
            "approvals": dict(Counter(a["status"] for a in self.store.approvals())),
        }


# --- Helpers ---------------------------------------------------------------------------------

def source_problems(approval: dict, corpus: Corpus) -> list[str]:
    """What changed in an approval's source documents since it was approved (empty list = nothing)."""
    problems = []
    for source in approval["sources"]:
        doc = corpus.documents.get(source["doc_id"])
        if doc is None:
            problems.append(f"{source['doc_id']} no longer exists")
            continue
        found = []
        if doc.version != source["version"]:
            found.append(f"{source['doc_id']} version changed from {source['version']} to {doc.version}")
        passage = corpus.passages.get(source["passage_id"])
        if passage is None:
            found.append(f"{source['passage_id']} no longer exists")
        elif doc.version == source["version"] and passage.text_sha != source["text_sha"]:
            found.append(f"{source['passage_id']} text changed without a version change")
        if corpus.is_superseded(source["doc_id"]):
            found.append(f"{source['doc_id']} is now {corpus.authority(source['doc_id'])}")
        if not found and doc.revision != source["revision"]:  # e.g. its supersedes link changed
            found.append(f"{source['doc_id']} was republished (revision {source['revision']} -> {doc.revision}) with changed metadata")
        problems += found
    return problems


def _decide_status(draft: dict, checks: list[Check], conflicts: list[dict]) -> tuple[str, list[dict]]:
    """answered if the model answered and every blocking check passed; otherwise unresolved."""
    if draft["status"] == "unresolved":
        open_conflict = any(not c["resolved_by_metadata"] for c in conflicts)
        code = "unresolved_conflict" if open_conflict else "missing_evidence"
        return "unresolved", [reason(code, draft["unresolved_reason"] or "The model could not answer from the documents.")]
    failed = [c for c in checks if c.blocking and not c.passed]
    if failed:
        return "unresolved", [reason(REASON_FOR_CHECK.get(c.name, "evidence_check_failed"), f"{c.name}: {c.detail}") for c in failed]
    return "answered", []


def _stale_reason(approval: dict) -> dict:
    """The reason shown when a question's approved answer needs review."""
    return reason("approval_needs_review", f"Approved answer #{approval['id']} for this question requires review: {approval['status_reason']}")


def _citation_problems(passage_ids: list[str], corpus: Corpus) -> list[str]:
    """An approved answer must rest on at least one existing, current passage."""
    if not passage_ids:
        return ["Select at least one supporting passage."]
    problems = []
    for passage_id in passage_ids:
        if passage_id not in corpus.passages:
            problems.append(f"{passage_id} does not exist.")
        elif corpus.is_superseded(corpus.passages[passage_id].doc_id):
            problems.append(f"{passage_id} is in a superseded document ({corpus.authority(corpus.passages[passage_id].doc_id)}).")
    return problems


def _stale_draft_problems(draft: dict, passage_ids: list[str], corpus: Corpus) -> list[str]:
    """Unchanged wording may only be approved if it was written against the current documents."""
    written_against = draft.get("corpus_versions") or {}
    problems = []
    for doc_id in sorted({corpus.passages[p].doc_id for p in passage_ids if p in corpus.passages}):
        then, doc = written_against.get(doc_id), corpus.documents[doc_id]
        if then and (then["version"], then["revision"]) != (doc.version, doc.revision):
            problems.append(f"This wording was drafted against {doc_id} version {then['version']} (revision {then['revision']}); "
                            f"it is now version {doc.version} (revision {doc.revision}). Request a fresh draft or edit the answer before approving.")
    return problems


def _citations(passage_ids: list[str], corpus: Corpus) -> list[dict]:
    """Quotes for the selected passages (the whole passage text is used as the quote)."""
    return [{"passage_id": p, "excerpt": corpus.passages[p].text} for p in passage_ids if p in corpus.passages]


def _versions(corpus: Corpus) -> dict:
    """Which version of each document a draft was written against."""
    return {d.id: {"version": d.version, "revision": d.revision} for d in corpus.documents.values()}


def _relative(path: Path) -> str:
    """Show a path relative to the project folder when possible."""
    path = Path(path).resolve()
    return str(path.relative_to(config.ROOT)) if path.is_relative_to(config.ROOT) else str(path)
