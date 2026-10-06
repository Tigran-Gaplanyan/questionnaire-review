"""Checks on the model's draft, done in plain code (not by the model).

  citations_exist                - every quoted passage ID is real
  excerpts_verbatim              - every quote really appears in its passage
  cites_current_authority        - no quote comes from a superseded (replaced) document
  conflicts_reference_existing   - conflicts only mention real passages
  conflicts_resolved_by_metadata - a conflict is only "settled" by an explicit supersedes link

The second model pass (verify_answer) is interpreted here too, by support_checks().
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .corpus import Corpus

# Which "reason" a failed check gives on the review queue.
REASON_FOR_CHECK = {
    "output_consistent": "evidence_check_failed",
    "citations_exist": "evidence_check_failed",
    "excerpts_verbatim": "evidence_check_failed",
    "cites_current_authority": "evidence_check_failed",
    "conflicts_reference_existing": "evidence_check_failed",
    "conflicts_resolved_by_metadata": "unresolved_conflict",
    "claims_supported": "unsupported_claim",
    "no_unresolved_contradiction": "unresolved_conflict",
    "support_check_ran": "verification_unavailable",
}


@dataclass
class Check:
    """The result of one check: its name, whether it passed, and an explanation."""
    name: str
    passed: bool
    detail: str
    blocking: bool = True  # if a blocking check fails, the question goes to a reviewer

    def as_dict(self) -> dict:
        """The check as a dict (how it is saved with the draft)."""
        return asdict(self)


# --- Small helpers ----------------------------------------------------------------------------

def normalize(text: str) -> str:
    """Lower-case, single spaces, plain quotes and dashes, so tiny formatting differences don't matter."""
    for fancy, plain in (("‘", "'"), ("’", "'"), ("“", '"'), ("”", '"'), ("–", "-"), ("—", "-"), ("…", "...")):
        text = text.replace(fancy, plain)
    return " ".join(text.split()).casefold()


def excerpt_in_passage(excerpt: str, passage_text: str) -> bool:
    """True if the quote appears in the passage. A quote may skip text with '...'; the parts must appear in order."""
    passage = normalize(passage_text)
    parts = [part.strip(" .,;:\"'") for part in normalize(excerpt).split("...")]
    parts = [part for part in parts if part]
    if not parts:
        return False
    position = 0
    for part in parts:
        found = passage.find(part, position)
        if found < 0:
            return False
        position = found + len(part)
    return True


def is_current(passage_id: str, corpus: Corpus) -> bool:
    """True if the passage exists and its document has not been superseded."""
    return passage_id in corpus.passages and not corpus.is_superseded(corpus.passages[passage_id].doc_id)


def classify_conflict(passage_ids: list[str], corpus: Corpus) -> dict:
    """Decide whether a disagreement between passages is settled by supersedes metadata.

    Settled only if a superseded passage is involved and at most one current document is left.
    Two current documents that disagree are never settled automatically (a newer date does not count).
    """
    unknown = [p for p in passage_ids if p not in corpus.passages]
    current = [p for p in passage_ids if is_current(p, corpus)]
    superseded = [p for p in passage_ids if p in corpus.passages and p not in current]
    current_docs = sorted({corpus.passages[p].doc_id for p in current})

    resolved = bool(superseded) and len(current_docs) <= 1 and not unknown
    if resolved:
        resolution = "Resolved by explicit supersedes metadata: " + ", ".join(
            f"{corpus.passages[p].doc_id} is {corpus.authority(corpus.passages[p].doc_id)}" for p in superseded)
    elif len(current_docs) >= 2:
        resolution = f"Current documents {', '.join(current_docs)} disagree and no supersedes link resolves it."
    else:
        resolution = "The reported conflict does not involve a superseded document (or names unknown passages); a reviewer must check it."
    return {"passage_ids": passage_ids, "current": current, "superseded": superseded, "unknown": unknown,
            "resolved_by_metadata": resolved, "resolution": resolution}


def replaced_text(passage_ids: list[str], corpus: Corpus) -> list[dict]:
    """Old (superseded) passages to show next to an answer, so the reviewer sees what was replaced."""
    shown = []

    def add(passage_id: str, replaced_by: str) -> None:
        """Add one old passage to the list (each passage only once)."""
        if passage_id in corpus.passages and passage_id not in [s["passage_id"] for s in shown]:
            passage = corpus.passages[passage_id]
            doc = corpus.documents[passage.doc_id]
            shown.append({"passage_id": passage_id, "doc_id": doc.id, "version": doc.version, "date": doc.date,
                          "text": passage.text, "replaced_by": replaced_by})

    for passage_id in passage_ids:
        if passage_id not in corpus.passages:
            continue
        doc_id = corpus.passages[passage_id].doc_id
        if corpus.is_superseded(doc_id):
            add(passage_id, corpus.superseded_by[doc_id])  # the passage itself is old
        else:
            for old_doc_id in corpus.replaced_chain(doc_id):  # show what this document replaced
                for old in corpus.documents[old_doc_id].passages:
                    add(old.id, corpus.superseded_by[old_doc_id])
    return shown


# --- Checks on the draft (pass 1) -------------------------------------------------------------

