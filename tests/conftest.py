"""Offline test fixtures. FakeModel returns canned outputs labelled provenance="test-fake";
it exercises the code paths around the model, not the model's judgement (that is what the
recorded real responses and `python -m qra evaluate` are for)."""
from __future__ import annotations

import json
import re
from html import unescape

import pytest

from qra.llm import ModelResult
from qra.store import Store
from qra.workflow import Workspace

SUPPORTED = {"claims": [{"claim": "stub", "passage_ids": [], "verdict": "supported", "explanation": "stub"}],
             "contradicting_passages": [], "overall": "supported"}


def answered(answer: str, *cites: tuple[str, str], conflicts=()) -> dict:
    return {"status": "answered", "answer": answer, "citations": [{"passage_id": p, "excerpt": e} for p, e in cites],
            "conflicts": [{"passage_ids": list(c), "description": "disagree"} for c in conflicts], "unresolved_reason": ""}


def unresolved(why: str, conflicts=()) -> dict:
    return {"status": "unresolved", "answer": "", "citations": [],
            "conflicts": [{"passage_ids": list(c), "description": "disagree"} for c in conflicts], "unresolved_reason": why}


DEFAULT_DRAFTS = {
    "Can free-plan users export CSV?": answered("No. CSV export is for paid plans only.", ("EXPORT-v2:p1", "Free-plan users cannot export CSV."), conflicts=[("EXPORT-v1:p1", "EXPORT-v2:p1")]),
    "Is JSON export available?": unresolved("JSON export is not documented."),
    "When is email support available?": answered("Monday to Friday, 09:00 to 17:00 UTC.", ("SUPPORT-v1:p1", "Monday to Friday, 09:00 to 17:00 UTC")),
    "Is live chat offered?": answered("No, live chat is not offered.", ("SUPPORT-v1:p1", "Live chat is not offered.")),
}


class FakeModel:
    def __init__(self, drafts: dict | None = None, verifications: dict | None = None):
        self.drafts = {**DEFAULT_DRAFTS, **(drafts or {})}
        self.verifications = verifications or {}
        self.calls: list[tuple[str, dict]] = []

    def complete_json(self, *, purpose, prompt_name, system, user, schema, output_model, effort, context) -> ModelResult:
        self.calls.append((purpose, context))
        question = unescape(re.search(r"<question>(.*?)</question>", user, re.S).group(1))
        data = self.drafts[question] if purpose == "draft_answer" else self.verifications.get(question, SUPPORTED)
        validated = output_model.model_validate(data).model_dump()
        return ModelResult(ok=True, provenance="test-fake", purpose=purpose, prompt_name=prompt_name, model_requested="fake",
                           data=validated, raw_text=json.dumps(data), request_hash="fake")


@pytest.fixture
def fake():
    return FakeModel()


@pytest.fixture
def ws(tmp_path, fake):
    workspace = Workspace(Store(tmp_path / "test.sqlite"), fake)
    findings = workspace.import_corpus()
    assert not [f for f in findings if f.level == "error"]
    return workspace
