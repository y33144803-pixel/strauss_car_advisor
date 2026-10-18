"""SQLite: שמירת מצב השיחות והשאלות לנציג, כדי שהתהליך ישרוד רענון ויעבור בין מסך העובד למסך הנציג."""
import hashlib
import json
import os
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

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
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL,                    -- employee / rep
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                origin_username TEXT                   -- השם שהוגדר ב-AUTH_USERS (נשמר גם אחרי החלפת שם משתמש)
            )""")
        user_columns = [row[1] for row in conn.execute("PRAGMA table_info(users)")]
        if "origin_username" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN origin_username TEXT")
            conn.execute("UPDATE users SET origin_username = username WHERE origin_username IS NULL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS auth_tokens (
                token_hash TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )""")
        columns = [row[1] for row in conn.execute("PRAGMA table_info(sessions)")]
        if "username" not in columns:                  # שיחות ישנות (לפני ההתחברות) נשארות בלי בעלים
            conn.execute("ALTER TABLE sessions ADD COLUMN username TEXT")
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


# --- משתמשים, התחברות והיסטוריית שיחות ---

def get_user(username: str) -> dict | None:
    init_db()
    with closing(_connect()) as conn:
        row = conn.execute("SELECT username, display_name, role, password_hash FROM users WHERE username = ?",
                           (username,)).fetchone()
    return dict(row) if row else None


def create_user_if_missing(username: str, display_name: str, role: str, password_hash: str) -> bool:
    """יוצר משתמש אם עוד לא קיים (משתמש קיים לא נדרס, כך ששינוי סיסמה נשמר). מחזיר True אם נוצר.
    משתמש ששינה את שם המשתמש שלו נחשב קיים: השם המקורי מ-AUTH_USERS לא נוצר מחדש."""
    init_db()
    with closing(_connect()) as conn, conn:
        exists = conn.execute("SELECT 1 FROM users WHERE username = ? OR origin_username = ?",
                              (username, username)).fetchone()
        if exists:
            return False
        conn.execute("""INSERT INTO users (username, display_name, role, password_hash, created_at, origin_username)
                        VALUES (?, ?, ?, ?, ?, ?)""", (username, display_name, role, password_hash, _now(), username))
    return True


def rename_user(old: str, new: str) -> bool:
    """מחליף שם משתמש ומעביר איתו את השיחות והאסימונים. False אם השם החדש כבר תפוס."""
    init_db()
    with closing(_connect()) as conn, conn:
        if conn.execute("SELECT 1 FROM users WHERE username = ? COLLATE NOCASE", (new,)).fetchone()                 and old.casefold() != new.casefold():
            return False
        conn.execute("UPDATE users SET username = ? WHERE username = ?", (new, old))
        conn.execute("UPDATE auth_tokens SET username = ? WHERE username = ?", (new, old))
        conn.execute("UPDATE sessions SET username = ? WHERE username = ?", (new, old))
    return True


def set_password_hash(username: str, password_hash: str) -> None:
    init_db()
    with closing(_connect()) as conn, conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE username = ?", (password_hash, username))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_token(username: str, hours: int = 12) -> str:
    """אסימון התחברות אקראי (נשמר רק כגיבוב) - כדי שרענון הדף לא ינתק את המשתמש."""
    init_db()
    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(hours=hours)).isoformat(timespec="seconds")
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM auth_tokens WHERE expires_at < ?", (_now(),))
        conn.execute("INSERT INTO auth_tokens (token_hash, username, expires_at) VALUES (?, ?, ?)",
                     (_token_hash(token), username, expires))
    return token


def user_for_token(token: str) -> dict | None:
    if not token:
        return None
    init_db()
    with closing(_connect()) as conn:
        row = conn.execute("""SELECT u.username, u.display_name, u.role FROM auth_tokens t
                              JOIN users u ON u.username = t.username
                              WHERE t.token_hash = ? AND t.expires_at >= ?""",
                           (_token_hash(token), _now())).fetchone()
    return dict(row) if row else None


def delete_token(token: str) -> None:
    init_db()
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM auth_tokens WHERE token_hash = ?", (_token_hash(token),))


def session_owner(session_id: str) -> str | None:
    init_db()
    with closing(_connect()) as conn:
        row = conn.execute("SELECT username FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    return row["username"] if row else None


def claim_session(session_id: str, username: str) -> bool:
    """משייך שיחה למשתמש (ויוצר אותה אם אינה קיימת). נכשל אם היא כבר שייכת למשתמש אחר או חסרת בעלים."""
    init_db()
    if not session_exists(session_id):
        save_state(ConversationState(session_id=session_id))
        with closing(_connect()) as conn, conn:
            conn.execute("UPDATE sessions SET username = ? WHERE session_id = ?", (username, session_id))
        return True
    return session_owner(session_id) == username


def list_user_sessions(username: str) -> list[dict]:
    """היסטוריית השיחות של משתמש, מהאחרונה לראשונה. שיחה בלי הודעות של העובד (רק פתיחה) לא נכללת."""
    init_db()
    with closing(_connect()) as conn:
        rows = conn.execute("""SELECT session_id, messages_json, created_at, updated_at FROM sessions
                               WHERE username = ? ORDER BY updated_at DESC""", (username,)).fetchall()
    sessions = []
    for row in rows:
        messages = json.loads(row["messages_json"])
        first_user = next((m["content"] for m in messages if m["role"] == "user"), None)
        if first_user is None:
            continue
        sessions.append({"session_id": row["session_id"], "created_at": row["created_at"],
                         "updated_at": row["updated_at"], "preview": first_user[:80],
                         "messages": sum(1 for m in messages if m["role"] == "user")})
    return sessions


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
