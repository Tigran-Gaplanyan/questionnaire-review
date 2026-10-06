"""Checks the evaluator itself with canned outputs: a correct application must pass, and a
deliberately wrong one must fail. This tests the harness, not the model."""
import json

from qra.evaluate import run_evaluation
from tests.conftest import FakeModel, answered, unresolved
from tests.test_workflow import drafts_for_all_seed_questions

BUYER_B = {
    "How often do scheduled exports run?": unresolved("Two current documents disagree about the export schedule.", conflicts=[("EXPORT-SCHEDULE-v1:p1", "EXPORT-FAQ-v1:p1")]),
    "Can users sign in with single sign-on (SSO)?": unresolved("SSO is not documented."),
    "Can users on the free plan export CSV files?": answered("No. CSV export is for paid plans only.", ("EXPORT-v2:p1", "Free-plan users cannot export CSV.")),
}


def test_harness_passes_a_correct_application(tmp_path):
    model = FakeModel({**drafts_for_all_seed_questions(), **BUYER_B})
    assert run_evaluation("replay", tmp_path, model=model) == 0
    report = json.loads((tmp_path / "results.json").read_text())
    failed = [(c["id"], e) for c in report["cases"] for e in c["expectations"] if not e["passed"]]
    assert not failed, failed
    assert report["additional_checks_passed"] == report["additional_checks_total"] == 6


def test_harness_fails_an_application_that_guesses(tmp_path):
    wrong = {"Is JSON export available?": answered("Yes, JSON export is available.", ("EXPORT-v2:p1", "CSV exports are available on paid plans only."))}
    verdict = {"Is JSON export available?": {"claims": [{"claim": "JSON", "passage_ids": [], "verdict": "supported", "explanation": "wrongly accepted"}],
                                             "contradicting_passages": [], "overall": "supported"}}
    model = FakeModel({**drafts_for_all_seed_questions(), **BUYER_B, **wrong}, verifications=verdict)
    assert run_evaluation("replay", tmp_path, model=model) == 1
    report = json.loads((tmp_path / "results.json").read_text())
    assert [c["id"] for c in report["cases"] if not c["passed"]] == ["RC2-unsupported"]
