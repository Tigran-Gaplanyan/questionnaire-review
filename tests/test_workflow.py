
import pytest

from qra import config
from qra.llm import AnthropicJsonModel, SimulatedFailures
from qra.store import Store
from qra.workflow import Workspace, WorkflowError
from tests.conftest import FakeModel, answered


def run_seed(ws):
    qid, _ = ws.import_questionnaire(config.STARTER_SEED)
    ws.process_questionnaire(qid)
    return {i["question_id"]: i for i in ws.store.items(questionnaire_id=qid)}


def drafts_for_all_seed_questions():
    return {
        "How do users sign in?": answered("With email and password.", ("ACCESS-v1:p1", "Users can sign in with email and password.")),
        "Who can invite team members?": answered("Account owners.", ("ACCESS-v1:p1", "Account owners can invite team members.")),
        "How often are subscriptions billed?": answered("Monthly.", ("BILLING-v1:p1", "Paid subscriptions are billed monthly in USD.")),
        "Who can download billing invoices?": answered("Account owners.", ("BILLING-v1:p1", "Account owners can download billing invoices.")),
    }


@pytest.fixture
def seeded(tmp_path):
    model = FakeModel(drafts_for_all_seed_questions())
    ws = Workspace(Store(tmp_path / "w.sqlite"), model)
    ws.import_corpus()
    return ws, model


def test_supported_unsupported_and_superseded_cases(seeded):
    ws, model = seeded
    items = run_seed(ws)
    assert items["Q3"]["status"] == "answered"
    assert items["Q2"]["status"] == "unresolved" and items["Q2"]["owner"] == "Product reviewer"
    assert items["Q2"]["reasons"][0]["code"] == "missing_evidence"
    q1 = ws.store.get("drafts", items["Q1"]["current_draft_id"])
    assert items["Q1"]["status"] == "answered" and q1["replaced_text"][0]["passage_id"] == "EXPORT-v1:p1"
    # every answered draft got a second (verification) call; the unresolved one did not
    purposes = [p for p, ctx in model.calls if ctx["question_id"] == "Q3"]
    assert purposes == ["draft_answer", "verify_answer"]
    assert [p for p, ctx in model.calls if ctx["question_id"] == "Q2"] == ["draft_answer"]


def test_superseded_citation_is_flagged_and_not_verified(tmp_path):
    model = FakeModel({**drafts_for_all_seed_questions(), "Can free-plan users export CSV?": answered("Yes, on every plan.", ("EXPORT-v1:p1", "CSV exports are available on every plan."))})
    ws = Workspace(Store(tmp_path / "w.sqlite"), model)
    ws.import_corpus()
    items = run_seed(ws)
    assert items["Q1"]["status"] == "unresolved"
    assert any("cites_current_authority" in r["text"] for r in items["Q1"]["reasons"])
    assert [p for p, ctx in model.calls if ctx["question_id"] == "Q1"] == ["draft_answer"]


def test_unsupported_claim_from_verifier_routes_to_review(tmp_path):
    verdict = {"claims": [{"claim": "SSO is not supported", "passage_ids": ["ACCESS-v1:p1"], "verdict": "not_supported", "explanation": "silent on SSO"}],
               "contradicting_passages": [], "overall": "not_supported"}
    model = FakeModel({**drafts_for_all_seed_questions(), "Is SSO supported?": answered("No, only email and password.", ("ACCESS-v1:p1", "Users can sign in with email and password."))},
                      verifications={"Is SSO supported?": verdict})
    ws = Workspace(Store(tmp_path / "w.sqlite"), model)
    ws.import_corpus()
    item = ws.ask("Is SSO supported?", "access")
    assert item["status"] == "unresolved" and item["reasons"][0]["code"] == "unsupported_claim"


def test_approval_is_reused_but_unapproved_edit_is_not(seeded):
    ws, model = seeded
    items = run_seed(ws)
    corrected = "No. CSV export is available on paid plans only."
    ws.approve(items["Q1"]["id"], "Product reviewer", answer=corrected, citation_ids=["EXPORT-v2:p1"])
    ws.save_edit(items["Q4"]["id"], "Support reviewer", "Edited but not approved")
    calls_before = len(model.calls)

    again = ws.ask("Can free-plan users export CSV?", "exports")
    assert again["status"] == "approved" and again["reused"] == 1
    assert ws.store.get("drafts", again["current_draft_id"])["answer"] == corrected
    assert len(model.calls) == calls_before  # reuse makes no model call

    chat = ws.ask("Is live chat offered?", "support")
    assert chat["status"] == "answered" and not chat["reused"]
    assert ws.store.get("drafts", chat["current_draft_id"])["answer"] != "Edited but not approved"


