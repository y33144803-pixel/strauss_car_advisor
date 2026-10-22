"""מסד הנתונים: שמירת מצב השיחות והשאלות לנציג, כדי שהתהליך ישרוד רענון ויעבור בין מסך העובד למסך הנציג.

ברירת המחדל: SQLite בקובץ (STRAUSS_DB_PATH). כשמוגדר DATABASE_URL (למשל Postgres ב-Render) משתמשים ב-Postgres.
שאר הקוד כותב שאילתות בסגנון SQLite (סימני ? ושורות בגישה לפי שם), ושכבת התאמה קטנה (_PgConnection) מתרגמת אותן."""
import re
import hashlib
import json
import os
import queue
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

from pydantic import BaseModel

from state import ConversationState

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "storage", "advisor.db")


def db_path() -> str:
    return os.environ.get("STRAUSS_DB_PATH", DEFAULT_DB_PATH)


def database_url() -> str | None:
    """כתובת Postgres (DATABASE_URL), או None כשעובדים עם SQLite. ספקים מסוימים נותנים 'postgres://' - מתקנים ל-postgresql://."""
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url or None


def using_postgres() -> bool:
    return database_url() is not None


_POOL: dict[str, queue.SimpleQueue] = {}          # חיבורי Postgres פנויים לשימוש חוזר (פתיחת חיבור לכל שאילתה איטית)


class _PgCursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self.rowcount = cursor.rowcount

    def fetchone(self):
        return self._cursor.fetchone() if self._cursor.description else None

    def fetchall(self):
        return self._cursor.fetchall() if self._cursor.description else []

    def __iter__(self):
        return iter(self.fetchall())


class _PgConnection:
    """עוטף חיבור psycopg כך שיתנהג כמו חיבור sqlite3 בשימוש שלנו: execute עם ?, שורות לפי שם, with = commit/rollback."""

    def __init__(self, raw, url: str):
        self._raw, self._url = raw, url

    isolation_level = None                         # (קיים ב-sqlite3; כאן אין משמעות)

    def execute(self, sql: str, params=()):
        command = sql.strip().upper()
        if command in ("BEGIN", "BEGIN IMMEDIATE"):     # ב-Postgres העסקה נפתחת לבד; הנעילה נעשית ב-FOR UPDATE
            return _PgCursor(self._raw.execute("SELECT 1"))
        if command == "COMMIT":
            self._raw.commit()
            return None
        if command == "ROLLBACK":
            self._raw.rollback()
            return None
        return _PgCursor(self._raw.execute(sql.replace("?", "%s"), params))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._raw.commit()
        else:
            self._raw.rollback()
        return False

    def close(self) -> None:
        """מחזיר את החיבור למאגר (אחרי ביטול עסקה פתוחה), או סוגר אותו אם הוא שבור או שהמאגר מלא."""
        try:
            if self._raw.closed:
                return
            self._raw.rollback()
            pool = _POOL.setdefault(self._url, queue.SimpleQueue())
            if pool.qsize() < 5:
                pool.put(self._raw)
                return
            self._raw.close()
        except Exception:
            pass


def _connect_postgres(url: str) -> _PgConnection:
    import psycopg
    from psycopg.rows import dict_row
    pool = _POOL.setdefault(url, queue.SimpleQueue())
    while True:
        try:
            raw = pool.get_nowait()
        except queue.Empty:
            raw = psycopg.connect(url, row_factory=dict_row, connect_timeout=15)
            break
        try:
            raw.execute("SELECT 1")                 # החיבור במאגר עלול להיות סגור (למשל המסד נרדם) - מנסים את הבא
            raw.rollback()
            break
        except Exception:
            try:
                raw.close()
            except Exception:
                pass
    return _PgConnection(raw, url)


def _connect():
    url = database_url()
    if url:
        return _connect_postgres(url)
    path = db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)          # ממתין לנעילה עד 30 שניות (ברירת המחדל 5 נכשלת בעומס)
    conn.row_factory = sqlite3.Row
    return conn