def check_draft(draft: dict, corpus: Corpus) -> tuple[list[Check], list[dict], list[dict]]:
    """Run the code checks. Returns (checks, conflicts with their classification, replaced text)."""
    answered = draft["status"] == "answered"
    cited = [c["passage_id"] for c in draft["citations"]]
    conflicts = [classify_conflict(c["passage_ids"], corpus) | {"description": c["description"], "source": "draft"}
                 for c in draft["conflicts"]]

    # For an unresolved draft the checks are informational: it goes to a reviewer anyway.
    checks = [
        _check_consistent(draft, answered),
        _check_citations_exist(cited, corpus, blocking=answered),
        _check_excerpts(draft["citations"], corpus, blocking=answered),
        _check_no_superseded_support(cited, corpus, blocking=answered),
        _check_conflict_references(conflicts, blocking=answered),
        _check_conflicts_resolved(conflicts, blocking=answered),
    ]
    old_passages_in_conflicts = [p for c in conflicts for p in c["superseded"]]
    return checks, conflicts, replaced_text(cited + old_passages_in_conflicts, corpus)


def _check_consistent(draft: dict, answered: bool) -> Check:
    """An answered draft needs answer text and quotes; an unresolved draft should have no answer text."""
    if answered:
        ok = bool(draft["answer"].strip()) and bool(draft["citations"])
        return Check("output_consistent", ok, "Answer and citations present." if ok else
                     "Answered drafts need a non-empty answer and at least one citation.")
    ok = not draft["answer"].strip()
    return Check("output_consistent", ok, "Unresolved draft makes no answer claim." if ok else
                 "Unresolved draft also contains answer text; it is kept as a note, not shown as an answer.", blocking=False)


def _check_citations_exist(cited: list[str], corpus: Corpus, blocking: bool) -> Check:
    """Every quoted passage id must exist in the documents."""
    missing = [p for p in cited if p not in corpus.passages]
    detail = f"Cited passages not in the corpus: {', '.join(missing)}" if missing else f"All {len(cited)} cited passages exist."
    return Check("citations_exist", not missing, detail, blocking)


def _check_excerpts(citations: list[dict], corpus: Corpus, blocking: bool) -> Check:
    """Every quote must really appear in the passage it claims to come from."""
    bad = [c["passage_id"] for c in citations
           if c["passage_id"] in corpus.passages and not excerpt_in_passage(c["excerpt"], corpus.passages[c["passage_id"]].text)]
    detail = f"Excerpt not found in passage: {', '.join(bad)}" if bad else "Every excerpt appears in its passage."
    return Check("excerpts_verbatim", not bad, detail, blocking)


def _check_no_superseded_support(cited: list[str], corpus: Corpus, blocking: bool) -> Check:
    """No quote may come from a superseded document (rule 2)."""
    old = [p for p in cited if p in corpus.passages and not is_current(p, corpus)]
    detail = f"Superseded passages cited as support: {', '.join(old)}" if old else "No superseded passage is cited as support."
    return Check("cites_current_authority", not old, detail, blocking)


def _check_conflict_references(conflicts: list[dict], blocking: bool) -> Check:
    """Conflicts reported by the model must only mention real passages."""
    unknown = sorted({p for c in conflicts for p in c["unknown"]})
    detail = f"Conflicts mention unknown passages: {', '.join(unknown)}" if unknown else "Conflict references exist."
    return Check("conflicts_reference_existing", not unknown, detail, blocking)


def _check_conflicts_resolved(conflicts: list[dict], blocking: bool) -> Check:
    """Every reported conflict must be settled by an explicit supersedes link; otherwise a reviewer decides."""
    open_conflicts = [c for c in conflicts if not c["resolved_by_metadata"]]
    if open_conflicts:
        detail = "; ".join(c["resolution"] for c in open_conflicts)
    elif conflicts:
        detail = "All reported conflicts are resolved by supersedes metadata."
    else:
        detail = "No conflicts reported."
    return Check("conflicts_resolved_by_metadata", not open_conflicts, detail, blocking)


# --- Interpreting the second model pass (verify_answer) ---------------------------------------

def support_checks(verification: dict, corpus: Corpus, cited: list[str]) -> tuple[list[Check], list[dict], list[dict]]:
    """Turn the verifier's verdicts into checks. Returns (checks, extra conflicts, extra replaced text)."""
    claims = verification["claims"]
    weak = [c for c in claims if c["verdict"] != "supported"]
    supported = bool(claims) and not weak and verification["overall"] == "supported"
    if weak:
        detail = "; ".join(f"'{c['claim']}' is {c['verdict'].replace('_', ' ')}: {c['explanation']}" for c in weak)
    elif supported:
        detail = f"All {len(claims)} claims supported by cited passages."
    else:
        detail = "Verifier did not confirm support."
    checks = [Check("claims_supported", supported, detail)]

    # A *current* passage that disagrees with the answer is an unresolved conflict.
    # A *superseded* passage that disagrees is expected (that is what supersedes is for).
    contradictions = verification["contradicting_passages"]
    current = [c for c in contradictions if is_current(c["passage_id"], corpus)]
    unknown = [c["passage_id"] for c in contradictions if c["passage_id"] not in corpus.passages]
    if current:
        detail = "; ".join(f"{c['passage_id']} (current) disagrees: {c['explanation']}" for c in current)
    elif contradictions:
        detail = "Only superseded passages disagree; supersedes metadata resolves them."
    else:
        detail = "No passage disagrees with the answer."
    if unknown:
        detail += f" Verifier named unknown passages: {', '.join(unknown)}."
    checks.append(Check("no_unresolved_contradiction", not current, detail))

    extra_conflicts = [
        classify_conflict(sorted(set(cited) | {c["passage_id"]}), corpus) | {"description": c["explanation"], "source": "verifier"}
        for c in contradictions if c["passage_id"] not in cited
    ]
    extra_replaced = replaced_text([c["passage_id"] for c in contradictions], corpus)
    return checks, extra_conflicts, extra_replaced
