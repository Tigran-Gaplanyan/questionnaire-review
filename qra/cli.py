"""Command line: `python -m qra <command>`. Run `python -m qra -h` for the list of commands."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config
from .llm import AnthropicJsonModel, SimulatedFailures
from .store import Store
from .workflow import Workspace, WorkflowError


def make_workspace(db: Path, mode: str = config.DEFAULT_MODE, simulate: list[str] | None = None) -> Workspace:
    """Create a Workspace (database + model client) for one command.

    If `simulate` is given (e.g. ["Q5=invalid_output"]), those questions fail on purpose (for testing).
    """
    model = AnthropicJsonModel(mode=mode)
    if simulate:  # e.g. ["Q5=invalid_output"]
        model = SimulatedFailures(model, dict(s.split("=", 1) for s in simulate))
    return Workspace(Store(db), model)


def build_parser() -> argparse.ArgumentParser:
    """Describe every command and its options for argparse (this is what `python -m qra -h` prints)."""
    parser = argparse.ArgumentParser(prog="python -m qra", description="Questionnaire evidence and review workspace")
    parser.add_argument("--db", type=Path, default=config.DEFAULT_DB, help="SQLite file (default: var/qra.sqlite)")
    commands = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_text: str, model: bool = False) -> argparse.ArgumentParser:
        """Register one sub-command; commands that call the model also get a --mode option."""
        command = commands.add_parser(name, help=help_text)
        if model:
            command.add_argument("--mode", choices=config.MODES, default=config.DEFAULT_MODE,
                                 help="replay (default, no API key), live, or auto")
        return command

    c = add("init", "Create the database and import the documents")
    c.add_argument("--reset", action="store_true", help="Delete the existing database first")

    c = add("run", "Import a questionnaire file and draft answers", model=True)
    c.add_argument("file", type=Path)
    c.add_argument("--reprocess", action="store_true", help="Redo questions that already have a result")
    c.add_argument("--simulate", action="append", metavar="QID=KIND",
                   help=f"Make one question fail on purpose; KIND is one of {SimulatedFailures.KINDS}")

    c = add("ask", "Ask one question (for example, ask a question again)", model=True)
    c.add_argument("text")
    c.add_argument("--topic", required=True)

    c = add("redraft", "Ask the model for a fresh draft of one question", model=True)
    c.add_argument("item", help="e.g. seed/Q1, or a numeric id")

    c = add("approve", "Approve a question's current draft (optionally with corrected wording)")
    c.add_argument("item")
    c.add_argument("--by", required=True)
    c.add_argument("--answer")
    c.add_argument("--cite", action="append", help="Supporting passage id (repeatable); default: the draft's citations")
    c.add_argument("--note", default="")

    c = add("edit", "Save an unapproved reviewer edit")
    c.add_argument("item")
    c.add_argument("--by", required=True)
    c.add_argument("--answer", required=True)

    c = add("unresolve", "Leave a question unresolved with a note")
    c.add_argument("item")
    c.add_argument("--by", required=True)
    c.add_argument("--note", required=True)

    c = add("publish", "Publish document changes from a scenario file and re-check approvals")
    c.add_argument("file", type=Path)

    c = add("export", "Export a questionnaire: approved answers with evidence, plus open items")
    c.add_argument("questionnaire")
    c.add_argument("--out", type=Path, default=config.ROOT / "var" / "exports")

    add("status", "Print counts and every question")
    add("export-state", "Print all questions and approvals as JSON (used to check state after a restart)")
    add("demo", "Fill the database with the demonstration steps", model=True)

    c = add("serve", "Start the review website", model=True)
    c.add_argument("--port", type=int, default=5050)

    c = add("evaluate", "Run the reference cases in a throwaway database and write evaluation/results.*", model=True)
    c.add_argument("--out", type=Path, default=config.ROOT / "evaluation")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point: parse the command line and run the command. Returns the process exit code."""
    args = build_parser().parse_args(argv)
    try:
        return run_command(args)
    except WorkflowError as error:
        print(f"Rejected: {error}", file=sys.stderr)
        return 2