def test_approval_rejects_superseded_or_missing_evidence(seeded):
    ws, _ = seeded
    items = run_seed(ws)
    with pytest.raises(WorkflowError, match="superseded"):
        ws.approve(items["Q1"]["id"], "Product reviewer", citation_ids=["EXPORT-v1:p1"])
    with pytest.raises(WorkflowError, match="at least one"):
        ws.approve(items["Q2"]["id"], "Product reviewer", answer="Yes")  # unresolved draft has no citations


def test_source_version_change_marks_approval_for_review(seeded):
    ws, model = seeded
    items = run_seed(ws)
    ws.approve(items["Q3"]["id"], "Support reviewer")
    ws.approve(items["Q1"]["id"], "Product reviewer")
    affected = ws.apply_scenario(config.SOURCE_CHANGE_SCENARIO)
    assert [a["question_text"] for a in affected] == ["When is email support available?"]
    assert "SUPPORT-v1 version changed from 1 to 2" in affected[0]["status_reason"]
    assert ws.store.get("items", items["Q3"]["id"])["status"] == "unresolved"
    assert ws.store.get("items", items["Q1"]["id"])["status"] == "approved"

    again = ws.ask("When is email support available?", "support")
    assert again["status"] == "unresolved" and not again["reused"]
    assert again["reasons"][0]["code"] == "approval_needs_review"


def test_version_bump_without_text_change_and_new_superseding_document(seeded):
    ws, _ = seeded
    items = run_seed(ws)
    ws.approve(items["Q4"]["id"], "Support reviewer")
    ws.approve(items["Q6"]["id"], "Product reviewer")
    support = {"id": "SUPPORT-v1", "version": 2, "date": "2026-10-01", "status": "current", "supersedes": None,
               "passages": [{"id": "SUPPORT-v1:p1", "text": "Email support is available Monday to Friday, 09:00 to 17:00 UTC. Live chat is not offered."}]}
    _, affected = ws.publish_revision(support, source="test")
    assert [a["question_text"] for a in affected] == ["Is live chat offered?"]

    access2 = {"id": "ACCESS-v2", "version": 2, "date": "2026-10-01", "status": "current", "supersedes": "ACCESS-v1",
               "passages": [{"id": "ACCESS-v2:p1", "text": "Users can sign in with email and password."}]}
    _, affected = ws.publish_revision(access2, source="test")
    assert "ACCESS-v1 is now superseded by ACCESS-v2" in affected[0]["status_reason"]


def test_state_survives_restart(seeded, tmp_path):
    ws, _ = seeded
    items = run_seed(ws)
    ws.approve(items["Q1"]["id"], "Product reviewer", answer="No, paid plans only.")
    ws.store.close()
    reopened = Workspace(Store(tmp_path / "w.sqlite"), None)
    approval = reopened.store.approvals()[0]
    assert approval["status"] == "active" and approval["answer"] == "No, paid plans only."
    assert approval["sources"][0]["passage_id"] == "EXPORT-v2:p1" and approval["sources"][0]["version"] == 2
    assert reopened.store.get("items", items["Q1"]["id"])["status"] == "approved"


def test_failures_are_visible_and_do_not_stop_the_batch(tmp_path):
    model = SimulatedFailures(FakeModel(drafts_for_all_seed_questions()), {"Q5": "invalid_output", "Q6": "api_error", "Q7": "fabricated_citation"})
    ws = Workspace(Store(tmp_path / "w.sqlite"), model)
    ws.import_corpus()
    items = run_seed(ws)
    assert items["Q5"]["status"] == "failed" and items["Q6"]["status"] == "failed"
    assert items["Q7"]["status"] == "unresolved" and "citations_exist" in items["Q7"]["reasons"][0]["text"]
    assert items["Q8"]["status"] == "answered"  # later questions still processed
    calls = ws.store.model_calls_for_item(items["Q6"]["id"])
    assert calls[0]["provenance"] == "simulated" and calls[0]["error_type"] == "api_error"


def test_replay_miss_is_a_failure_not_an_answer(tmp_path):
    ws = Workspace(Store(tmp_path / "w.sqlite"), AnthropicJsonModel(mode="replay", replay_dir=tmp_path / "empty"))
    ws.import_corpus()
    item = ws.ask("Is JSON export available?", "exports")
    assert item["status"] == "failed" and "replay_miss" in item["reasons"][0]["text"]
    assert not (tmp_path / "empty").exists()
