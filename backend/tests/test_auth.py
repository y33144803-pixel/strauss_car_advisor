"""בדיקות התחברות: סיסמאות, טעינת משתמשים, אסימונים, נעילה והיסטוריית שיחות לכל משתמש."""
import pytest

import auth
import db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("STRAUSS_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("AUTH_USERS", "dana:secret-1:employee:דנה כהן;moshe:secret-2:rep:משה לוי")
    auth._failures.clear()


def test_password_hash_is_salted_and_verifiable():
    first, second = auth.hash_password("abc12345"), auth.hash_password("abc12345")
    assert first != second                                   # מלח אקראי - אותה סיסמה, גיבוב שונה
    assert auth.verify_password("abc12345", first) and not auth.verify_password("abc123456", first)
    assert "abc12345" not in first                           # הסיסמה עצמה לא נשמרת
    assert auth.verify_password("x", "not-a-hash") is False


def test_users_are_seeded_from_env_without_overwriting():
    assert auth.seed_users() == 2
    assert db.get_user("dana")["display_name"] == "דנה כהן" and db.get_user("moshe")["role"] == "rep"
    auth.change_password("dana", "secret-1", "new-secret")
    assert auth.seed_users() == 0                            # הפעלה חוזרת לא דורסת את הסיסמה ששונתה
    assert auth.authenticate("dana", "new-secret")["role"] == "employee"


@pytest.mark.parametrize("bad", ["dana:secret-1:employee", "dana:secret-1:boss:דנה", "dana:123:employee:דנה"])
def test_bad_user_definitions_are_rejected(bad):
    with pytest.raises(ValueError):
        auth.parse_users(bad)


def test_authenticate_success_and_failure_messages_do_not_leak():
    auth.seed_users()
    assert auth.authenticate("dana", "secret-1") == {"username": "dana", "display_name": "דנה כהן", "role": "employee"}
    for username, password in (("dana", "wrong"), ("nobody", "secret-1")):
        with pytest.raises(auth.LoginError) as error:
            auth.authenticate(username, password)
        assert str(error.value) == "שם משתמש או סיסמה שגויים."   # אותה הודעה: לא מסגירה אם המשתמש קיים


def test_account_locks_after_repeated_failures():
    auth.seed_users()
    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        with pytest.raises(auth.LoginError):
            auth.authenticate("dana", "wrong")
    with pytest.raises(auth.LoginError) as error:
        auth.authenticate("dana", "secret-1")                # גם סיסמה נכונה נדחית בזמן הנעילה
    assert "יותר מדי ניסיונות" in str(error.value)


def test_change_password_rules():
    auth.seed_users()
    with pytest.raises(auth.LoginError):
        auth.change_password("dana", "wrong", "new-secret")
    with pytest.raises(auth.LoginError):
        auth.change_password("dana", "secret-1", "123")      # קצרה
    with pytest.raises(auth.LoginError):
        auth.change_password("dana", "secret-1", "secret-1")  # זהה לנוכחית
    auth.change_password("dana", "secret-1", "brand-new")
    with pytest.raises(auth.LoginError):
        auth.authenticate("dana", "secret-1")
    assert auth.authenticate("dana", "brand-new")


def test_token_lifecycle():
    auth.seed_users()
    token = db.create_token("dana")
    assert db.user_for_token(token)["username"] == "dana"
    assert db.user_for_token("garbage") is None and db.user_for_token("") is None
    db.delete_token(token)
    assert db.user_for_token(token) is None
    expired = db.create_token("dana", hours=-1)
    assert db.user_for_token(expired) is None


def test_sessions_belong_to_their_owner_and_history_is_per_user():
    auth.seed_users()
    assert db.claim_session("s-dana", "dana") is True
    assert db.claim_session("s-dana", "dana") is True        # אותו בעלים - בסדר
    assert db.claim_session("s-dana", "moshe") is False      # שיחה של משתמש אחר - נדחית
    db.save_messages("s-dana", [{"role": "assistant", "content": "שלום"},
                                {"role": "user", "content": "אני רוצה לבדוק את הנטו שלי אם אחליף רכב"}])
    db.claim_session("s-empty", "dana")
    db.save_messages("s-empty", [{"role": "assistant", "content": "שלום"}])       # רק פתיחה - לא בהיסטוריה
    db.claim_session("s-moshe", "moshe")
    db.save_messages("s-moshe", [{"role": "user", "content": "שאלה של משה"}])
    history = db.list_user_sessions("dana")
    assert [h["session_id"] for h in history] == ["s-dana"]
    assert history[0]["preview"].startswith("אני רוצה") and history[0]["messages"] == 1


def test_legacy_session_without_owner_cannot_be_claimed():
    db.save_messages("old", [{"role": "user", "content": "מלפני ההתחברות"}])
    assert db.claim_session("old", "dana") is False
