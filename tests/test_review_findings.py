"""Regression tests for findings from an independent code review (see docs/LLM_USAGE.md)."""
import pytest

from qra import config
from qra.corpus import Corpus
from qra.evidence import classify_conflict
from qra.store import Store
from qra.workflow import Workspace, WorkflowError
from tests.conftest import FakeModel, unresolved
from tests.test_workflow import drafts_for_all_seed_questions, run_seed

EXPORT_V2 = {"id": "EXPORT-v2", "date": "2026-08-01", "status": "current",
             "passages": [{"id": "EXPORT-v2:p1", "text": "CSV exports are available on paid plans only. Free-plan users cannot export CSV."}]}


@pytest.fixture
def ws(tmp_path):
    workspace = Workspace(Store(tmp_path / "r.sqlite"), FakeModel(drafts_for_all_seed_questions()))
    workspace.import_corpus()
    return workspace


def test_items_reusing_a_replaced_approval_return_to_review_on_source_change(ws):
    items = run_seed(ws)
    ws.approve(items["Q1"]["id"], "Product reviewer", answer="No.")
    reused = ws.ask("Can free-plan users export CSV?", "exports")
    assert reused["reused"] and reused["status"] == "approved"
    ws.approve(items["Q1"]["id"], "Product reviewer", answer="No, paid plans only.")  # replaces approval #1
    ws.publish_revision({**EXPORT_V2, "version": 3, "supersedes": "EXPORT-v1"}, source="test")
    assert ws.store.get("items", reused["id"])["status"] == "unresolved"


def test_republishing_with_changed_authority_invalidates_approval(ws):
    items = run_seed(ws)
    ws.approve(items["Q1"]["id"], "Product reviewer")
    _, affected = ws.publish_revision({**EXPORT_V2, "version": 2, "supersedes": None}, source="test")
    assert [a["question_text"] for a in affected] == ["Can free-plan users export CSV?"]
    assert not ws.ask("Can free-plan users export CSV?", "exports")["reused"]


def test_unresolved_draft_does_not_present_answer_text(tmp_path):
    bad = {**unresolved("JSON export is not documented."), "answer": "No, JSON export is not supported."}
    ws = Workspace(Store(tmp_path / "u.sqlite"), FakeModel({**drafts_for_all_seed_questions(), "Is JSON export available?": bad}))
    ws.import_corpus()
    item = run_seed(ws)["Q2"]
    draft = ws.store.get("drafts", item["current_draft_id"])
    assert item["status"] == "unresolved" and draft["answer"] == ""
    assert "No, JSON export is not supported." in draft["note"]


def test_draft_written_against_an_old_document_version_cannot_be_approved_unchanged(ws):
    items = run_seed(ws)
    ws.apply_scenario(config.SOURCE_CHANGE_SCENARIO)
    with pytest.raises(WorkflowError, match="SUPPORT-v1"):
        ws.approve(items["Q3"]["id"], "Support reviewer")
    ws.approve(items["Q3"]["id"], "Support reviewer", answer="Monday to Friday, 08:00 to 18:00 UTC.")  # edited: accepted


def test_leaving_an_approved_item_unresolved_stops_reuse(ws):
    items = run_seed(ws)
    ws.approve(items["Q1"]["id"], "Product reviewer")
    ws.mark_unresolved(items["Q1"]["id"], "Product reviewer", "Policy under legal review")
    assert ws.store.approvals()[0]["status"] == "needs_review"
    assert not ws.ask("Can free-plan users export CSV?", "exports")["reused"]


def test_editing_a_reused_item_clears_the_reused_flag(ws):
    items = run_seed(ws)
    ws.approve(items["Q1"]["id"], "Product reviewer")
    reused = ws.ask("Can free-plan users export CSV?", "exports")
    assert ws.save_edit(reused["id"], "Product reviewer", "Changed wording")["reused"] == 0


def test_malformed_or_internal_conflicts_are_not_resolved_by_metadata(ws):
    corpus: Corpus = ws.corpus()
    assert not classify_conflict([], corpus)["resolved_by_metadata"]
    assert not classify_conflict(["SUPPORT-v1:p1"], corpus)["resolved_by_metadata"]
    assert classify_conflict(["EXPORT-v1:p1"], corpus)["resolved_by_metadata"]  # superseded text only
    assert classify_conflict(["EXPORT-v1:p1", "EXPORT-v2:p1"], corpus)["resolved_by_metadata"]
