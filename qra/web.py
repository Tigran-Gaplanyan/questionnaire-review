"""The review website (Flask). Start it with `python -m qra serve`.

Each function below handles one page or one button. The pages themselves are the HTML files in
qra/templates/. Every action goes through the Workspace (workflow.py), so the website cannot
skip any of the rules.
"""
from __future__ import annotations

from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, url_for
from markupsafe import Markup, escape

from . import config
from .evidence import replaced_text
from .export import export_questionnaire, render_markdown
from .llm import AnthropicJsonModel
from .store import Store
from .workflow import Workspace, WorkflowError

STATUSES = ("answered", "unresolved", "approved", "failed", "pending")
QUESTIONNAIRE_FILES = {"seed": config.STARTER_SEED, "buyer-b": config.QUESTIONNAIRE_B}
SCENARIOS = {"support-v1-version-2": config.SOURCE_CHANGE_SCENARIO}


def highlight(text: str, excerpt: str | None) -> Markup:
    """Show a passage with the quoted part wrapped in <mark>. Everything else is escaped (safe HTML)."""
    for part in (excerpt or "").split("..."):
        part = part.strip(" .")
        start = text.lower().find(part.lower()) if part else -1
        if start >= 0:
            end = start + len(part)
            return escape(text[:start]) + Markup("<mark>") + escape(text[start:end]) + Markup("</mark>") + escape(text[end:])
    return escape(text)


