"""Run the reference cases and compare the app's results with the answer key.

Expected values come ONLY from reference/reference-cases.json (written before the app existed).
Observed values come from the app's saved state. Quotes are checked against the original data
files, not the app's copy, so this check does not depend on the code it is checking.

Order of the run (all in a throwaway database):
  RC1-RC3  process the seed questionnaire and check Q3, Q2, Q1
  RC4      reviewer approves/edits, then questionnaire B repeats questions -> reuse or not
  RC5      read the state from a new process (restart), change a document, ask again
  AC1-AC3  extra checks on questionnaire B
  AC4-AC6  simulated failures, in a separate database
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .corpus import read_json
from .evidence import excerpt_in_passage
from .llm import AnthropicJsonModel, SimulatedFailures
from .store import Store
from .workflow import Workspace, WorkflowError


class Report:
    """The results of the checks for one case."""

    def __init__(self, case_id: str, description: str, simulated: bool = False):
        """Start an empty report for one case."""
        self.id = case_id
        self.description = description
        self.simulated = simulated
        self.checks: list[dict] = []
        self.observed: list[dict] = []

    def check(self, what: str, expected, observed, passed: bool) -> None:
        """Record one comparison of an expected value with what the app actually did."""
        self.checks.append({"what": what, "expected": expected, "observed": observed, "passed": bool(passed)})

    @property
    def passed(self) -> bool:
        """The case passes only if it has checks and every one of them passed."""
        return bool(self.checks) and all(c["passed"] for c in self.checks)

    def as_dict(self) -> dict:
        """The report as a dict, for results.json."""
        return {"id": self.id, "brief_check": self.description, "simulated": self.simulated, "passed": self.passed,
                "expectations": self.checks, "observed_items": self.observed}


# --- Reading the app's results -----------------------------------------------------------------

def observe(ws: Workspace, item: dict | None) -> dict:
    """Everything we want to know about one question's result, in one dict."""
    if item is None:
        return {"missing": True}
    draft = ws.store.get("drafts", item["current_draft_id"]) or {}
    approval = ws.store.get("approvals", item["approval_id"])
    citations = draft.get("citations") or []
    return {
        "ref": f"{item['questionnaire_id']}/{item['question_id']}",
        "question": item["question_text"],
        "status": item["status"],
        "owner": item["owner"],
        "reused": bool(item["reused"]),
        "answer": draft.get("answer") or "",
        "citations": citations,
        "cited": [c["passage_id"] for c in citations],
        "conflict_passages": sorted({p for c in draft.get("conflicts") or [] for p in c["passage_ids"]}),
        "replaced_text": [r["passage_id"] for r in draft.get("replaced_text") or []],
        "support": (draft.get("support") or {}).get("overall"),
        "reasons": item["reasons"],
        "approval": None if approval is None else {
            k: approval[k] for k in ("id", "status", "approved_by", "approved_at", "answer", "sources", "status_reason")},
        "model_calls": [{"purpose": c["purpose"], "provenance": c["provenance"], "ok": bool(c["ok"]),
                         "model_served": c["model_served"], "replay_path": c["replay_path"], "error_type": c["error_type"]}
                        for c in ws.store.model_calls_for_item(item["id"])],
    }


def _item(ws: Workspace, ref: str) -> dict | None:
    """Find a question by "<questionnaire>/<question id>", e.g. "seed/Q1"."""
    questionnaire_id, question_id = ref.split("/")
    return ws.store.item_by_ref(questionnaire_id, question_id)


def _first_word(text: str) -> str:
    """The first word of an answer in lower case (used to check "yes"/"no")."""
    words = text.strip().lower().lstrip("\"'").split(" ")
    return words[0].strip(".,;:!-") if text.strip() else ""


def _mentions_any(text: str, words: list[str]) -> bool:
    """True if the text contains at least one of the words (ignoring capitals)."""
    return any(word.lower() in text.lower() for word in words)