def _lock_suffix() -> str:
    """נעילת השורה בקריאה שלפני כתיבה (ב-SQLite הנעילה נעשית ב-BEGIN IMMEDIATE)."""
    return " FOR UPDATE" if using_postgres() else ""


def _columns(conn, table: str) -> list[str]:
    if using_postgres():
        rows = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() "
                            "AND table_name = ?", (table,)).fetchall()
        return [row["column_name"] for row in rows]
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


_READY: set[str] = set()                           # מסדים שכבר אותחלו בתהליך הזה (init_db רץ פעם אחת לכל מסד)


def init_db() -> None:
    key = database_url() or db_path()
    if key in _READY and (using_postgres() or os.path.exists(key)):
        return
    _init_schema()
    _READY.add(key)


def _init_schema() -> None:
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
                role TEXT NOT NULL,                    -- employee / rep / admin
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                origin_username TEXT                   -- השם שהוגדר ב-AUTH_USERS (נשמר גם אחרי החלפת שם משתמש)
            )""")
        user_columns = _columns(conn, "users")
        if "consent_at" not in user_columns:          # אישור שמירת השיחות (נדרש פעם אחת מכל עובד)
            conn.execute("ALTER TABLE users ADD COLUMN consent_at TEXT")
        if "origin_username" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN origin_username TEXT")
            conn.execute("UPDATE users SET origin_username = username WHERE origin_username IS NULL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS auth_tokens (
                token_hash TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )""")
        columns = _columns(conn, "sessions")
        if "username" not in columns:                  # שיחות ישנות (לפני ההתחברות) נשארות בלי בעלים
            conn.execute("ALTER TABLE sessions ADD COLUMN username TEXT")
        if "first_user" not in columns:                # תקציר לרשימות: ההודעה הראשונה של העובד ומספר הודעותיו
            conn.execute("ALTER TABLE sessions ADD COLUMN first_user TEXT")
            conn.execute("ALTER TABLE sessions ADD COLUMN user_messages INTEGER")
            for row in conn.execute("SELECT session_id, messages_json FROM sessions").fetchall():
                first_user, count = _summary(json.loads(row["messages_json"]))
                conn.execute("UPDATE sessions SET first_user = ?, user_messages = ? WHERE session_id = ?",
                             (first_user, count, row["session_id"]))
        if "topic" not in columns:                     # נושא השיחה לפי מצב השיחה (שם מקצועי בהיסטוריה)
            conn.execute("ALTER TABLE sessions ADD COLUMN topic TEXT")
            for row in conn.execute("SELECT session_id, state_json FROM sessions").fetchall():
                try:
                    topic = _topic(ConversationState.model_validate_json(row["state_json"]))
                except ValueError:
                    topic = None
                conn.execute("UPDATE sessions SET topic = ? WHERE session_id = ?", (topic, row["session_id"]))
        if "topic_v" not in columns:                   # ניסוח הנושא השתנה: מחשבים מחדש לשיחות קיימות
            conn.execute("ALTER TABLE sessions ADD COLUMN topic_v INTEGER")
            for row in conn.execute("SELECT session_id, state_json FROM sessions").fetchall():
                try:
                    topic = _topic(ConversationState.model_validate_json(row["state_json"]))
                except ValueError:
                    topic = None
                conn.execute("UPDATE sessions SET topic = ?, topic_v = ? WHERE session_id = ?",
                             (topic, TOPIC_VERSION, row["session_id"]))
        if "admin_viewed_at" not in columns:           # מתי מנהל צפה בשיחה לאחרונה, ומי
            conn.execute("ALTER TABLE sessions ADD COLUMN admin_viewed_at TEXT")
            conn.execute("ALTER TABLE sessions ADD COLUMN admin_viewed_by TEXT")
        if "title" not in columns:                     # שם שהמשתמש נתן לשיחה (אחרת מוצגת תחילת ההודעה הראשונה)
            conn.execute("ALTER TABLE sessions ADD COLUMN title TEXT")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rep_questions (
                id %s,
                session_id TEXT NOT NULL,
                missing_field TEXT NOT NULL,
                context_message TEXT NOT NULL,
                employee_summary TEXT,
                status TEXT NOT NULL DEFAULT 'open',   -- open / answered / applied / cancelled
                answer_value DOUBLE PRECISION,
                answer_note TEXT,
                effective_from TEXT,
                created_at TEXT NOT NULL,
                answered_at TEXT
            )""" % ("SERIAL PRIMARY KEY" if using_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"))
        rep_columns = _columns(conn, "rep_questions")
        if "answered_by" not in rep_columns:           # שם המשתמש של הנציג שענה
            conn.execute("ALTER TABLE rep_questions ADD COLUMN answered_by TEXT")


# --- שיחות ---

def load_state(session_id: str) -> ConversationState:
    """טוען מצב שיחה; אם לא קיים - יוצר מצב ריק."""
    init_db()
    with closing(_connect()) as conn:
        row = conn.execute("SELECT state_json FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    if row is None:
        return ConversationState(session_id=session_id)
    state = ConversationState.model_validate_json(row["state_json"])
    state._base = state.model_dump()                 # לעומת זה ייבדק מה השתנה בשמירה
    return state


TOPIC_VERSION = 2       # גרסת ניסוח הנושא: העלאה מחשבת מחדש את הנושאים של שיחות קיימות


def _topic(state: ConversationState) -> str | None:
    """נושא ממוקד לשיחה: מה בודקים (רכב נוכחי -> חלופה, או ויתור על רכב), ואם עוד לא נבחרה חלופה - הקבוצה."""
    target, current, emp = state.target, state.current, state.employee
    short = lambda label: re.sub(r"\s*\(\d{4}\)\s*$", "", label or "")       # בלי שנת הרישום: שם קצר וקריא בסרגל
    if target.kind == "waiver":
        return f"ויתור על רכב · {emp.occupation}" if emp.occupation else "ויתור על רכב"
    if target.kind == "car" and target.label:
        if current.kind == "car" and current.label:
            return f"מ-{short(current.label)} ל-{short(target.label)}"
        return f"מעבר ל-{short(target.label)}"
    if emp.eligibility_group:
        return f"בדיקת נטו · קבוצה {emp.eligibility_group}"
    return None


def display_title(custom: str | None, topic: str | None, created_at: str) -> str:
    """שם השיחה בהיסטוריה: שם שהמשתמש נתן, ואחרת הנושא, ואם עוד אין נושא - "בדיקת נטו". התאריך מוצג בנפרד (relative_day)."""
    custom = (custom or "").strip()
    if custom:
        return custom
    return topic or "בדיקת נטו"


def relative_day(timestamp: str, now: datetime | None = None) -> str:
    """תאריך יחסי קצר להיסטוריית השיחות: "היום", "אתמול", "לפני 3 ימים", "לפני שבוע", ואחרי כחודש - תאריך."""
    moment = datetime.fromisoformat(timestamp)
    days = ((now or datetime.now()).date() - moment.date()).days
    if days <= 0:
        return "היום"
    if days == 1:
        return "אתמול"
    if days < 7:
        return f"לפני {days} ימים"
    if days < 14:
        return "לפני שבוע"
    if days < 30:
        return f"לפני {days // 7} שבועות"
    return f"{moment:%d/%m/%Y}"


def _changes(base, new, path=()) -> tuple[list[tuple], list[tuple]]:
    """(שדות ששונו או נוספו, שדות שנמחקו) בין שני עצי מילונים. רשימות וערכים בודדים מושווים כיחידה אחת."""
    changed, removed = [], []
    for key, value in new.items():
        if key not in base:
            changed.append((path + (key,), value))
        elif isinstance(value, dict) and isinstance(base[key], dict):
            sub_changed, sub_removed = _changes(base[key], value, path + (key,))
            changed += sub_changed
            removed += sub_removed
        elif value != base[key]:
            changed.append((path + (key,), value))
    removed += [path + (key,) for key in base if key not in new]
    return changed, removed


def _merge_changes(current: dict, changed, removed) -> None:
    for keys, value in changed:
        node = current
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        node[keys[-1]] = value
    for keys in removed:
        node = current
        for key in keys[:-1]:
            node = node.get(key) or {}
        node.pop(keys[-1], None)


def _assign(dst, src) -> None:
    """מעתיק שדות מ-src ל-dst במקום (גם אובייקטים מקוננים), כדי שהפניות שהקורא מחזיק ימשיכו להיות תקפות."""
    for name in type(src).model_fields:
        value = getattr(src, name)
        if isinstance(value, BaseModel):
            _assign(getattr(dst, name), value)
        else:
            setattr(dst, name, value)


def save_state(state: ConversationState) -> None:
    """שומר את המצב. אם הוא נטען קודם מהמסד - נכתבים רק השדות ששונו מאז הטעינה, על גבי המצב העדכני במסד
    (בתוך נעילה), כך ששמירה מקבילה של תהליך אחר (למשל תשובת נציג) לא נדרסת."""
    init_db()
    if state._base is not None:
        with closing(_connect()) as conn:
            conn.isolation_level = None                          # עסקה ידנית
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT state_json FROM sessions WHERE session_id = ?" + _lock_suffix(),
                                   (state.session_id,)).fetchone()
                if row is not None:
                    current = json.loads(row["state_json"])
                    changed, removed = _changes(state._base, state.model_dump())
                    _merge_changes(current, changed, removed)
                    merged = ConversationState.model_validate(current)
                    conn.execute("""UPDATE sessions SET state_json = ?, updated_at = ?, topic = ?, topic_v = ?
                                    WHERE session_id = ?""",
                                 (merged.model_dump_json(), _now(), _topic(merged), TOPIC_VERSION, state.session_id))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        if row is not None:
            _assign(state, merged)                               # האובייקט של הקורא מתעדכן (במקום) למצב המשולב
            state._base = merged.model_dump()
            return
    with closing(_connect()) as conn, conn:
        conn.execute("""
            INSERT INTO sessions (session_id, state_json, created_at, updated_at, topic, topic_v) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET state_json = excluded.state_json, updated_at = excluded.updated_at,
                                                  topic = excluded.topic, topic_v = excluded.topic_v
        """, (state.session_id, state.model_dump_json(), _now(), _now(), _topic(state), TOPIC_VERSION))


def load_messages(session_id: str) -> list[dict]:
    init_db()
    with closing(_connect()) as conn:
        row = conn.execute("SELECT messages_json FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    return json.loads(row["messages_json"]) if row else []


def since_reset(messages: list[dict]) -> list[dict]:
    """ההודעות מאיפוס הנתונים האחרון (כולל הודעת האיפוס) - מה שלפני כן כבר לא נחשב: לא נשלח למודל ולא משמש ראיה."""
    start = next((i for i in range(len(messages) - 1, -1, -1) if messages[i].get("reset")), 0)
    return messages[start:]


def _summary(messages: list[dict]) -> tuple[str | None, int]:
    """(ההודעה הראשונה של העובד, כמה הודעות כתב) - נשמרים בעמודות כדי שרשימות השיחות לא יפענחו את כל ה-JSON."""
    users = [m["content"] for m in messages if m["role"] == "user" and not m.get("hidden")]
    return (users[0] if users else None), len(users)


def save_messages(session_id: str, messages: list[dict]) -> None:
    init_db()
    if not session_exists(session_id):
        save_state(ConversationState(session_id=session_id))
    first_user, count = _summary(messages)
    with closing(_connect()) as conn, conn:
        conn.execute("""UPDATE sessions SET messages_json = ?, updated_at = ?, first_user = ?, user_messages = ?
                        WHERE session_id = ?""",
                     (json.dumps(messages, ensure_ascii=False), _now(), first_user, count, session_id))


def append_messages(session_id: str, new_messages: list[dict]) -> list[dict]:
    """מוסיף הודעות לסוף השיחה בתוך עסקה אחת (קריאה וכתיבה יחד). כך הודעה שנוספה במקביל - למשל תשובת נציג
    שנקלטה באמצע תור ארוך - לא נדרסת. מחזיר את רשימת ההודעות המלאה."""
    init_db()
    if not session_exists(session_id):
        save_state(ConversationState(session_id=session_id))
    with closing(_connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        with conn:
            row = conn.execute("SELECT messages_json FROM sessions WHERE session_id = ?" + _lock_suffix(),
                               (session_id,)).fetchone()
            messages = (json.loads(row["messages_json"]) if row else []) + list(new_messages)
            first_user, count = _summary(messages)
            conn.execute("""UPDATE sessions SET messages_json = ?, updated_at = ?, first_user = ?, user_messages = ?
                            WHERE session_id = ?""",
                         (json.dumps(messages, ensure_ascii=False), _now(), first_user, count, session_id))
    return messages


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
        taken = conn.execute("SELECT 1 FROM users WHERE LOWER(username) = LOWER(?)", (new,)).fetchone()
        if taken and old.casefold() != new.casefold():
            return False
        conn.execute("UPDATE users SET username = ? WHERE username = ?", (new, old))
        conn.execute("UPDATE auth_tokens SET username = ? WHERE username = ?", (new, old))
        conn.execute("UPDATE sessions SET username = ? WHERE username = ?", (new, old))
        conn.execute("UPDATE rep_questions SET answered_by = ? WHERE answered_by = ?", (new, old))
        conn.execute("UPDATE sessions SET admin_viewed_by = ? WHERE admin_viewed_by = ?", (new, old))
    return True


def has_consented(username: str) -> bool:
    init_db()
    with closing(_connect()) as conn:
        row = conn.execute("SELECT consent_at FROM users WHERE username = ?", (username,)).fetchone()
    return bool(row and row["consent_at"])


def give_consent(username: str) -> None:
    init_db()
    with closing(_connect()) as conn, conn:
        conn.execute("UPDATE users SET consent_at = ? WHERE username = ? AND consent_at IS NULL", (_now(), username))


def list_users() -> list[dict]:
    """כל המשתמשים (בלי הגיבוב), לפי תפקיד ושם - למסך הניהול."""
    init_db()
    with closing(_connect()) as conn:
        rows = conn.execute("""SELECT u.username, u.display_name, u.role, u.created_at,
                                      (SELECT COUNT(*) FROM sessions s WHERE s.username = u.username) AS sessions
                               FROM users u ORDER BY u.role, u.username""").fetchall()
    return [dict(row) for row in rows]


def import_users(users: list[dict]) -> int:
    """טוען משתמשים מגיבוי (כולל הגיבוב של הסיסמה). משתמש שכבר קיים לא נדרס. מחזיר כמה נוספו."""
    init_db()
    added = 0
    with closing(_connect()) as conn, conn:
        for u in users:
            if conn.execute("SELECT 1 FROM users WHERE username = ? OR origin_username = ?",
                            (u["username"], u.get("origin_username") or u["username"])).fetchone():
                continue
            conn.execute("""INSERT INTO users (username, display_name, role, password_hash, created_at, origin_username)
                            VALUES (?, ?, ?, ?, ?, ?)""",
                         (u["username"], u["display_name"], u["role"], u["password_hash"],
                          u.get("created_at") or _now(), u.get("origin_username") or u["username"]))
            added += 1
    return added


def export_users() -> list[dict]:
    """כל המשתמשים כולל הגיבוב של הסיסמה (לגיבוי). שיחות לא נכללות."""
    init_db()
    with closing(_connect()) as conn:
        rows = conn.execute("""SELECT username, display_name, role, password_hash, created_at, origin_username
                               FROM users ORDER BY created_at, username""").fetchall()
    return [dict(row) for row in rows]


def create_user(username: str, display_name: str, role: str, password_hash: str) -> bool:
    """יוצר משתמש חדש. False אם שם המשתמש כבר תפוס (בלי הבחנה בין אותיות גדולות לקטנות)."""
    init_db()
    with closing(_connect()) as conn, conn:
        if conn.execute("SELECT 1 FROM users WHERE LOWER(username) = LOWER(?) OR LOWER(origin_username) = LOWER(?)",
                        (username, username)).fetchone():
            return False
        conn.execute("""INSERT INTO users (username, display_name, role, password_hash, created_at, origin_username)
                        VALUES (?, ?, ?, ?, ?, ?)""", (username, display_name, role, password_hash, _now(), username))
    return True


def delete_user(username: str) -> bool:
    """מוחק משתמש ואת אסימוני ההתחברות שלו. השיחות שלו נשארות (נראות למנהל)."""
    init_db()
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM auth_tokens WHERE username = ?", (username,))
        return conn.execute("DELETE FROM users WHERE username = ?", (username,)).rowcount > 0


def clear_tokens(username: str) -> None:
    """מנתק את המשתמש מכל המכשירים (למשל אחרי איפוס סיסמה על ידי מנהל)."""
    init_db()
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM auth_tokens WHERE username = ?", (username,))


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
        rows = conn.execute("""SELECT session_id, created_at, updated_at, title, topic, first_user, user_messages
                               FROM sessions WHERE username = ? AND first_user IS NOT NULL
                               ORDER BY updated_at DESC""", (username,)).fetchall()
    return [{"session_id": row["session_id"], "created_at": row["created_at"], "updated_at": row["updated_at"],
             "preview": row["first_user"][:80], "title": display_title(row["title"], row["topic"], row["created_at"]),
             "when": relative_day(row["updated_at"]), "messages": row["user_messages"] or 0} for row in rows]


def mark_session_viewed(session_id: str, admin_username: str) -> None:
    """רושם שמנהל צפה בשיחה (תאריך ושם)."""
    init_db()
    with closing(_connect()) as conn, conn:
        conn.execute("UPDATE sessions SET admin_viewed_at = ?, admin_viewed_by = ? WHERE session_id = ?",
                     (_now(), admin_username, session_id))


def list_all_sessions() -> list[dict]:
    """כל השיחות של כל המשתמשים (לצפייה בלבד, למנהל), מהאחרונה לראשונה. שיחה בלי הודעות של העובד לא נכללת.
    לכל שיחה: שם תצוגה של הבעלים; כמה שאלות הועברו בה לנציג (rep_total), כמה עדיין פתוחות (rep_open),
    אילו נציגים ענו (reps - שמות משתמש); ומתי ועל ידי מי נצפתה לאחרונה (viewed_at / viewed_by)."""
    init_db()
    with closing(_connect()) as conn:
        rows = conn.execute("""SELECT s.session_id, s.username, s.first_user, s.user_messages, s.created_at, s.updated_at, s.title, s.topic,
                                      s.admin_viewed_at, s.admin_viewed_by, u.display_name,
                                      (SELECT COUNT(*) FROM rep_questions q WHERE q.session_id = s.session_id
                                         AND q.status != 'cancelled') AS rep_total,
                                      (SELECT COUNT(*) FROM rep_questions q WHERE q.session_id = s.session_id
                                         AND q.status = 'open') AS rep_open,
                                      (SELECT __REPS__ FROM rep_questions q
                                         WHERE q.session_id = s.session_id AND q.answered_by IS NOT NULL) AS reps
                               FROM sessions s LEFT JOIN users u ON u.username = s.username
                               ORDER BY s.updated_at DESC""".replace(
            "__REPS__", "STRING_AGG(DISTINCT q.answered_by, ',')" if using_postgres() else "group_concat(DISTINCT q.answered_by)")
        ).fetchall()
    sessions = []
    for row in rows:
        first_user = row["first_user"]
        if first_user is None:
            continue
        sessions.append({"session_id": row["session_id"], "username": row["username"] or "(ללא משתמש)",
                         "display_name": row["display_name"] or row["username"] or "?",
                         "created_at": row["created_at"], "updated_at": row["updated_at"],
                         "title": display_title(row["title"], row["topic"], row["created_at"]),
                         "messages": row["user_messages"] or 0,
                         "rep_total": row["rep_total"], "rep_open": row["rep_open"],
                         "reps": [r for r in (row["reps"] or "").split(",") if r],
                         "viewed_at": row["admin_viewed_at"], "viewed_by": row["admin_viewed_by"]})
    return sessions


def list_session_questions(session_id: str) -> list[dict]:
    """כל השאלות לנציג של שיחה אחת (למנהל), לפי סדר יצירה."""
    init_db()
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT * FROM rep_questions WHERE session_id = ? AND status != 'cancelled' ORDER BY id",
                            (session_id,)).fetchall()
    return [dict(row) for row in rows]


def rename_session(session_id: str, username: str, title: str) -> bool:
    """נותן שם לשיחה (רק לבעלים). שם ריק מחזיר את השם האוטומטי (תחילת ההודעה הראשונה)."""
    init_db()
    with closing(_connect()) as conn, conn:
        cursor = conn.execute("UPDATE sessions SET title = ? WHERE session_id = ? AND username = ?",
                              ((title or "").strip()[:60] or None, session_id, username))
    return cursor.rowcount > 0


def delete_session(session_id: str, username: str) -> bool:
    """מוחק שיחה של המשתמש, ומבטל שאלות פתוחות שלה אצל הנציג (כדי שלא יישארו שאלות על שיחה שנמחקה)."""
    init_db()
    with closing(_connect()) as conn, conn:
        cursor = conn.execute("DELETE FROM sessions WHERE session_id = ? AND username = ?", (session_id, username))
        if cursor.rowcount:
            conn.execute("UPDATE rep_questions SET status = 'cancelled' WHERE session_id = ? AND status = 'open'",
                         (session_id,))
    return cursor.rowcount > 0


# --- שאלות לנציג ---

def create_question(session_id: str, missing_field: str, context_message: str, employee_summary: str) -> int:
    init_db()
    with closing(_connect()) as conn, conn:
        cur = conn.execute("""
            INSERT INTO rep_questions (session_id, missing_field, context_message, employee_summary, created_at)
            VALUES (?, ?, ?, ?, ?)""" + (" RETURNING id" if using_postgres() else ""),
                           (session_id, missing_field, context_message, employee_summary, _now()))
        return cur.fetchone()["id"] if using_postgres() else cur.lastrowid


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
                    answer_note: str | None = None, answered_by: str | None = None) -> None:
    """הנציג עונה (answered_by - שם המשתמש שלו, נשמר לצורך מעקב). השיחה של העובד תקלוט את התשובה בטעינה הבאה."""
    init_db()
    with closing(_connect()) as conn, conn:
        conn.execute("""UPDATE rep_questions SET status = 'answered', answer_value = ?, effective_from = ?,
                        answer_note = ?, answered_at = ?, answered_by = ? WHERE id = ? AND status = 'open'""",
                     (answer_value, effective_from, answer_note, _now(), answered_by, question_id))


def answered_unapplied(session_id: str) -> list[dict]:
    init_db()
    with closing(_connect()) as conn:
        rows = conn.execute("""SELECT * FROM rep_questions WHERE session_id = ? AND status = 'answered'
                               ORDER BY id""", (session_id,)).fetchall()
    return [dict(r) for r in rows]


def mark_applied(question_id: int) -> None:
    init_db()
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