def create_app(db_path: Path, mode: str = config.DEFAULT_MODE, model=None) -> Flask:
    """Create the Flask website, connected to the database at `db_path`.

    `model` lets tests pass a fake model client; normally the real one is created from `mode`.
    """
    app = Flask(__name__)
    app.secret_key = "local-demo-only"  # only used for the short messages shown after an action
    app.jinja_env.filters["highlight"] = highlight
    ws = Workspace(Store(db_path), model or AnthropicJsonModel(mode=mode))  # tests pass a fake model
    if not ws.store.has_corpus():
        ws.import_corpus()

    @app.context_processor
    def shared_values():
        """Values every page can use: the model name, the mode and the list of statuses."""
        return {"model_mode": mode, "model_name": config.MODEL, "statuses": STATUSES}

    def after_action(item_id: int, action, success_message: str):
        """Run a reviewer action, show the result as a message, and go back to the item page."""
        try:
            action()
            flash(success_message)
        except WorkflowError as error:
            flash(f"Rejected: {error}", "error")
        return redirect(url_for("item_detail", item_id=item_id))

    # --- Queue (home page) --------------------------------------------------------------------

    @app.get("/")
    def queue():
        """Home page: the review queue, with counts, filters and the input problems."""
        status = request.args.get("status") or None
        questionnaire = request.args.get("questionnaire") or None
        items = ws.store.items(status=status, questionnaire_id=questionnaire)
        drafts = {i["id"]: ws.store.get("drafts", i["current_draft_id"]) for i in items}
        return render_template(
            "queue.html", items=items, drafts=drafts, summary=ws.summary(), status=status, questionnaire=questionnaire,
            questionnaires=ws.store.query("SELECT * FROM questionnaires ORDER BY rowid"),
            findings=ws.store.findings(), owners=ws.store.owners(), questionnaire_files=QUESTIONNAIRE_FILES,
        )

    @app.post("/questionnaires/run")
    def run_questionnaire():
        """Button: import a questionnaire file and draft answers for its new questions."""
        key = request.form.get("questionnaire")
        if key not in QUESTIONNAIRE_FILES:
            abort(400)
        questionnaire_id, findings = ws.import_questionnaire(QUESTIONNAIRE_FILES[key])
        processed = ws.process_questionnaire(questionnaire_id)
        total = len(ws.store.items(questionnaire_id=questionnaire_id))
        message = (f"Questionnaire '{questionnaire_id}': drafted {len(processed)} new or failed question(s); "
                   f"{total - len(processed)} already had a result and were left as they are. "
                   "To redo one, open it and click 'Request a fresh model draft'.")
        problems = [f.message for f in findings if f.level == "error"]
        if problems:
            message += " Problems: " + "; ".join(problems)
        flash(message)
        return redirect(url_for("queue", questionnaire=questionnaire_id))

    @app.post("/ask")
    def ask():
        """Form: ask a single question, then open its page."""
        try:
            item = ws.ask(request.form.get("text", ""), request.form.get("topic", ""))
        except WorkflowError as error:
            flash(f"Rejected: {error}", "error")
            return redirect(url_for("queue"))
        return redirect(url_for("item_detail", item_id=item["id"]))

    @app.get("/questionnaires/<questionnaire_id>/export.md")
    def export_markdown(questionnaire_id: str):
        """Download a questionnaire's approved answers and open items as Markdown."""
        if not ws.store.items(questionnaire_id=questionnaire_id):
            abort(404)
        body = render_markdown(export_questionnaire(ws, questionnaire_id))
        return body, 200, {"Content-Type": "text/markdown; charset=utf-8",
                           "Content-Disposition": f'attachment; filename="{questionnaire_id}.md"'}

    # --- One question -------------------------------------------------------------------------

    @app.get("/items/<int:item_id>")
    def item_detail(item_id: int):
        """One question's page: answer and checks on the left, evidence and history on the right."""
        item = ws.store.get("items", item_id) or abort(404)
        corpus = ws.corpus()
        draft = ws.store.get("drafts", item["current_draft_id"])
        model_draft = ws.store.latest_model_draft(item_id)
        approval = ws.store.get("approvals", item["approval_id"])

        # Keep the evidence visible even after a reviewer edit or reuse: replaced (old) text comes from
        # whatever the current draft cites, conflicts come from the latest model draft.
        cited = [c["passage_id"] for c in (draft or {}).get("citations") or []]
        replaced = replaced_text(cited, corpus)
        shown = {r["passage_id"] for r in replaced}
        replaced += [r for r in (model_draft or {}).get("replaced_text") or [] if r["passage_id"] not in shown]

        return render_template(
            "item.html", item=item, draft=draft, model_draft=model_draft, approval=approval, corpus=corpus,
            replaced=replaced, conflicts=(model_draft or {}).get("conflicts") or [],
            drafts=ws.store.query("SELECT * FROM drafts WHERE item_id = ? ORDER BY id", item_id),
            events=ws.store.query("SELECT * FROM events WHERE item_id = ? ORDER BY id", item_id),
            calls=ws.store.model_calls_for_item(item_id),
            reused_by=ws.store.items_with_approval(approval["id"]) if approval else [],
        )

    @app.post("/items/<int:item_id>/approve")
    def approve(item_id: int):
        """Button: approve the (possibly corrected) answer with the selected passages."""
        form = request.form
        return after_action(item_id, lambda: ws.approve(item_id, form.get("reviewer", ""), answer=form.get("answer", ""),
                                                        citation_ids=form.getlist("citations"), note=form.get("note", "")),
                            "Approved. This wording will be reused for the same question.")

    @app.post("/items/<int:item_id>/edit")
    def edit(item_id: int):
        """Button: save the reviewer's edit as an unapproved draft."""
        form = request.form
        return after_action(item_id, lambda: ws.save_edit(item_id, form.get("reviewer", ""), form.get("answer", ""),
                                                          form.getlist("citations"), form.get("note", "")),
                            "Saved as an unapproved draft. It will not be reused until approved.")

    @app.post("/items/<int:item_id>/unresolved")
    def unresolved(item_id: int):
        """Button: leave the question unresolved with the reviewer's note."""
        form = request.form
        return after_action(item_id, lambda: ws.mark_unresolved(item_id, form.get("reviewer", ""), form.get("note", "")),
                            "Left unresolved with a note.")

    @app.post("/items/<int:item_id>/redraft")
    def redraft(item_id: int):
        """Button: ask the model for a fresh draft."""
        return after_action(item_id, lambda: ws.redraft(item_id), "Requested a fresh model draft.")

    @app.get("/calls/<int:call_id>")
    def call_detail(call_id: int):
        """One model call: where its answer came from (live, replay, simulated) and the raw output."""
        call = ws.store.get("model_calls", call_id) or abort(404)
        return render_template("call.html", call=call)

    # --- Approved answers and documents ---------------------------------------------------------

    @app.get("/approvals")
    def approvals():
        """Page: every approved answer, its sources, status, and which questions use it."""
        rows = ws.store.approvals()
        usage = {a["id"]: ws.store.items_with_approval(a["id"]) for a in rows}
        return render_template("approvals.html", approvals=rows, usage=usage)

    @app.get("/documents")
    def documents():
        """Page: every document with its authority and earlier revisions."""
        corpus = ws.corpus()
        history = {doc_id: ws.store.document_history(doc_id) for doc_id in corpus.documents}
        return render_template("documents.html", corpus=corpus, history=history, scenarios=SCENARIOS, findings=ws.store.findings())

    @app.post("/documents/<doc_id>/revise")
    def revise(doc_id: str):
        """Form: publish a new version of one document, then re-check approvals."""
        doc = ws.corpus().documents.get(doc_id) or abort(404)
        form = request.form
        try:
            new_doc = {
                "id": doc.id, "version": int(form.get("version", "0")), "date": form.get("date") or doc.date,
                "status": doc.declared_status, "supersedes": doc.supersedes,
                "passages": [{"id": p.id, "text": form.get(f"passage:{p.id}", p.text)} for p in doc.passages],
            }
            _, affected = ws.publish_revision(new_doc, source="web edit", note=form.get("note", ""), actor=form.get("reviewer") or "reviewer")
            flash(f"Published {doc_id} version {new_doc['version']}. Approvals now needing review: "
                  f"{', '.join('#' + str(a['id']) for a in affected) or 'none'}.")
        except (WorkflowError, ValueError) as error:
            flash(f"Rejected: {error}", "error")
        return redirect(url_for("documents"))

    @app.post("/documents/scenario")
    def scenario():
        """Button: apply a prepared document-change scenario (e.g. SUPPORT-v1 version 2)."""
        path = SCENARIOS.get(request.form.get("scenario", "")) or abort(400)
        try:
            affected = ws.apply_scenario(path, actor="reviewer")
            flash(f"Applied {path.name}. Approvals now needing review: {', '.join('#' + str(a['id']) for a in affected) or 'none'}.")
        except WorkflowError as error:
            flash(f"Rejected: {error}", "error")
        return redirect(url_for("documents"))

    return app