def check_answer(report: Report, obs: dict, expected: dict, passages: dict[str, str]) -> None:
    """Compare one question's result with the expected values. Only keys present in `expected` are checked."""
    e = expected
    if "status" in e:
        report.check("status", e["status"], obs["status"], obs["status"] == e["status"])
    if "owner" in e:
        report.check("suggested owner", e["owner"], obs["owner"], obs["owner"] == e["owner"])
    if "reused" in e:
        report.check("reused approved answer", e["reused"], obs["reused"], obs["reused"] == e["reused"])
    if "must_cite" in e:
        report.check("cites required passages", e["must_cite"], obs["cited"], set(e["must_cite"]) <= set(obs["cited"]))
    if "may_cite_only" in e:
        report.check("cites nothing else", e["may_cite_only"], obs["cited"], set(obs["cited"]) <= set(e["may_cite_only"]))
    if "must_not_cite" in e:
        report.check("does not cite as support", e["must_not_cite"], obs["cited"], not set(e["must_not_cite"]) & set(obs["cited"]))
    if "answer_must_start_with" in e:
        report.check("answer polarity", f"starts with '{e['answer_must_start_with']}'", obs["answer"],
                     _first_word(obs["answer"]) == e["answer_must_start_with"])
    if "answer_must_not_start_with" in e:
        report.check("no yes/no capability claim", f"not starting with {e['answer_must_not_start_with']}", obs["answer"] or "(empty)",
                     _first_word(obs["answer"]) not in e["answer_must_not_start_with"])
    if "answer_must_mention_any" in e:
        report.check("answer mentions", e["answer_must_mention_any"], obs["answer"], _mentions_any(obs["answer"], e["answer_must_mention_any"]))
    if "answer_must_mention_each_group" in e:
        missing = [group for group in e["answer_must_mention_each_group"] if not _mentions_any(obs["answer"], group)]
        report.check("answer mentions key facts", e["answer_must_mention_each_group"], obs["answer"], not missing)
    if e.get("excerpts_verbatim"):
        bad = [c["passage_id"] for c in obs["citations"]
               if c["passage_id"] not in passages or not excerpt_in_passage(c["excerpt"], passages[c["passage_id"]])]
        report.check("excerpts appear in the source files", "all", bad or "all", obs["citations"] and not bad)
    if "model_support_check" in e:
        report.check("independent support check (2nd model pass)", e["model_support_check"], obs["support"], obs["support"] == e["model_support_check"])
    if "reason_must_mention_any" in e:
        text = " ".join(r["text"] for r in obs["reasons"])
        report.check("reason explains why", e["reason_must_mention_any"], text, _mentions_any(text, e["reason_must_mention_any"]))
    if e.get("not_approved"):
        report.check("not approved", True, obs["status"] != "approved", obs["status"] != "approved")
    if "replaced_text_shown" in e:
        report.check("replaced text visible to reviewer", e["replaced_text_shown"], obs["replaced_text"], set(e["replaced_text_shown"]) <= set(obs["replaced_text"]))
    if "conflict_passages_include" in e:
        report.check("conflicting passages listed", e["conflict_passages_include"], obs["conflict_passages"],
                     set(e["conflict_passages_include"]) <= set(obs["conflict_passages"]))


# --- Scripted reviewer actions (also used by `python -m qra demo`) ------------------------------

