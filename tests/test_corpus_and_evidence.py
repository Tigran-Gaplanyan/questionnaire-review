import json

from qra import config
from qra.corpus import Corpus, documents_from_raw, load_questionnaire, question_key, validate_documents
from qra.evidence import check_draft, classify_conflict, excerpt_in_passage, support_checks
from tests.conftest import answered


def full_corpus() -> Corpus:
    seed = json.loads(config.STARTER_SEED.read_text())
    docs = documents_from_raw(seed["documents"], "seed") + documents_from_raw(json.loads(config.ADDITION_DOCUMENTS.read_text())["documents"], "add")
    return Corpus(docs, seed["owners"])


def test_supplied_corpus_validates_cleanly():
    seed = json.loads(config.STARTER_SEED.read_text())
    docs = documents_from_raw(seed["documents"], "seed") + documents_from_raw(json.loads(config.ADDITION_DOCUMENTS.read_text())["documents"], "add")
    assert validate_documents(docs, seed["owners"]) == []


def test_authority_comes_only_from_supersedes():
    corpus = full_corpus()
    assert corpus.is_superseded("EXPORT-v1") and corpus.superseded_by["EXPORT-v1"] == "EXPORT-v2"
    # EXPORT-FAQ-v1 is newer than EXPORT-SCHEDULE-v1 but has no supersedes link: neither wins.
    assert not corpus.is_superseded("EXPORT-SCHEDULE-v1") and not corpus.is_superseded("EXPORT-FAQ-v1")


def test_broken_corpus_reports_missing_and_duplicate_references():
    raw = [
        {"id": "A", "version": 1, "date": "", "status": "current", "supersedes": "MISSING", "passages": [{"id": "A:p1", "text": "x"}]},
        {"id": "B", "version": 1, "date": "", "status": "superseded", "supersedes": None, "passages": [{"id": "A:p1", "text": "y"}]},
        {"id": "C", "version": "two", "date": "", "status": "current", "supersedes": "D", "passages": [{"id": "C:p1", "text": "z"}]},
        {"id": "D", "version": 1, "date": "", "status": "current", "supersedes": "C", "passages": [{"id": "D:p1", "text": ""}]},
    ]
    codes = {f.code for f in validate_documents(documents_from_raw(raw, "t"), {"x": "y"})}
    assert {"missing_supersedes_target", "duplicate_passage_id", "invalid_version", "supersedes_cycle",
            "empty_passage", "status_disagrees_with_supersedes", "passage_prefix"} <= codes


def test_questionnaire_validation_flags_duplicates_and_unmapped_topics(tmp_path):
    path = tmp_path / "q.json"
    path.write_text(json.dumps({"questions": [{"id": "Q1", "topic": "exports", "text": "A?"}, {"id": "Q1", "topic": "exports", "text": "B?"},
                                              {"id": "Q2", "topic": "security", "text": "C?"}, {"id": "Q3", "topic": "exports", "text": " "}]}))
    q = load_questionnaire(path, {"exports": "Product reviewer"})
    assert [x.id for x in q.questions] == ["Q1", "Q2"]
    assert {f.code for f in q.findings} == {"duplicate_question_id", "unmapped_topic", "empty_question"}


def test_question_key_is_exact_apart_from_case_and_spacing():
    assert question_key("Can free-plan users  export CSV?") == question_key("can free-plan users export csv?")
    assert question_key("Can users on the free plan export CSV files?") != question_key("Can free-plan users export CSV?")


def test_excerpt_matching():
    text = "CSV exports are available on paid plans only. Free-plan users cannot export CSV."
    assert excerpt_in_passage("Free-plan users cannot export CSV.", text)
    assert excerpt_in_passage("CSV exports are available ... cannot export CSV", text)
    assert not excerpt_in_passage("Free-plan users can export CSV.", text)
    assert not excerpt_in_passage("", text)


def test_check_draft_rejects_fabricated_and_superseded_citations():
    corpus = full_corpus()
    checks, _, _ = check_draft(answered("Yes.", ("EXPORT-v9:p1", "x"), ("EXPORT-v1:p1", "CSV exports are available on every plan.")), corpus)
    failed = {c.name for c in checks if not c.passed}
    assert {"citations_exist", "cites_current_authority"} <= failed


def test_check_draft_shows_replaced_text_for_superseding_source():
    corpus = full_corpus()
    checks, conflicts, shown = check_draft(answered("No.", ("EXPORT-v2:p1", "Free-plan users cannot export CSV."), conflicts=[("EXPORT-v1:p1", "EXPORT-v2:p1")]), corpus)
    assert all(c.passed for c in checks)
    assert conflicts[0]["resolved_by_metadata"]
    assert [s["passage_id"] for s in shown] == ["EXPORT-v1:p1"] and shown[0]["replaced_by"] == "EXPORT-v2"


def test_conflict_between_current_documents_is_not_resolved_by_date():
    corpus = full_corpus()
    assert not classify_conflict(["EXPORT-SCHEDULE-v1:p1", "EXPORT-FAQ-v1:p1"], corpus)["resolved_by_metadata"]
    assert classify_conflict(["EXPORT-v1:p1", "EXPORT-v2:p1"], corpus)["resolved_by_metadata"]


def test_support_checks_route_current_contradictions_to_review():
    corpus = full_corpus()
    verification = {"claims": [{"claim": "daily", "passage_ids": ["EXPORT-FAQ-v1:p1"], "verdict": "supported", "explanation": ""}],
                    "contradicting_passages": [{"passage_id": "EXPORT-SCHEDULE-v1:p1", "explanation": "says weekly"}], "overall": "supported"}
    checks, conflicts, _ = support_checks(verification, corpus, ["EXPORT-FAQ-v1:p1"])
    assert {c.name: c.passed for c in checks} == {"claims_supported": True, "no_unresolved_contradiction": False}
    assert not conflicts[0]["resolved_by_metadata"]

    superseded_only = dict(verification, contradicting_passages=[{"passage_id": "EXPORT-v1:p1", "explanation": "old"}])
    checks, conflicts, shown = support_checks(superseded_only, corpus, ["EXPORT-v2:p1"])
    assert all(c.passed for c in checks) and shown[0]["passage_id"] == "EXPORT-v1:p1"
