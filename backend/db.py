"""SQLite: שמירת מצב השיחות והשאלות לנציג, כדי שהתהליך ישרוד רענון ויעבור בין מסך העובד למסך הנציג."""
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime

from state import ConversationState

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "storage", "advisor.db")


def db_path() -> str:
    return os.environ.get("STRAUSS_DB_PATH", DEFAULT_DB_PATH)


def _connect() -> sqlite3.Connection:
    path = db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def init_db() -> None:
    with closing(_connect()) as conn, conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                state_json TEXT NOT NULL,
                messages_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rep_questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                missing_field TEXT NOT NULL,
                context_message TEXT NOT NULL,
                employee_summary TEXT,
                status TEXT NOT NULL DEFAULT 'open',   -- open / answered / applied / cancelled
                answer_value REAL,
                answer_note TEXT,
                effective_from TEXT,
                created_at TEXT NOT NULL,
                answered_at TEXT
            )""")


# --- שיחות ---

def load_state(session_id: str) -> ConversationState:
    """טוען מצב שיחה; אם לא קיים - יוצר מצב ריק."""
    init_db()
    with closing(_connect()) as conn:
        row = conn.execute("SELECT state_json FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    if row is None:
        return ConversationState(session_id=session_id)
    return ConversationState.model_validate_json(row["state_json"])


def save_state(state: ConversationState) -> None:
    init_db()
    with closing(_connect()) as conn, conn:
        conn.execute("""
            INSERT INTO sessions (session_id, state_json, created_at, updated_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET state_json = excluded.state_json, updated_at = excluded.updated_at
        """, (state.session_id, state.model_dump_json(), _now(), _now()))


def load_messages(session_id: str) -> list[dict]:
    init_db()
    with closing(_connect()) as conn:
        row = conn.execute("SELECT messages_json FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    return json.loads(row["messages_json"]) if row else []


def save_messages(session_id: str, messages: list[dict]) -> None:
    init_db()
    if not session_exists(session_id):
        save_state(ConversationState(session_id=session_id))
    with closing(_connect()) as conn, conn:
        conn.execute("UPDATE sessions SET messages_json = ?, updated_at = ? WHERE session_id = ?",
                     (json.dumps(messages, ensure_ascii=False), _now(), session_id))


def session_exists(session_id: str) -> bool:
    init_db()
    with closing(_connect()) as conn:
        return conn.execute("SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)).fetchone() is not None


# --- שאלות לנציג ---

def create_question(session_id: str, missing_field: str, context_message: str, employee_summary: str) -> int:
    init_db()
    with closing(_connect()) as conn, conn:
        cur = conn.execute("""
            INSERT INTO rep_questions (session_id, missing_field, context_message, employee_summary, created_at)
            VALUES (?, ?, ?, ?, ?)""", (session_id, missing_field, context_message, employee_summary, _now()))
        return cur.lastrowid


def find_open_question(session_id: str, missing_field: str) -> dict | None:
    init_db()
    with closing(_connect()) as conn:
        row = conn.execute("""SELECT * FROM rep_questions
                              WHERE session_id = ? AND missing_field = ? AND status = 'open'""",
                           (session_id, missing_field)).fetchone()
    return dict(row) if row else None


def list_questions(status: str | None = "open") -> list[dict]:
    """לשימוש מסך הנציג. status=None מחזיר את כל השאלות."""
    init_db()
    with closing(_connect()) as conn:
        if status is None:
            rows = conn.execute("SELECT * FROM rep_questions ORDER BY id DESC").fetchall()
        else:
            rows = conn.execute("SELECT * FROM rep_questions WHERE status = ? ORDER BY id",
                                (status,)).fetchall()
    return [dict(r) for r in rows]


def answer_question(question_id: int, answer_value: float | None, effective_from: str | None = None,
                    answer_note: str | None = None) -> None:
    """הנציג עונה. השיחה של העובד תקלוט את התשובה בטעינה הבאה שלה."""
    init_db()
    with closing(_connect()) as conn, conn:
        conn.execute("""UPDATE rep_questions SET status = 'answered', answer_value = ?, effective_from = ?,
                        answer_note = ?, answered_at = ? WHERE id = ? AND status = 'open'""",
                     (answer_value, effective_from, answer_note, _now(), question_id))


def answered_unapplied(session_id: str) -> list[dict]:
    init_db()
    with closing(_connect()) as conn:
        rows = conn.execute("""SELECT * FROM rep_questions WHERE session_id = ? AND status = 'answered'
                               ORDER BY id""", (session_id,)).fetchall()
    return [dict(r) for r in rows]


def mark_applied(question_id: int) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute("UPDATE rep_questions SET status = 'applied' WHERE id = ?", (question_id,))


def cancel_question(question_id: int) -> None:
    """שאלה שכבר לא רלוונטית (העובד החליף חלופה או מסר את הנתון בעצמו)."""
    init_db()
    with closing(_connect()) as conn, conn:
        conn.execute("UPDATE rep_questions SET status = 'cancelled' WHERE id = ? AND status = 'open'",
                     (question_id,))


def has_open_questions(session_id: str) -> bool:
    init_db()
    with closing(_connect()) as conn:
        return conn.execute("SELECT 1 FROM rep_questions WHERE session_id = ? AND status = 'open'",
                            (session_id,)).fetchone() is not None
