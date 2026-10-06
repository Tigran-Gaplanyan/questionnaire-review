"""Documents and questionnaires: load them, check them, and work out which documents count.

Rule 2 of the exercise: a document replaces another ONLY through its explicit `supersedes`
field. Dates and the declared `status` field are kept for display but never decide anything.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Finding:
    """A problem found in an input file. "error" blocks the import; "warning" is shown."""
    level: str
    code: str
    message: str
    ref: str = ""


@dataclass(frozen=True)
class Passage:
    """One quotable piece of a document, e.g. id "EXPORT-v2:p1"."""
    id: str
    doc_id: str
    text: str

    @property
    def text_sha(self) -> str:
        """A fingerprint of the text, used to notice when the text changes."""
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Document:
    """One document (e.g. EXPORT-v2) at one revision: its version, date, supersedes link and passages."""
    id: str
    version: int
    date: str
    declared_status: str
    supersedes: str | None
    passages: tuple[Passage, ...]
    revision: int = 1
    source: str = ""


@dataclass(frozen=True)
class Question:
    """One question from a questionnaire: its id, topic (used to find the reviewer) and text."""
    id: str
    topic: str
    text: str


@dataclass
class Questionnaire:
    """A questionnaire file after loading: its questions plus any problems found in it."""
    id: str
    name: str
    questions: list[Question]
    findings: list[Finding] = field(default_factory=list)


class Corpus:
    """The current version of every document, plus which documents have been replaced."""

    def __init__(self, documents: list[Document], owners: dict[str, str]):
        """Index the documents and passages by id and work out which documents are superseded."""
        self.documents = {d.id: d for d in documents}
        self.passages = {p.id: p for d in documents for p in d.passages}
        self.owners = dict(owners)
        # superseded_by["EXPORT-v1"] == "EXPORT-v2" because EXPORT-v2 says it supersedes EXPORT-v1.
        self.superseded_by = {}
        for d in documents:
            if d.supersedes in self.documents:
                self.superseded_by[d.supersedes] = d.id

    def is_superseded(self, doc_id: str) -> bool:
        """True if another document explicitly says it supersedes `doc_id`."""
        return doc_id in self.superseded_by

    def authority(self, doc_id: str) -> str:
        """Human-readable authority, e.g. "current" or "superseded by EXPORT-v2"."""
        if self.is_superseded(doc_id):
            return f"superseded by {self.superseded_by[doc_id]}"
        return "current"

    def replaced_chain(self, doc_id: str) -> list[str]:
        """Older documents that this one replaces, directly or through a chain (newest first)."""
        chain = []
        doc = self.documents.get(doc_id)
        while doc and doc.supersedes and doc.supersedes not in chain and doc.supersedes != doc_id:
            chain.append(doc.supersedes)
            doc = self.documents.get(doc.supersedes)
        return chain

    def owner_for(self, topic: str) -> str | None:
        """The reviewer for a topic from the topic mapping, or None if the topic is not mapped."""
        return self.owners.get(topic)


# --- Loading ---------------------------------------------------------------------------------


def read_json(path: Path) -> dict:
    """Read a JSON file and return its content."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def documents_from_raw(raw_docs: list[dict], source: str) -> list[Document]:
    """Turn the JSON documents from seed.json into Document objects."""
    documents = []
    for raw in raw_docs:
        doc_id = str(raw.get("id", ""))
        passages = tuple(Passage(id=str(p.get("id", "")), doc_id=doc_id, text=str(p.get("text", "")))
                         for p in raw.get("passages", []))
        documents.append(Document(
            id=doc_id, version=raw.get("version"), date=str(raw.get("date", "")),
            declared_status=str(raw.get("status", "")), supersedes=raw.get("supersedes"),
            passages=passages, source=source,
        ))
    return documents


def load_questionnaire(path: Path, owners: dict[str, str]) -> Questionnaire:
    """Read a questionnaire file. Bad questions are skipped and reported as findings."""
    raw = read_json(path)
    questionnaire_id = str(raw.get("questionnaire_id") or Path(path).stem)
    questionnaire = Questionnaire(id=questionnaire_id, name=str(raw.get("name") or questionnaire_id), questions=[])
    seen = set()
    for item in raw.get("questions", []):
        q = Question(id=str(item.get("id", "")).strip(), topic=str(item.get("topic", "")).strip(), text=str(item.get("text", "")).strip())
        if not q.id:
            questionnaire.findings.append(Finding("error", "missing_question_id", "A question has no id."))
        elif q.id in seen:
            questionnaire.findings.append(Finding("error", "duplicate_question_id", f"Question {q.id} appears more than once; only the first is used.", q.id))
        elif not q.text:
            questionnaire.findings.append(Finding("error", "empty_question", f"Question {q.id} has no text.", q.id))
        else:
            seen.add(q.id)
            if q.topic not in owners:
                questionnaire.findings.append(Finding("warning", "unmapped_topic", f"Question {q.id} has topic '{q.topic}' with no reviewer in the mapping.", q.id))
            questionnaire.questions.append(q)
    if not questionnaire.questions:
        questionnaire.findings.append(Finding("error", "no_questions", f"{path} contains no usable questions."))
    return questionnaire


