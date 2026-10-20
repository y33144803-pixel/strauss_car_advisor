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


def test_change_username_keeps_sessions_and_login_and_is_not_resurrected_by_seeding():
    auth.seed_users()
    db.claim_session("s1", "dana")
    db.save_messages("s1", [{"role": "user", "content": "שאלה"}])
    token = db.create_token("dana")
    updated = auth.change_username("dana", "secret-1", "dana.k")
    assert updated == {"username": "dana.k", "display_name": "דנה כהן", "role": "employee"}
    assert db.user_for_token(token)["username"] == "dana.k"                  # ההתחברות נשמרת
    assert [h["session_id"] for h in db.list_user_sessions("dana.k")] == ["s1"]   # וגם ההיסטוריה
    assert db.list_user_sessions("dana") == []
    with pytest.raises(auth.LoginError):
        auth.authenticate("dana", "secret-1")                                # השם הישן לא נכנס
    assert auth.authenticate("dana.k", "secret-1")["role"] == "employee"
    assert auth.seed_users() == 0                                            # הפעלה מחדש לא יוצרת שוב את "dana"
    assert db.get_user("dana") is None


@pytest.mark.parametrize("bad", ["ab", "a b c", "dana:x", "x" * 31, "שם;"])
def test_change_username_rejects_invalid_names(bad):
    auth.seed_users()
    with pytest.raises(auth.LoginError):
        auth.change_username("dana", "secret-1", bad)


def test_change_username_requires_password_and_unique_name():
    auth.seed_users()
    with pytest.raises(auth.LoginError):
        auth.change_username("dana", "wrong", "dana2")
    with pytest.raises(auth.LoginError):
        auth.change_username("dana", "secret-1", "MOSHE")                    # תפוס (בלי תלות באותיות)
    with pytest.raises(auth.LoginError):
        auth.change_username("dana", "secret-1", "dana")                     # זהה לנוכחי
    assert auth.change_username("dana", "secret-1", "דנה_כ")["username"] == "דנה_כ"   # גם עברית


def test_rename_and_delete_session_only_by_owner():
    auth.seed_users()
    db.claim_session("s1", "dana")
    db.save_messages("s1", [{"role": "user", "content": "שאלה ארוכה על הנטו שלי אחרי החלפת רכב"}])
    assert db.list_user_sessions("dana")[0]["title"].startswith("בדיקת נטו")           # שם אוטומטי (עוד אין נושא)
    assert db.rename_session("s1", "moshe", "פריצה") is False                           # לא הבעלים
    assert db.rename_session("s1", "dana", "  הרכב החדש  ") is True
    assert db.list_user_sessions("dana")[0]["title"] == "הרכב החדש"
    db.rename_session("s1", "dana", "")                                                 # ריק = חזרה לשם האוטומטי
    assert db.list_user_sessions("dana")[0]["title"].startswith("בדיקת נטו")
    assert db.delete_session("s1", "moshe") is False and db.session_exists("s1")
    db.create_question("s1", "new_participation", "חסר", "סיכום")
    assert db.delete_session("s1", "dana") is True
    assert not db.session_exists("s1") and db.list_user_sessions("dana") == []
    assert db.list_questions("open") == []                                              # השאלה לנציג בוטלה


def test_admin_creates_users_and_rules_are_enforced():
    auth.create_user("newrep", "secret-1", "rep", "נציג חדש")
    assert auth.authenticate("newrep", "secret-1")["role"] == "rep"
    for args in (("newrep", "secret-2", "rep", "x"), ("NEWREP", "secret-2", "rep", "x"),
                 ("ab", "secret-2", "rep", "x"), ("okname", "123", "rep", "x"),
                 ("okname", "secret-2", "boss", "x"), ("okname", "secret-2", "rep", " ")):
        with pytest.raises(auth.LoginError):
            auth.create_user(*args)


def test_admin_reset_password_logs_user_out_and_delete_guards():
    auth.create_user("boss", "secret-1", "admin", "מנהל")
    auth.create_user("emp", "secret-1", "employee", "עובד")
    token = db.create_token("emp")
    auth.reset_password("emp", "brand-new-1")
    assert db.user_for_token(token) is None
    assert auth.authenticate("emp", "brand-new-1")
    with pytest.raises(auth.LoginError):
        auth.delete_user("boss", "boss")              # לא מוחקים את עצמך
    with pytest.raises(auth.LoginError):
        auth.delete_user("emp", "boss")               # המנהל האחרון
    auth.delete_user("boss", "emp")
    assert db.get_user("emp") is None