def run_command(args) -> int:
    """Run the chosen command. Returns 0 on success (the exit code)."""
    if args.command == "init":
        if args.reset and args.db.exists():
            args.db.unlink()
        ws = make_workspace(args.db)
        findings = ws.import_corpus()
        corpus = ws.corpus()
        print(f"Database: {args.db}")
        print(f"Corpus: {len(corpus.documents)} documents, {len(corpus.passages)} passages; owners: {corpus.owners}")
        print_findings(findings) if findings else print("  no validation findings")
        return 1 if any(f.level == "error" for f in findings) else 0

    if args.command == "evaluate":
        from .evaluate import run_evaluation
        return run_evaluation(mode=args.mode, out_dir=args.out)

    if args.command == "serve":
        from .web import create_app
        app = create_app(args.db, mode=args.mode)
        print(f"Review workspace on http://127.0.0.1:{args.port}  (model mode: {args.mode})")
        # threaded=False: the app has one database connection, so it handles one request at a time.
        app.run(host="127.0.0.1", port=args.port, debug=False, threaded=False)
        return 0

    ws = make_workspace(args.db, getattr(args, "mode", config.DEFAULT_MODE), getattr(args, "simulate", None))
    if not ws.store.has_corpus():
        print_findings(ws.import_corpus())

    if args.command == "run":
        questionnaire_id, findings = ws.import_questionnaire(args.file)
        print(f"Questionnaire {questionnaire_id}:")
        print_findings(findings)
        ws.process_questionnaire(questionnaire_id, reprocess=args.reprocess)
        print_items(ws, questionnaire_id)
    elif args.command == "ask":
        item = ws.ask(args.text, args.topic)
        print_items(ws, item["questionnaire_id"])
    elif args.command == "redraft":
        ws.redraft(find_item(ws, args.item))
        print_items(ws)
    elif args.command == "approve":
        item = ws.approve(find_item(ws, args.item), args.by, answer=args.answer, citation_ids=args.cite, note=args.note)
        print(f"Approved item #{item['id']} as approval #{item['approval_id']}")
    elif args.command == "edit":
        ws.save_edit(find_item(ws, args.item), args.by, args.answer)
        print("Saved as an unapproved draft.")
    elif args.command == "unresolve":
        ws.mark_unresolved(find_item(ws, args.item), args.by, args.note)
        print("Left unresolved.")
    elif args.command == "publish":
        affected = ws.apply_scenario(args.file)
        print(f"Published. Approvals now needing review: {[a['id'] for a in affected] or 'none'}")
        for a in affected:
            print(f"  approval #{a['id']} '{a['question_text']}': {a['status_reason']}")
    elif args.command == "export":
        from .export import write_export
        for path in write_export(ws, args.questionnaire, args.out):
            print(f"Wrote {path}")
    elif args.command == "status":
        print(json.dumps(ws.summary(), indent=2))
        print_items(ws)
    elif args.command == "export-state":
        state = {"items": ws.store.items(), "approvals": ws.store.approvals(), "summary": ws.summary()}
        print(json.dumps(state, indent=2, ensure_ascii=False))
    elif args.command == "demo":
        from .evaluate import build_demo
        build_demo(ws)
        print_items(ws)
        print(json.dumps(ws.summary(), indent=2))
    return 0


def find_item(ws: Workspace, ref: str) -> int:
    """Accept either a numeric id or "<questionnaire>/<question id>", e.g. seed/Q1."""
    if ref.isdigit():
        return int(ref)
    questionnaire_id, _, question_id = ref.partition("/")
    item = ws.store.item_by_ref(questionnaire_id, question_id)
    if item is None:
        raise SystemExit(f"No item {ref!r}. Use <questionnaire>/<question id>, e.g. seed/Q1, or a numeric id.")
    return item["id"]


def print_findings(findings) -> None:
    """Print problems found in the input files, one per line."""
    for f in findings:
        print(f"  [{f.level}] {f.code}: {f.message}")


def print_items(ws: Workspace, questionnaire_id: str | None = None) -> None:
    """Print every question with its status, owner, answer and the reasons it needs review."""
    for item in ws.store.items(questionnaire_id=questionnaire_id):
        draft = ws.store.get("drafts", item["current_draft_id"]) or {}
        reused = " (reused)" if item["reused"] else ""
        print(f"  #{item['id']:<3} {item['questionnaire_id']}/{item['question_id']:<5} {item['status']:<10}{reused:<9} "
              f"owner={item['owner'] or '-':<17} {item['question_text']}")
        if draft.get("answer"):
            print(f"         answer: {draft['answer']}")
        for r in item["reasons"]:
            print(f"         reason [{r['code']}]: {r['text']}")
