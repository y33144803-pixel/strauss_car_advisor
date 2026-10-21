"""הגדרות משותפות לכל הבדיקות.

ברירת המחדל: כל בדיקה עובדת מול SQLite זמני (STRAUSS_DB_PATH שהבדיקה מגדירה).
כדי להריץ את אותן בדיקות מול Postgres: TEST_DATABASE_URL=postgresql://... python -m pytest
(לפני כל בדיקה מרוקנים את הטבלאות במסד הבדיקה - אל תפנו אותו למסד אמיתי!)"""
import os

import pytest


@pytest.fixture(autouse=True)
def _postgres_for_tests(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        yield
        return
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))
    import db
    monkeypatch.setenv("DATABASE_URL", url)
    db._READY.clear()
    db.init_db()
    with db._connect() as conn:
        conn.execute("TRUNCATE sessions, users, auth_tokens, rep_questions RESTART IDENTITY")
    yield
    db._READY.clear()


@pytest.fixture
def fresh_database(monkeypatch, tmp_path):
    """מדמה שרת חדש עם מסד ריק (SQLite: קובץ חדש; Postgres: מרוקנים את הטבלאות)."""
    def make(name: str = "fresh.db") -> None:
        if os.environ.get("TEST_DATABASE_URL"):
            import db
            with db._connect() as conn:
                conn.execute("TRUNCATE sessions, users, auth_tokens, rep_questions RESTART IDENTITY")
        else:
            monkeypatch.setenv("STRAUSS_DB_PATH", str(tmp_path / name))
    return make