def reviewer_actions(ws: Workspace, ref: dict, report: Report | None = None) -> None:
    """Approve a corrected Q1, approve Q3 as drafted, and save an UNAPPROVED edit of Q4."""
    actions = ref["reviewer_actions"]
    q1, q3, q4 = _item(ws, "seed/Q1"), _item(ws, "seed/Q3"), _item(ws, "seed/Q4")
    a1, a3, e4 = actions["approve_q1_corrected"], actions["approve_q3_as_drafted"], actions["unapproved_edit_q4"]
    steps = [
        ("approve corrected Q1", lambda: ws.approve(q1["id"], a1["approved_by"], answer=a1["answer"], citation_ids=a1["citations"])),
        ("approve Q3 as drafted", lambda: ws.approve(q3["id"], a3["approved_by"])),
        ("save unapproved Q4 edit", lambda: ws.save_edit(q4["id"], e4["author"], e4["answer"])),
    ]
    for what, action in steps:
        try:
            action()
            if report:
                report.check(what, "accepted", "accepted", True)
        except WorkflowError as error:
            if report:
                report.check(what, "accepted", f"rejected: {error}", False)
            else:
                print(f"  ! {what} rejected: {error}")


def build_demo(ws: Workspace) -> None:
    """Fill a workspace with the same steps as the evaluation (stopping before the source change)."""
    ref = read_json(config.REFERENCE_CASES)
    ws.import_corpus()
    seed_id, _ = ws.import_questionnaire(config.STARTER_SEED)
    ws.process_questionnaire(seed_id)
    reviewer_actions(ws, ref)
    b_id, _ = ws.import_questionnaire(config.QUESTIONNAIRE_B)
    ws.process_questionnaire(b_id)


# --- The cases --------------------------------------------------------------------------------

def check_seed_cases(ws: Workspace, cases: dict, passages: dict) -> list[Report]:
    """RC1 (supported), RC2 (unsupported), RC3 (superseded conflict)."""
    reports = []
    for case_id in ("RC1-supported", "RC2-unsupported", "RC3-superseded-conflict"):
        spec = cases[case_id]
        report = Report(case_id, spec["brief_check"])
        obs = observe(ws, _item(ws, "seed/" + spec["input"]["question_id"]))
        report.observed.append(obs)
        check_answer(report, obs, spec["expected"], passages)
        reports.append(report)
    return reports


def check_reuse(ws: Workspace, ref: dict, spec: dict) -> Report:
    """RC4: an approved answer is reused for an exact repeat; an unapproved edit is not."""
    report = Report(spec["id"], spec["brief_check"])
    reviewer_actions(ws, ref, report)
    b_id, _ = ws.import_questionnaire(config.QUESTIONNAIRE_B)
    ws.process_questionnaire(b_id)

    qb1 = observe(ws, _item(ws, "buyer-b/QB1"))
    qb3 = observe(ws, _item(ws, "buyer-b/QB3"))
    report.observed += [qb1, qb3]

    expected = spec["expected"]["QB1"]
    approved_wording = ref["reviewer_actions"]["approve_q1_corrected"]["answer"]
    approval = qb1["approval"] or {}
    sources = [{"passage_id": s["passage_id"], "doc_id": s["doc_id"], "version": s["version"]} for s in approval.get("sources") or []]
    report.check("QB1 status", expected["status"], qb1["status"], qb1["status"] == expected["status"])
    report.check("QB1 reused", True, qb1["reused"], qb1["reused"])
    report.check("QB1 shows approved wording", approved_wording, qb1["answer"], qb1["answer"] == approved_wording)
    report.check("approval record fields", expected["approval_record_has"],
                 {k: approval.get(k) for k in expected["approval_record_has"] if k != "sources"},
                 all(approval.get(k) for k in expected["approval_record_has"]))
    report.check("approval sources with document version", expected["sources"], sources, sources == expected["sources"])
    report.check("no model call for reused answer", 0, len(qb1["model_calls"]), len(qb1["model_calls"]) == expected["model_calls_for_item"])

    expected = spec["expected"]["QB3"]
    unapproved_edit = ref["reviewer_actions"]["unapproved_edit_q4"]["answer"]
    report.check("QB3 not approved", f"status != {expected['status_not']}", qb3["status"], qb3["status"] != expected["status_not"])
    report.check("QB3 not reused", False, qb3["reused"], not qb3["reused"])
    report.check("QB3 does not show the unapproved edit", f"!= {unapproved_edit!r}", qb3["answer"], qb3["answer"] != unapproved_edit)
    return report


