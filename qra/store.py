"""Saving and loading data with SQLite (one file, var/qra.sqlite by default).

Each kind of record has its own table. Model drafts, reviewer edits and approved answers are in
separate tables on purpose, so an unapproved edit can never be mistaken for an approved answer.
Lists and dicts are saved as JSON text and turned back into Python objects when read.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .corpus import Corpus, Document, Finding, Passage

SCHEMA = """
CREATE TABLE IF NOT EXISTS owners (               -- topic -> reviewer mapping from seed.json
    topic TEXT PRIMARY KEY,
    owner TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS document_revisions (   -- every version of every document is kept
    doc_id TEXT NOT NULL,
    revision INTEGER NOT NULL,                    -- 1, 2, 3 ... per document; the highest is current
    version INTEGER NOT NULL,
    date TEXT,
    declared_status TEXT,
    supersedes TEXT,
    passages TEXT NOT NULL,                       -- JSON list of {id, text}
    source TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (doc_id, revision)
);
CREATE TABLE IF NOT EXISTS validation_findings (  -- problems found in the input files
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    level TEXT NOT NULL,
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    ref TEXT
);
CREATE TABLE IF NOT EXISTS questionnaires (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_path TEXT
);
CREATE TABLE IF NOT EXISTS items (                -- one row per question in a questionnaire
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    questionnaire_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    question_text TEXT NOT NULL,
    status TEXT NOT NULL,                         -- pending | answered | unresolved | approved | failed
    owner TEXT,                                   -- suggested reviewer from the topic mapping
    reasons TEXT NOT NULL DEFAULT '[]',           -- JSON list of {code, text}: why it needs review
    current_draft_id INTEGER,
    approval_id INTEGER,                          -- approval made here, reused here, or now stale
    reused INTEGER NOT NULL DEFAULT 0,            -- 1 if the answer came from an earlier approval
    reviewer_note TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (questionnaire_id, question_id)
);
CREATE TABLE IF NOT EXISTS drafts (               -- model suggestions and reviewer edits
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    kind TEXT NOT NULL,                           -- model | reviewer_edit | reused_approval
    proposed_status TEXT,
    answer TEXT,
    citations TEXT NOT NULL DEFAULT '[]',
    conflicts TEXT NOT NULL DEFAULT '[]',
    replaced_text TEXT NOT NULL DEFAULT '[]',     -- superseded passages shown to the reviewer
    unresolved_reason TEXT,
    checks TEXT NOT NULL DEFAULT '[]',
    support TEXT,                                 -- the second model pass's verdict
    corpus_versions TEXT NOT NULL DEFAULT '{}',   -- document versions the draft was written against
    model_call_ids TEXT NOT NULL DEFAULT '[]',
    author TEXT,
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_calls (          -- every model call, with where its answer came from
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER,
    purpose TEXT NOT NULL,                        -- draft_answer | verify_answer
    provenance TEXT NOT NULL,                     -- live | replay | simulated | test-fake
    ok INTEGER NOT NULL,
    error_type TEXT,
    error_message TEXT,
    model_requested TEXT,
    model_served TEXT,
    prompt_name TEXT,
    request_hash TEXT,
    replay_path TEXT,
    response_id TEXT,
    request_id TEXT,
    stop_reason TEXT,
    raw_text TEXT,
    parsed TEXT,
    usage TEXT,
    recorded_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (            -- the only answers that may be reused
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_key TEXT NOT NULL,                   -- normalised question text (exact-match reuse)
    question_text TEXT NOT NULL,
    topic TEXT,
    answer TEXT NOT NULL,
    sources TEXT NOT NULL,                        -- JSON: passage, document, version, revision, text hash
    approved_by TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    from_item_id INTEGER,
    from_draft_id INTEGER,
    model_answer TEXT,                            -- the model's suggestion, for comparison
    status TEXT NOT NULL,                         -- active | needs_review | superseded
    status_reason TEXT,
    note TEXT
);
CREATE TABLE IF NOT EXISTS events (               -- history log for the item page
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER,
    approval_id INTEGER,
    type TEXT NOT NULL,
    actor TEXT,
    detail TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
"""

# Columns that hold lists or dicts. They are saved as JSON text.
JSON_COLUMNS = {"passages", "reasons", "citations", "conflicts", "replaced_text", "checks", "support",
                "corpus_versions", "model_call_ids", "parsed", "usage", "sources", "detail"}


def now() -> str:
    """The current time in UTC, as text (e.g. "2026-10-05T10:00:00+00:00")."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_db(column: str, value):
    """Prepare a value for the database: lists and dicts become JSON text, True/False become 1/0."""
    if column in JSON_COLUMNS and value is not None:
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return int(value)
    return value


def _from_db(row: sqlite3.Row | None) -> dict | None:
    """Turn a database row into a dict, turning JSON text columns back into lists and dicts."""
    if row is None:
        return None
    record = dict(row)
    for column in JSON_COLUMNS & record.keys():
        if record[column] is not None:
            record[column] = json.loads(record[column])
    return record


class Store:
    """All reading and writing of the database goes through this class."""
    def __init__(self, path: Path | str):
        """Open (or create) the database file and make sure all tables exist."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False lets the web app use this connection; it serves one request at a time.
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        """Close the database connection."""
        self.conn.close()

    # --- Generic helpers used everywhere -------------------------------------------------------

    def insert(self, table: str, **values) -> int:
        """Insert one row and return its id."""
        columns = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        params = [_to_db(column, value) for column, value in values.items()]
        cursor = self.conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", params)
        self.conn.commit()
        return cursor.lastrowid

    def update(self, table: str, row_id: int, **values) -> None:
        """Change some columns of one row."""
        assignments = ", ".join(f"{column} = ?" for column in values)
        params = [_to_db(column, value) for column, value in values.items()] + [row_id]
        self.conn.execute(f"UPDATE {table} SET {assignments} WHERE id = ?", params)
        self.conn.commit()

    def get(self, table: str, row_id: int | None) -> dict | None:
        """Read one row by id (None if it does not exist)."""
        if row_id is None:
            return None
        return self.query_one(f"SELECT * FROM {table} WHERE id = ?", row_id)

    def query(self, sql: str, *params) -> list[dict]:
        """Run a SELECT query and return all rows as dicts. Use ? in the SQL for each parameter."""
        return [_from_db(row) for row in self.conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, *params) -> dict | None:
        """Run a SELECT query and return the first row as a dict (or None)."""
        return _from_db(self.conn.execute(sql, params).fetchone())

    def log(self, event_type: str, item_id: int | None = None, approval_id: int | None = None, actor: str | None = None, **detail) -> None:
        """Add a line to the history shown on the item page."""
        self.insert("events", item_id=item_id, approval_id=approval_id, type=event_type, actor=actor, detail=detail, created_at=now())

    # --- Questions (items) ----------------------------------------------------------------------

    def items(self, status: str | None = None, questionnaire_id: str | None = None) -> list[dict]:
        """All questions, optionally only those with a given status and/or from one questionnaire."""
        sql, params = "SELECT * FROM items WHERE 1 = 1", []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if questionnaire_id:
            sql += " AND questionnaire_id = ?"
            params.append(questionnaire_id)
        return self.query(sql + " ORDER BY id", *params)

    def item_by_ref(self, questionnaire_id: str, question_id: str) -> dict | None:
        """Find one question by questionnaire id and question id (e.g. "seed", "Q1")."""
        return self.query_one("SELECT * FROM items WHERE questionnaire_id = ? AND question_id = ?", questionnaire_id, question_id)

    def items_with_approval(self, approval_id: int) -> list[dict]:
        """Questions that were approved with, reused, or refer to this approval."""
        return self.query("SELECT * FROM items WHERE approval_id = ? ORDER BY id", approval_id)

    def latest_model_draft(self, item_id: int) -> dict | None:
        """The newest draft the model wrote for this question (not reviewer edits)."""
        return self.query_one("SELECT * FROM drafts WHERE item_id = ? AND kind = 'model' ORDER BY id DESC LIMIT 1", item_id)

    def model_calls_for_item(self, item_id: int) -> list[dict]:
        """Every model call made for this question, oldest first."""
        return self.query("SELECT * FROM model_calls WHERE item_id = ? ORDER BY id", item_id)

    # --- Approvals ------------------------------------------------------------------------------

    def approvals(self, status: str | None = None) -> list[dict]:
        """All approvals, optionally only those with one status (active, needs_review, superseded)."""
        if status:
            return self.query("SELECT * FROM approvals WHERE status = ? ORDER BY id", status)
        return self.query("SELECT * FROM approvals ORDER BY id")

    def latest_approval_for_key(self, key: str) -> dict | None:
        """The newest approval for this exact question that has not been replaced."""
        return self.query_one(
            "SELECT * FROM approvals WHERE question_key = ? AND status IN ('active', 'needs_review') ORDER BY id DESC LIMIT 1", key)

    # --- Documents ------------------------------------------------------------------------------

    def has_corpus(self) -> bool:
        """True if the documents have already been imported."""
        return self.query_one("SELECT COUNT(*) AS n FROM document_revisions")["n"] > 0

    def add_revision(self, doc: Document, source: str, note: str = "") -> int:
        """Save a new revision of a document. The highest revision number is the current one."""
        last = self.query_one("SELECT MAX(revision) AS r FROM document_revisions WHERE doc_id = ?", doc.id)["r"] or 0
        revision = last + 1
        self.conn.execute(
            "INSERT INTO document_revisions (doc_id, revision, version, date, declared_status, supersedes, passages, source, note, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (doc.id, revision, doc.version, doc.date, doc.declared_status, doc.supersedes,
             json.dumps([{"id": p.id, "text": p.text} for p in doc.passages]), source, note, now()))
        self.conn.commit()
        return revision

    def current_documents(self) -> list[Document]:
        """The newest revision of every document."""
        rows = self.query(
            "SELECT r.* FROM document_revisions r"
            " JOIN (SELECT doc_id, MAX(revision) AS latest FROM document_revisions GROUP BY doc_id) m"
            " ON r.doc_id = m.doc_id AND r.revision = m.latest ORDER BY r.doc_id")
        return [_document(row) for row in rows]

    def document_history(self, doc_id: str) -> list[Document]:
        """Every revision of one document, newest first (old text is kept for reviewers)."""
        rows = self.query("SELECT * FROM document_revisions WHERE doc_id = ? ORDER BY revision DESC", doc_id)
        return [_document(row) for row in rows]

    def corpus(self) -> Corpus:
        """The current documents and the topic mapping, as a Corpus (see corpus.py)."""
        return Corpus(self.current_documents(), self.owners())

    def save_owners(self, owners: dict[str, str]) -> None:
        """Save the topic -> reviewer mapping."""
        self.conn.executemany("INSERT OR REPLACE INTO owners (topic, owner) VALUES (?, ?)", owners.items())
        self.conn.commit()

    def owners(self) -> dict[str, str]:
        """The topic -> reviewer mapping, e.g. {"exports": "Product reviewer"}."""
        return {row["topic"]: row["owner"] for row in self.query("SELECT * FROM owners")}

    # --- Input problems -------------------------------------------------------------------------

    def save_findings(self, scope: str, findings: list[Finding]) -> None:
        """Replace the stored findings for one input (the corpus or one questionnaire)."""
        self.conn.execute("DELETE FROM validation_findings WHERE scope = ?", (scope,))
        self.conn.commit()
        for f in findings:
            self.insert("validation_findings", scope=scope, level=f.level, code=f.code, message=f.message, ref=f.ref)

    def findings(self) -> list[dict]:
        """All saved input problems, errors first."""
        return self.query("SELECT * FROM validation_findings ORDER BY level, scope, id")


def _document(row: dict) -> Document:
    """Turn a document_revisions row back into a Document object."""
    return Document(
        id=row["doc_id"], version=row["version"], date=row["date"], declared_status=row["declared_status"],
        supersedes=row["supersedes"], revision=row["revision"], source=row["source"],
        passages=tuple(Passage(id=p["id"], doc_id=row["doc_id"], text=p["text"]) for p in row["passages"]),
    )
