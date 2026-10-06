"""Optional enhancement: export a questionnaire with answers, evidence references and open items.

Only approved answers are exported as answers. Drafts awaiting approval and unresolved questions
are listed separately with their owner and reason, so an export can never pass off an unreviewed
draft as the company's answer.
"""
from __future__ import annotations

import json
from pathlib import Path

from .workflow import Workspace


def export_questionnaire(ws: Workspace, questionnaire_id: str) -> dict:
    """Collect a questionnaire's approved answers (with evidence) and its open items.

    Only active approvals count as answers; everything else is listed as an open item, so an
    unreviewed draft is never exported as the company's answer.
    """
    rows = []
    for item in ws.store.items(questionnaire_id=questionnaire_id):
        approval = ws.store.get("approvals", item["approval_id"])
        draft = ws.store.get("drafts", item["current_draft_id"]) or {}
        row = {"question_id": item["question_id"], "question": item["question_text"], "topic": item["topic"], "status": item["status"]}
        if item["status"] == "approved" and approval and approval["status"] == "active":
            row.update({
                "answer": approval["answer"],
                "evidence": [{"passage_id": s["passage_id"], "document": s["doc_id"], "version": s["version"]} for s in approval["sources"]],
                "approved_by": approval["approved_by"], "approved_at": approval["approved_at"],
                "approval_id": approval["id"], "reused": bool(item["reused"]),
            })
        else:
            row.update({
                "answer": None,
                "owner": item["owner"],
                "open_reasons": [r["text"] for r in item["reasons"]] or ["Draft awaiting reviewer approval."],
                "unapproved_draft": draft.get("answer") or None,
            })
        rows.append(row)
    return {
        "questionnaire_id": questionnaire_id,
        "answered_and_approved": sum(1 for r in rows if r["answer"] is not None),
        "open": sum(1 for r in rows if r["answer"] is None),
        "questions": rows,
    }


def _cell(value) -> str:
    """Make a value safe to put in a Markdown table cell."""
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(export: dict) -> str:
    """Turn an export into a Markdown document with two tables: answers and open items."""
    lines = [f"# Questionnaire {export['questionnaire_id']}", "",
             f"{export['answered_and_approved']} approved answers, {export['open']} open items.", "",
             "| ID | Question | Approved answer | Evidence |", "|---|---|---|---|"]
    for r in export["questions"]:
        if r["answer"] is not None:
            evidence = ", ".join(f"{e['passage_id']} (v{e['version']})" for e in r["evidence"])
            lines.append(f"| {r['question_id']} | {_cell(r['question'])} | {_cell(r['answer'])} | {evidence} |")
    open_rows = [r for r in export["questions"] if r["answer"] is None]
    if open_rows:
        lines += ["", "## Open items (not answers)", "", "| ID | Question | Status | Owner | Why |", "|---|---|---|---|---|"]
        for r in open_rows:
            lines.append(f"| {r['question_id']} | {_cell(r['question'])} | {r['status']} | {r['owner'] or '—'} | {_cell('; '.join(r['open_reasons']))} |")
    return "\n".join(lines) + "\n"


def write_export(ws: Workspace, questionnaire_id: str, out_dir: Path) -> tuple[Path, Path]:
    """Write the export as <questionnaire>.json and <questionnaire>.md. Returns both paths."""
    export = export_questionnaire(ws, questionnaire_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{questionnaire_id}.json"
    md_path = out_dir / f"{questionnaire_id}.md"
    json_path.write_text(json.dumps(export, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(render_markdown(export), encoding="utf-8")
    return json_path, md_path