def check_restart_and_source_change(ws: Workspace, db: Path, model, ref: dict, spec: dict) -> tuple[Workspace, Report]:
    """RC5: state survives a restart; changing a source sends its approval back to review."""
    report = Report(spec["id"], spec["brief_check"])

    # Restart: close the database and read it from a completely separate process.
    ws.store.close()
    process = subprocess.run([sys.executable, "-m", "qra", "--db", str(db), "export-state"],
                             capture_output=True, text=True, timeout=60, cwd=config.ROOT)
    report.check("state readable by a new process", "exit 0", process.returncode, process.returncode == 0)
    state = json.loads(process.stdout) if process.returncode == 0 else {"items": [], "approvals": []}
    active = {a["question_text"]: a for a in state["approvals"] if a["status"] == "active"}
    items = {f"{i['questionnaire_id']}/{i['question_id']}": i for i in state["items"]}

    expected = spec["expected"]["after_restart"]
    for question in expected["approvals_active_for_questions"]:
        report.check(f"after restart: approval active for '{question}'", "active", (active.get(question) or {}).get("status"), question in active)
    for ref_id, status in expected["item_status"].items():
        observed = (items.get(ref_id) or {}).get("status")
        report.check(f"after restart: {ref_id} status", status, observed, observed == status)
    for question, passage_ids in expected["approval_sources"].items():
        observed = [s["passage_id"] for s in (active.get(question) or {}).get("sources", [])]
        report.check(f"after restart: sources kept for '{question}'", passage_ids, observed, observed == passage_ids)

    # Change a source document: SUPPORT-v1 goes from version 1 to 2.
    ws = Workspace(Store(db), model)
    ws.apply_scenario(config.SOURCE_CHANGE_SCENARIO)
    latest = {a["question_text"]: a for a in ws.store.approvals()}  # later approvals overwrite earlier ones

    expected = spec["expected"]["after_source_change"]
    for question, status in expected["approval_status"].items():
        observed = (latest.get(question) or {}).get("status")
        report.check(f"after change: approval '{question}'", status, observed, observed == status)
    why = (latest.get("When is email support available?") or {}).get("status_reason") or ""
    report.check("change reason names the source", expected["approval_reason_must_mention"], why,
                 all(word in why for word in expected["approval_reason_must_mention"]))
    for ref_id, status in expected["item_status"].items():
        observed = (_item(ws, ref_id) or {}).get("status")
        report.check(f"after change: {ref_id} status", status, observed, observed == status)

    # Ask the same question again: the stale approval must not be reused.
    repeat = ref["reviewer_actions"]["repeat_question_after_source_change"]
    obs = observe(ws, ws.ask(repeat["text"], repeat["topic"]))
    report.observed.append(obs)
    expected = spec["expected"]["repeat_question"]
    reasons = " ".join(r["text"] for r in obs["reasons"])
    report.check("asked again: status", expected["status"], obs["status"], obs["status"] == expected["status"])
    report.check("asked again: not reused", False, obs["reused"], not obs["reused"])
    report.check("asked again: owner", expected["owner"], obs["owner"], obs["owner"] == expected["owner"])
    report.check("asked again: reason", expected["reason_must_mention_any"], reasons, _mentions_any(reasons, expected["reason_must_mention_any"]))
    return ws, report


def check_additional(ws: Workspace, extra: dict, passages: dict) -> list[Report]:
    """AC1-AC3 on questionnaire B: unresolved conflict, SSO not inferred as 'no', reworded question."""
    reports = []
    for case_id in ("AC1-unresolved-conflict-no-supersedes", "AC2-undocumented-not-negative", "AC3-reworded-question-not-reused"):
        spec = extra[case_id]
        report = Report(case_id, spec["rule"])
        obs = observe(ws, _item(ws, "buyer-b/" + spec["input"]["question_id"]))
        report.observed.append(obs)
        check_answer(report, obs, spec["expected"], passages)
        reports.append(report)
    return reports