def test_admin_sees_all_sessions():
    for user in ("alice", "bobby"):
        auth.create_user(user, "secret-1", "employee", user)
        db.claim_session(f"s-{user}", user)
        db.save_messages(f"s-{user}", [{"role": "user", "content": f"שלום מ-{user}"}])
    assert {s["username"] for s in db.list_all_sessions()} == {"alice", "bobby"}


def test_backup_roundtrip_restores_users_with_passwords(tmp_path, monkeypatch):
    auth.create_user("carol", "secret-1", "rep", "קרול")
    text = auth.export_users_json()
    assert "secret-1" not in text                      # רק גיבוב, לא סיסמה גלויה
    monkeypatch.setenv("STRAUSS_DB_PATH", str(tmp_path / "fresh.db"))      # שרת חדש עם מסד ריק
    assert db.get_user("carol") is None
    assert auth.import_users_json(text) == 1
    assert auth.authenticate("carol", "secret-1")["role"] == "rep"
    assert auth.import_users_json(text) == 0           # שוב: לא נדרס ולא מוכפל


def test_bad_backup_is_rejected_and_startup_loads_file(tmp_path, monkeypatch):
    for bad in ("not json", '{"users": [{"username": "x"}]}', '{"users": [{"username": "abc", "display_name": "d", '
                '"role": "root", "password_hash": "a$b$c"}]}'):
        with pytest.raises(auth.LoginError):
            auth.import_users_json(bad)
    auth.create_user("dave", "secret-1", "employee", "דייב")
    backup = tmp_path / "users_backup.json"
    backup.write_text(auth.export_users_json(), encoding="utf-8")
    monkeypatch.setattr(auth, "BACKUP_FILE", str(backup))
    monkeypatch.setenv("STRAUSS_DB_PATH", str(tmp_path / "fresh2.db"))
    monkeypatch.setenv("AUTH_USERS", "")
    auth.seed_users()
    assert auth.authenticate("dave", "secret-1")


def test_account_forms_share_the_login_throttle():
    auth.create_user("erin", "secret-12", "employee", "ארין")
    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        with pytest.raises(auth.LoginError):
            auth.change_password("erin", "wrong-pass", "another-pass-1")
    with pytest.raises(auth.LoginError, match="יותר מדי"):
        auth.change_password("erin", "secret-12", "another-pass-1")     # גם סיסמה נכונה נחסמת בזמן הנעילה
    with pytest.raises(auth.LoginError, match="יותר מדי"):
        auth.change_username("erin", "secret-12", "erin.new")


def test_minimum_password_length_is_eight():
    with pytest.raises(auth.LoginError):
        auth.create_user("frank", "short12", "employee", "פרנק")      # 7 תווים
    auth.create_user("frank", "longer-12", "employee", "פרנק")


def test_append_messages_keeps_concurrent_messages_and_summary_columns():
    db.claim_session("race", "dana")
    db.save_messages("race", [{"role": "assistant", "content": "שלום"}])
    db.append_messages("race", [{"role": "user", "content": "שאלה ראשונה"}])
    db.append_messages("race", [{"role": "assistant", "content": "תשובת נציג", "source": "representative"}])   # "במקביל"
    db.append_messages("race", [{"role": "assistant", "content": "תשובת הבוט"}])
    contents = [m["content"] for m in db.load_messages("race")]
    assert contents == ["שלום", "שאלה ראשונה", "תשובת נציג", "תשובת הבוט"]
    row = db.list_user_sessions("dana")[0]
    assert row["preview"] == "שאלה ראשונה" and row["messages"] == 1


def test_session_title_is_professional_and_follows_the_topic():
    from state import ConversationState
    auth.seed_users()
    db.claim_session("t1", "dana")
    db.save_messages("t1", [{"role": "user", "content": "אני רוצה לעבור לרכב חשמלי"}])
    assert db.list_user_sessions("dana")[0]["title"].startswith("בדיקת נטו · ")
    state = db.load_state("t1")
    state.target.kind, state.target.label = "car", "BYD ATTO 3"
    db.save_state(state)
    title = db.list_user_sessions("dana")[0]["title"]
    assert title.startswith("השוואה ל-BYD ATTO 3 · ") and "אני רוצה" not in title
    state.target.kind, state.target.label = "waiver", None
    db.save_state(state)
    assert db.list_user_sessions("dana")[0]["title"].startswith("ויתור על רכב · ")
    assert db.list_all_sessions()[0]["title"].startswith("ויתור על רכב · ")            # אותו שם גם במסך המנהל
    db.rename_session("t1", "dana", "הרכב של אשתי")
    assert db.list_user_sessions("dana")[0]["title"] == "הרכב של אשתי"                  # שם ידני גובר
