"""What we send to the model, and the shape of what it must send back.

There are two model passes:
  1. draft_answer  - answer the question with exact quotes, or say why it can't be answered.
  2. verify_answer - check each claim in that answer against the quoted passages.

The instructions (system prompt) live in qra/prompts/*.md. The documents and the question go in
the user message, wrapped in tags, so document text is kept apart from our instructions.

Note: saved responses in replays/ are matched on the exact request text, so changing any text
here (or in the prompt files) means the responses have to be recorded again.
"""
from __future__ import annotations

from html import escape
from typing import Literal

from pydantic import BaseModel, ConfigDict

from . import config
from .corpus import Corpus

DRAFT_PROMPT = "draft_answer.v1"
VERIFY_PROMPT = "verify_answer.v1"


def load_prompt(name: str) -> str:
    """Read a system prompt from qra/prompts/<name>.md."""
    return (config.PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


# --- Expected output, as Python classes (used to validate the model's JSON) --------------------

class _Strict(BaseModel):
    """Base class: the model's JSON may not contain any field we did not ask for."""
    model_config = ConfigDict(extra="forbid")  # reject unexpected fields


class Citation(_Strict):
    """One quote: the passage id and the exact words copied from it."""
    passage_id: str
    excerpt: str


class Conflict(_Strict):
    """A disagreement the model noticed between passages."""
    passage_ids: list[str]
    description: str


class DraftOutput(_Strict):
    """Pass 1 output: the draft answer with quotes, or the reason it cannot be answered."""
    status: Literal["answered", "unresolved"]
    answer: str
    citations: list[Citation]
    conflicts: list[Conflict]
    unresolved_reason: str


class ClaimVerdict(_Strict):
    """Pass 2: one claim from the draft and whether the quoted passages support it."""
    claim: str
    passage_ids: list[str]
    verdict: Literal["supported", "not_supported", "contradicted"]
    explanation: str


class Contradiction(_Strict):
    """Pass 2: a passage anywhere in the documents that disagrees with the draft answer."""
    passage_id: str
    explanation: str


class VerifyOutput(_Strict):
    """Pass 2 output: a verdict per claim, disagreeing passages, and an overall verdict."""
    claims: list[ClaimVerdict]
    contradicting_passages: list[Contradiction]
    overall: Literal["supported", "not_supported"]


# --- The same shapes as JSON Schema (sent to the API, which then only returns matching JSON) ---

STRING = {"type": "string"}
STRING_LIST = {"type": "array", "items": STRING}

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["answered", "unresolved"]},
        "answer": STRING,
        "citations": {"type": "array", "items": {
            "type": "object",
            "properties": {"passage_id": STRING, "excerpt": STRING},
            "required": ["passage_id", "excerpt"],
            "additionalProperties": False,
        }},
        "conflicts": {"type": "array", "items": {
            "type": "object",
            "properties": {"passage_ids": STRING_LIST, "description": STRING},
            "required": ["passage_ids", "description"],
            "additionalProperties": False,
        }},
        "unresolved_reason": STRING,
    },
    "required": ["status", "answer", "citations", "conflicts", "unresolved_reason"],
    "additionalProperties": False,
}

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "claim": STRING,
                "passage_ids": STRING_LIST,
                "verdict": {"type": "string", "enum": ["supported", "not_supported", "contradicted"]},
                "explanation": STRING,
            },
            "required": ["claim", "passage_ids", "verdict", "explanation"],
            "additionalProperties": False,
        }},
        "contradicting_passages": {"type": "array", "items": {
            "type": "object",
            "properties": {"passage_id": STRING, "explanation": STRING},
            "required": ["passage_id", "explanation"],
            "additionalProperties": False,
        }},
        "overall": {"type": "string", "enum": ["supported", "not_supported"]},
    },
    "required": ["claims", "contradicting_passages", "overall"],
    "additionalProperties": False,
}


# --- Building the user message ----------------------------------------------------------------

def documents_block(corpus: Corpus) -> str:
    """All documents as tagged text, e.g.

    <document id="EXPORT-v2" version="2" date="2026-08-01" supersedes="EXPORT-v1" authority="current">
    <passage id="EXPORT-v2:p1">CSV exports are available on paid plans only. ...</passage>
    </document>

    Text is escaped so a document cannot break out of its tags.
    """
    lines = ["<documents>"]
    for doc_id in sorted(corpus.documents):
        d = corpus.documents[doc_id]
        attributes = f'id="{escape(d.id)}" version="{d.version}" date="{escape(d.date)}"'
        if d.supersedes:
            attributes += f' supersedes="{escape(d.supersedes)}"'
        attributes += f' authority="{escape(corpus.authority(d.id))}"'
        lines.append(f"<document {attributes}>")
        for p in d.passages:
            lines.append(f'<passage id="{escape(p.id)}">{escape(p.text, quote=False)}</passage>')
        lines.append("</document>")
    lines.append("</documents>")
    return "\n".join(lines)


def draft_messages(corpus: Corpus, question_text: str) -> tuple[str, str]:
    """Returns (system prompt, user message) for pass 1."""
    user = documents_block(corpus) + "\n\n" + f"<question>{escape(question_text, quote=False)}</question>"
    return load_prompt(DRAFT_PROMPT), user


def verify_messages(corpus: Corpus, question_text: str, answer: str, cited_ids: list[str]) -> tuple[str, str]:
    """Returns (system prompt, user message) for pass 2."""
    user = (
        documents_block(corpus) + "\n\n"
        + f"<question>{escape(question_text, quote=False)}</question>\n\n"
        + f"<draft_answer>{escape(answer, quote=False)}</draft_answer>\n\n"
        + f"<cited_passages>{escape(', '.join(cited_ids), quote=False)}</cited_passages>"
    )
    return load_prompt(VERIFY_PROMPT), user