def check_simulated_failures(model, extra: dict, workdir: Path) -> list[Report]:
    """AC4-AC6: injected failures are visible and do not stop the batch (separate database)."""
    simulated = {case_id: spec for case_id, spec in extra.items() if spec.get("simulated")}
    failures = {spec["input"]["question_id"]: spec["input"]["simulate"] for spec in simulated.values()}
    ws = Workspace(Store(workdir / "simulated.sqlite"), SimulatedFailures(model, failures))
    ws.import_corpus()
    seed_id, _ = ws.import_questionnaire(config.STARTER_SEED)
    ws.process_questionnaire(seed_id)
    others = [i for i in ws.store.items(questionnaire_id=seed_id) if i["question_id"] not in failures]

    reports = []
    for case_id, spec in simulated.items():
        report = Report(case_id, "Simulated failure (labelled SIMULATED; not a real model response)", simulated=True)
        obs = observe(ws, _item(ws, "seed/" + spec["input"]["question_id"]))
        report.observed.append(obs)
        expected = spec["expected"]
        draft_call = next((c for c in obs["model_calls"] if c["purpose"] == "draft_answer"), {})
        report.check("status", expected["status"], obs["status"], obs["status"] == expected["status"])
        if "error_type" in expected:
            report.check("error type", expected["error_type"], draft_call.get("error_type"), draft_call.get("error_type") == expected["error_type"])
        if "provenance" in expected:
            report.check("labelled as simulated", expected["provenance"], draft_call.get("provenance"), draft_call.get("provenance") == expected["provenance"])
        if expected.get("other_items_processed"):
            done = [i for i in others if i["status"] != "pending"]
            report.check("batch continued", f"{len(others)} other questions processed", f"{len(done)} processed", len(done) == len(others))
        if "failed_checks_include" in expected:
            reasons = " ".join(r["text"] for r in obs["reasons"])
            report.check("failed checks", expected["failed_checks_include"], reasons, all(c in reasons for c in expected["failed_checks_include"]))
        reports.append(report)
    ws.store.close()
    return reports


# --- Running everything -----------------------------------------------------------------------

def run_evaluation(mode: str, out_dir: Path, model=None) -> int:
    """Run all reference cases in a throwaway database and write evaluation/results.*.

    Returns 0 if all five reference cases pass, otherwise 1 (the command's exit code).
    """
    ref = read_json(config.REFERENCE_CASES)
    cases = {c["id"]: c for c in ref["cases"]}
    extra = {c["id"]: c for c in ref["additional_checks"]}
    docs = read_json(config.STARTER_SEED)["documents"] + read_json(config.ADDITION_DOCUMENTS)["documents"]
    passages = {p["id"]: p["text"] for d in docs for p in d["passages"]}  # straight from the data files

    workdir = Path(tempfile.mkdtemp(prefix="qra-eval-"))
    db = workdir / "eval.sqlite"
    model = model or AnthropicJsonModel(mode=mode)
    ws = Workspace(Store(db), model)
    print(f"Evaluating in {db} (model mode: {mode})")

    ws.import_corpus()
    seed_id, _ = ws.import_questionnaire(config.STARTER_SEED)
    ws.process_questionnaire(seed_id)

    reports = check_seed_cases(ws, cases, passages)
    reports.append(check_reuse(ws, ref, cases["RC4-approved-reuse"]))
    ws, rc5 = check_restart_and_source_change(ws, db, model, ref, cases["RC5-reload-and-source-change"])
    reports.append(rc5)
    reports += check_additional(ws, extra, passages)
    ws.store.close()
    reports += check_simulated_failures(model, extra, workdir)

    summary = write_results(reports, mode, out_dir)
    for r in reports:
        print(f"  {'PASS' if r.passed else 'FAIL'}  {r.id}" + ("  [simulated]" if r.simulated else ""))
        for c in r.checks:
            if not c["passed"]:
                print(f"        - {c['what']}: expected {c['expected']!r}, observed {c['observed']!r}")
    print(f"Reference cases: {summary['reference_cases_passed']}/{summary['reference_cases_total']}; "
          f"additional: {summary['additional_checks_passed']}/{summary['additional_checks_total']}")
    print(f"Wrote {out_dir / 'results.md'}")
    all_reference_passed = summary["reference_cases_passed"] == summary["reference_cases_total"]
    return 0 if all_reference_passed else 1


