from qra.web import create_app
from tests.conftest import FakeModel
from tests.test_workflow import drafts_for_all_seed_questions


def test_review_flow_through_the_web_app(tmp_path):
    app = create_app(tmp_path / "web.sqlite", mode="replay", model=FakeModel(drafts_for_all_seed_questions()))
    client = app.test_client()
    assert client.post("/questionnaires/run", data={"questionnaire": "seed"}).status_code == 302
    page = client.get("/?status=unresolved").get_data(as_text=True)
    assert "Is JSON export available?" in page and "Product reviewer" in page

    q1 = client.get("/items/1").get_data(as_text=True)
    assert "Replaced text" in q1 and "EXPORT-v1:p1" in q1 and "<mark>" in q1

    # approving with a superseded passage is rejected; a valid approval is accepted and reused
    rejected = client.post("/items/1/approve", data={"reviewer": "Product reviewer", "answer": "Yes", "citations": ["EXPORT-v1:p1"]}, follow_redirects=True)
    assert "Rejected" in rejected.get_data(as_text=True)
    client.post("/items/1/approve", data={"reviewer": "Product reviewer", "answer": "No, paid plans only.", "citations": ["EXPORT-v2:p1"]})
    reused = client.post("/ask", data={"text": "Can free-plan users export CSV?", "topic": "exports"}, follow_redirects=True).get_data(as_text=True)
    assert "Reused for this question" in reused and "No, paid plans only." in reused
    # the superseded policy stays visible after approval and on the reused answer (regression)
    assert "Replaced text" in reused and "CSV exports are available on every plan." in reused
    assert "Replaced text" in client.get("/items/1").get_data(as_text=True)

    exported = client.get("/questionnaires/seed/export.md").get_data(as_text=True)
    assert "No, paid plans only." in exported and "## Open items (not answers)" in exported
    for path in ("/", "/approvals", "/documents", "/calls/1"):
        assert client.get(path).status_code == 200
    changed = client.post("/documents/scenario", data={"scenario": "support-v1-version-2"}, follow_redirects=True).get_data(as_text=True)
    assert "Applied" in changed and "08:00 to 18:00" in changed and "Earlier revisions" in changed