def question_key(text: str) -> str:
    """What counts as "the same question" for reuse: identical text, ignoring spacing and capitals."""
    return " ".join(text.split()).casefold()


# --- Validation ------------------------------------------------------------------------------


def validate_documents(docs: list[Document], owners: dict[str, str]) -> list[Finding]:
    """Check IDs, references and supersedes links. Returns a list of problems (empty if all good)."""
    findings = _check_ids_and_text(docs) + _check_supersedes(docs)
    if not owners:
        findings.append(Finding("warning", "no_owner_mapping", "No topic-to-reviewer mapping was loaded."))
    return findings


def _check_ids_and_text(docs: list[Document]) -> list[Finding]:
    """Check document and passage ids, versions and text. Returns the problems found."""
    findings = []
    doc_ids, passage_owner = set(), {}
    for d in docs:
        if not d.id:
            findings.append(Finding("error", "missing_document_id", "A document has no id."))
            continue
        if d.id in doc_ids:
            findings.append(Finding("error", "duplicate_document_id", f"Document {d.id} appears more than once.", d.id))
        doc_ids.add(d.id)
        if not isinstance(d.version, int) or isinstance(d.version, bool) or d.version < 1:
            findings.append(Finding("error", "invalid_version", f"{d.id} has a non-integer or missing version.", d.id))
        if not d.passages:
            findings.append(Finding("error", "no_passages", f"{d.id} has no passages.", d.id))
        for p in d.passages:
            if not p.id:
                findings.append(Finding("error", "missing_passage_id", f"A passage in {d.id} has no id.", d.id))
                continue
            if p.id in passage_owner:
                findings.append(Finding("error", "duplicate_passage_id", f"Passage {p.id} appears in {passage_owner[p.id]} and {d.id}.", p.id))
            passage_owner[p.id] = d.id
            if not p.id.startswith(d.id + ":"):
                findings.append(Finding("warning", "passage_prefix", f"Passage {p.id} is not prefixed with its document id {d.id}.", p.id))
            if not p.text.strip():
                findings.append(Finding("error", "empty_passage", f"Passage {p.id} has no text.", p.id))
    return findings


def _check_supersedes(docs: list[Document]) -> list[Finding]:
    """Check the supersedes links: missing targets, loops, several replacements, and status mismatches."""
    findings = []
    by_id = {d.id: d for d in docs}
    replaced_by = {}  # old document id -> list of documents that say they replace it
    for d in docs:
        if d.supersedes is None:
            continue
        if d.supersedes == d.id:
            findings.append(Finding("error", "self_supersedes", f"{d.id} supersedes itself.", d.id))
        elif d.supersedes not in by_id:
            findings.append(Finding("error", "missing_supersedes_target", f"{d.id} supersedes unknown document {d.supersedes}.", d.id))
        else:
            replaced_by.setdefault(d.supersedes, []).append(d.id)

    for old, newer in replaced_by.items():
        if len(newer) > 1:
            findings.append(Finding("warning", "multiple_replacements",
                                    f"{old} is superseded by several documents: {', '.join(newer)}. Answers citing them need review.", old))

    for d in docs:  # follow each chain of supersedes links; coming back to a document means a loop
        seen, current = {d.id}, d
        while current.supersedes in by_id:
            if current.supersedes in seen:
                findings.append(Finding("error", "supersedes_cycle", f"Supersedes chain starting at {d.id} loops.", d.id))
                break
            seen.add(current.supersedes)
            current = by_id[current.supersedes]

    for d in docs:  # the declared status is only informational; point out when it disagrees
        derived = "superseded" if d.id in replaced_by else "current"
        if d.declared_status and d.declared_status != derived:
            findings.append(Finding("warning", "status_disagrees_with_supersedes",
                                    f"{d.id} declares status '{d.declared_status}' but its supersedes links make it '{derived}'. The supersedes links are used.", d.id))
    return findings