def write_results(reports: list[Report], mode: str, out_dir: Path) -> dict:
    """Write results.json and results.md and return the summary."""
    reference = [r for r in reports if r.id.startswith("RC")]
    additional = [r for r in reports if r.id.startswith("AC")]
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model_mode": mode,
        "model": config.MODEL,
        "reference_cases_passed": sum(r.passed for r in reference),
        "reference_cases_total": len(reference),
        "additional_checks_passed": sum(r.passed for r in additional),
        "additional_checks_total": len(additional),
        "cases": [r.as_dict() for r in reports],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "results.md").write_text(render_markdown(summary), encoding="utf-8")
    return summary


def _cell(value) -> str:
    """Make a value safe to put in a Markdown table cell."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(summary: dict) -> str:
    """Turn the results into the human-readable results.md report."""
    lines = [
        "# Evaluation results",
        "",
        f"Generated {summary['generated_at']} by `python -m qra evaluate --mode {summary['model_mode']}` (model `{summary['model']}`).",
        "Expected values come from `reference/reference-cases.json`; observed values come from the application's saved state.",
        "",
        f"**Reference cases: {summary['reference_cases_passed']}/{summary['reference_cases_total']} passed. "
        f"Additional checks: {summary['additional_checks_passed']}/{summary['additional_checks_total']} passed.**",
        "",
        "Model-call provenance per item is listed under each case: `live` = new API call, `replay` = saved real response, "
        "`simulated` = injected failure (not a model response). Five reference cases are a small demonstration and do not "
        "establish accuracy on other questions or documents.",
        "",
        "| Case | Result | Brief check |",
        "|---|---|---|",
    ]
    for case in summary["cases"]:
        result = "PASS" if case["passed"] else "**FAIL**"
        lines.append(f"| {case['id']}{' (simulated)' if case['simulated'] else ''} | {result} | {_cell(case['brief_check'])} |")

    for case in summary["cases"]:
        lines += ["", f"## {case['id']} — {'PASS' if case['passed'] else 'FAIL'}", "",
                  "| Check | Expected | Observed | |", "|---|---|---|---|"]
        for c in case["expectations"]:
            lines.append(f"| {_cell(c['what'])} | {_cell(c['expected'])} | {_cell(c['observed'])} | {'✅' if c['passed'] else '❌'} |")
        for obs in case["observed_items"]:
            if obs.get("missing"):
                continue
            calls = ", ".join(f"{m['purpose']}={m['provenance']}" + ("" if m["ok"] else f" ({m['error_type']})")
                              for m in obs["model_calls"]) or "none (reused)"
            lines += ["",
                      f"- **{obs['ref']}** “{obs['question']}” → `{obs['status']}`" + (" (reused approval)" if obs["reused"] else ""),
                      f"  - answer: {obs['answer'] or '(none)'}",
                      f"  - cited: {', '.join(obs['cited']) or '(none)'}; replaced text shown: {', '.join(obs['replaced_text']) or '(none)'}",
                      f"  - model calls: {calls}"]
            for r in obs["reasons"]:
                lines.append(f"  - reason [{r['code']}]: {r['text']}")
    return "\n".join(lines) + "\n"
