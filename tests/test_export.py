from qra import config
from qra.export import export_questionnaire, render_markdown
from qra.store import Store
from qra.workflow import Workspace
from tests.conftest import FakeModel
from tests.test_workflow import drafts_for_all_seed_questions


def test_export_contains_only_approved_answers_and_lists_open_items(tmp_path):
    ws = Workspace(Store(tmp_path / "e.sqlite"), FakeModel(drafts_for_all_seed_questions()))
    ws.import_corpus()
    qid, _ = ws.import_questionnaire(config.STARTER_SEED)
    ws.process_questionnaire(qid)
    q1, q4 = ws.store.item_by_ref("seed", "Q1"), ws.store.item_by_ref("seed", "Q4")
    ws.approve(q1["id"], "Product reviewer", answer="No | paid plans only.")
    ws.save_edit(q4["id"], "Support reviewer", "Unapproved edit")

    export = export_questionnaire(ws, "seed")
    rows = {r["question_id"]: r for r in export["questions"]}
    assert rows["Q1"]["answer"] == "No | paid plans only." and rows["Q1"]["evidence"][0] == {"passage_id": "EXPORT-v2:p1", "document": "EXPORT-v2", "version": 2}
    assert rows["Q4"]["answer"] is None and rows["Q4"]["unapproved_draft"] == "Unapproved edit"
    assert rows["Q2"]["answer"] is None and rows["Q2"]["owner"] == "Product reviewer"
    assert export["answered_and_approved"] == 1 and export["open"] == 7

    md = render_markdown(export)
    assert "No \\| paid plans only." in md and "## Open items (not answers)" in md
