"""בדיקות המסכים בלי דפדפן (Streamlit AppTest) ובלי מודל שפה."""
import os

import pytest
from streamlit.testing.v1 import AppTest

import db
from agent_tools import (set_current_situation, calculate_net_salary, find_current_car, request_representative_info,
                         set_current_situation, update_employee_data, verify_budget_and_eligibility)

VIEWS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "views")
SID = "screen-test"


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("STRAUSS_DB_PATH", str(tmp_path / "test.db"))


def signed_in(at: AppTest, role: str = "both", name: str = "נועה לוי") -> AppTest:
    """מדמה משתמש מחובר (בהרצה ישירה של מסך, בלי app.py שמבצע את הכניסה)."""
    at.session_state["user"] = {"username": "tester", "display_name": name, "role": role}
    return at


def employee_app(show_comparison: bool = False) -> AppTest:
    at = signed_in(AppTest.from_file(os.path.join(VIEWS, "employee.py"), default_timeout=30))
    at.query_params["sid"] = SID
    if show_comparison:
        at.session_state["show_comparison"] = True       # העובדת סימנה "הצג השוואה מפורטת בצד"
    return at.run()


def fill_to_comparison():
    update_employee_data(SID, eligibility_group="ג", base_salary=18500, commissions=0, bonus=0, pension_rate=6,
                         study_fund_rate=2.5, credit_points=2.75)
    find_current_car(SID, "corolla cross", 2025)
    set_current_situation(SID, "car", candidate_number=1, participation=300)
    verify_budget_and_eligibility(SID, "CAR-10")
    update_employee_data(SID, new_participation=300)
    assert calculate_net_salary(SID)["ok"]


def test_new_session_shows_greeting_and_no_comparison():
    at = employee_app()
    assert not at.exception
    assert any("שלום" in m.value for m in at.markdown)
    assert not any("ההשוואה תופיע כאן" in m.value for m in at.markdown)   # בלי תוצאה - אין פאנל השוואה ריק
    assert not at.toggle and not at.subheader                              # וגם לא כפתור סימון
    assert at.query_params["sid"] == [SID] or at.query_params["sid"] == SID


def test_comparison_table_matches_engine_reference():
    fill_to_comparison()                      # E-001 מהאקסל: 11,032 -> 11,693
    at = employee_app(show_comparison=True)
    assert not at.exception
    kpis = next(m.value for m in at.markdown if "class='kpis'" in m.value)
    assert "11,032 ₪" in kpis and "11,693 ₪" in kpis and "+661 ₪" in kpis
    assert "diff pos" in kpis                                   # הפרש חיובי מסומן בירוק
    table = next(m.value for m in at.markdown if "<table" in m.value)
    assert "COROLLA CROSS" in table and "ATTO 3" in table and "החזר חשמל" in table
    assert not any("השוואת ביניים" in w.value for w in at.warning)


def test_refresh_keeps_conversation():
    fill_to_comparison()
    first = employee_app(show_comparison=True)
    second = employee_app(show_comparison=True)       # "רענון" = הרצה מחדש עם אותו sid
    kpis = lambda at: next(m.value for m in at.markdown if "class='kpis'" in m.value)
    assert kpis(first) == kpis(second)


def test_provisional_comparison_is_labeled_and_sidebar_options_exist():
    update_employee_data(SID, eligibility_group="ג", base_salary=18500, commissions=0, bonus=0, pension_rate=6,
                         study_fund_rate=2.5, credit_points=2.75)
    find_current_car(SID, "corolla cross", 2025)
    set_current_situation(SID, "car", candidate_number=1, participation=300)
    verify_budget_and_eligibility(SID, "CAR-10")
    request_representative_info(SID, "new_participation", "בדיקה", True)
    at = employee_app(show_comparison=True)
    assert any("השוואת ביניים" in w.value for w in at.warning)
    assert any("ממתין לתשובה" in i.value or "ממתין" in i.value for i in at.info)


def test_comparison_is_hidden_until_the_toggle_is_checked():
    fill_to_comparison()
    at = employee_app()
    assert at.toggle and at.toggle[0].label == "הצג השוואה מפורטת בצד"     # יש תוצאה - יש כפתור סימון
    assert not any("class='kpis'" in m.value for m in at.markdown)         # אבל ההשוואה מוסתרת כברירת מחדל
    at.toggle[0].set_value(True).run()
    assert any("class='kpis'" in m.value for m in at.markdown)             # ומופיעה בצד אחרי הסימון


def test_progress_total_never_changes_during_the_conversation():
    from ui import state_progress
    totals = set()
    totals.add(state_progress(db.load_state(SID))[1])
    update_employee_data(SID, eligibility_group="ג", base_salary=18500, commissions=0, bonus=0, pension_rate=6,
                         study_fund_rate=2.5, credit_points=2.75)
    totals.add(state_progress(db.load_state(SID))[1])
    find_current_car(SID, "corolla cross", 2025)
    set_current_situation(SID, "waiver", maintenance=1500)               # ויתור: אין השתתפות ברכב נוכחי
    totals.add(state_progress(db.load_state(SID))[1])
    assert db.load_state(SID).current.kind == "waiver"
    verify_budget_and_eligibility(SID, "CAR-10")
    totals.add(state_progress(db.load_state(SID))[1])
    assert totals == {11}
    done = state_progress(db.load_state(SID))[0]
    update_employee_data(SID, new_participation=300)
    assert state_progress(db.load_state(SID))[0] == done + 1               # ההתקדמות רק עולה


def test_representative_answer_flows_back_to_employee_screen():
    fill_to_comparison()
    verify_budget_and_eligibility(SID, "CAR-11")                 # חלופה חדשה בלי השתתפות
    request_representative_info(SID, "new_participation", "EV3 בקבוצה ג", True)

    rep = signed_in(AppTest.from_file(os.path.join(VIEWS, "representative.py"), default_timeout=30)).run()
    assert not rep.exception
    assert any("שאלות פתוחות (1)" in s.value for s in rep.subheader)
    rep.number_input[0].set_value(350)
    rep.button[0].click().run()                                  # "שלח תשובה"
    assert db.list_questions("open") == []
    assert db.list_questions("answered")[0]["answer_value"] == 350

    at = employee_app()                                          # הטעינה הבאה של העובד קולטת את התשובה
    assert not at.exception
    assert any("הנציג ענה" in m.value for m in at.markdown)
    assert not db.load_state(SID).waiting_for_representative


def test_representative_cannot_submit_empty_amount():
    fill_to_comparison()
    verify_budget_and_eligibility(SID, "CAR-11")
    request_representative_info(SID, "new_participation", "EV3 בקבוצה ג", True)

    rep = signed_in(AppTest.from_file(os.path.join(VIEWS, "representative.py"), default_timeout=30)).run()
    rep.button[0].click().run()                                  # שליחה בלי סכום
    assert any("יש להזין סכום" in e.value for e in rep.error)
    assert len(db.list_questions("open")) == 1                   # השאלה נשארת פתוחה

    rep.number_input[0].set_value(0)                             # אפס מפורש הוא תשובה חוקית
    rep.button[0].click().run()
    answered = db.list_questions("answered")
    assert len(answered) == 1 and answered[0]["answer_value"] == 0


# ------------------------------------------------------------ קטלוג בפאנל, בידוד כיווניות, שגיאות וניסיון חוזר

def test_fix_bidi_wraps_amounts_and_keeps_text():
    from ui import fix_bidi
    text = "הנטו עולה ב-661 ₪, מ-11,032 ₪ ל-11,693 ₪."
    fixed = fix_bidi(text)
    assert fixed.count("\u2066") == 3 and fixed.count("\u2069") == 3
    assert fixed.replace("\u2066", "").replace("\u2069", "") == text      # הטקסט עצמו לא משתנה
    assert fix_bidi(fixed) == fixed                                       # פעולה חוזרת לא מכפילה


def test_stored_explanation_is_clean_text():
    fill_to_comparison()
    explanation = db.load_state(SID).last_result["explanation"]
    assert "\u2066" not in explanation and "\u2069" not in explanation    # בידוד מתווסף רק בתצוגה


def car_buttons(at):
    return [b for b in at.button if b.label.startswith("קבוצה ")]


def recording_chat(sent):
    """chat מדומה: רושם את ההודעה ששלח המסך, ושומר הודעה + תשובה כמו chat() האמיתי."""
    import agent

    def fake_chat(session_id, text=None, llm=None, on_step=None, on_status=None):
        sent.append(text)
        messages = agent.start_session(session_id)
        messages.append({"role": "user", "content": text})
        messages.append({"role": "assistant", "content": "בסדר"})
        db.save_messages(session_id, messages)
        return {"reply": "בסדר", "tool_calls": []}
    return fake_chat


# ------------------------------------------------------------ בורר הרכבים מעל תיבת השיחה

def test_no_picker_before_group_is_known():
    at = employee_app()
    assert not car_buttons(at) and not at.dataframe


def test_picker_shows_buttons_for_up_to_five_cars():
    update_employee_data(SID, eligibility_group="א")                      # 4 רכבים בקבוצה א'
    set_current_situation(SID, "none")                                  # קודם המצב היום, אחר כך חלופות
    at = employee_app()
    assert len(car_buttons(at)) == 4 and not at.dataframe
    assert any("PICANTO" in b.label for b in car_buttons(at))


def test_picker_shows_table_for_more_than_five_cars():
    update_employee_data(SID, eligibility_group="ג")                      # 13 רכבים
    set_current_situation(SID, "none")                                  # קודם המצב היום, אחר כך חלופות
    at = employee_app()
    assert at.dataframe and not car_buttons(at)
    table = at.dataframe[0].value
    names = list(table["רכב"])
    assert len(names) == 13 and any("ATTO 3" in n for n in names)
    assert not any("MODEL 3" in n or "CARNIVAL" in n for n in names)      # קבוצה ד ומסלול ילדים לא מוצגים


def test_picker_has_waiver_button_only_when_occupation_allows():
    update_employee_data(SID, eligibility_group="א")
    set_current_situation(SID, "none")                                  # קודם המצב היום, אחר כך חלופות
    assert not any("ויתור" in b.label for b in employee_app().button)
    update_employee_data(SID, occupation="רכז/ת רכש")
    assert any("ויתור על הרכב" in b.label for b in employee_app().button)


def test_clicking_a_car_sends_it_to_the_conversation(monkeypatch):
    import agent
    sent = []
    monkeypatch.setattr(agent, "chat", recording_chat(sent))
    update_employee_data(SID, eligibility_group="א")
    set_current_situation(SID, "none")                                  # קודם המצב היום, אחר כך חלופות
    at = employee_app()
    next(b for b in car_buttons(at) if "PICANTO" in b.label).click().run()
    assert sent == ["אני רוצה לבדוק את קיה קוריאה PICANTO (2025) (מזהה CAR-01)"]


def test_clicking_waiver_sends_it_to_the_conversation(monkeypatch):
    import agent
    sent = []
    monkeypatch.setattr(agent, "chat", recording_chat(sent))
    update_employee_data(SID, eligibility_group="א", occupation="רכז/ת רכש")
    set_current_situation(SID, "none")                                  # קודם המצב היום, אחר כך חלופות
    at = employee_app()
    next(b for b in at.button if "ויתור על הרכב" in b.label).click().run()
    assert sent == ["אני רוצה לוותר על הרכב"]


def test_picker_closes_after_choice_and_can_be_reopened():
    fill_to_comparison()                                                  # כבר נבחרה חלופה
    at = employee_app()
    assert not car_buttons(at) and not at.dataframe                       # הבורר סגור
    next(b for b in at.button if b.label == "בחירת רכב אחר").click().run()
    assert db.load_state(SID).picker_open
    assert at.dataframe or car_buttons(at)                                # נפתח
    next(b for b in at.button if b.label == "סגור").click().run()
    assert not db.load_state(SID).picker_open


# ------------------------------------------------------------ פאנל הצד: הנתונים שנקלטו עד כה

def sidebar_text(at) -> str:
    return " ".join(m.value for m in at.sidebar.markdown)


def test_state_card_starts_empty_and_fills_in_as_data_arrives():
    at = employee_app()
    text = sidebar_text(at)
    assert "הנתונים שנקלטו עד כה" in text
    empty_before = text.count("טרם הוזן")
    assert empty_before >= 9                                              # כל השדות ריקים בתחילת השיחה

    update_employee_data(SID, eligibility_group="ג", base_salary=14200, pension_rate=6)
    text = sidebar_text(employee_app())
    assert "14,200 ₪" in text and "6%" in text and "ג" in text
    assert text.count("טרם הוזן") == empty_before - 3                     # שלושה שדות התמלאו


def test_state_card_shows_current_and_target_car():
    fill_to_comparison()
    text = sidebar_text(employee_app())
    assert "COROLLA CROSS (2025)" in text and "ATTO 3 (2025)" in text
    assert "טרם הוזן" not in text.replace("עמלות", "")                    # הכול מולא אחרי חישוב


def test_state_progress_counts_required_fields():
    from ui import state_progress
    assert state_progress(db.load_state(SID)) == (0, 11)
    update_employee_data(SID, eligibility_group="ג", base_salary=14200)
    assert state_progress(db.load_state(SID)) == (2, 11)
    fill_to_comparison()
    done, total = state_progress(db.load_state(SID))
    assert done == total


def test_unanswered_message_shows_retry_and_resumes(monkeypatch):
    import agent
    agent.start_session(SID)
    messages = db.load_messages(SID)
    messages.append({"role": "user", "content": "היי"})
    db.save_messages(SID, messages)

    def fake_chat(session_id, text=None, llm=None, on_step=None, on_status=None):
        saved = db.load_messages(session_id)
        saved.append({"role": "assistant", "content": "שלום! תשובה אחרי ניסיון חוזר"})
        db.save_messages(session_id, saved)
        return {"reply": "שלום! תשובה אחרי ניסיון חוזר", "tool_calls": []}

    monkeypatch.setattr(agent, "chat", fake_chat)
    at = employee_app()
    assert any("עדיין לא נענתה" in w.value for w in at.warning)
    retry = next(b for b in at.button if b.label == "נסי שוב")
    retry.click().run()
    assert db.load_messages(SID)[-1]["role"] == "assistant"
    assert not any("עדיין לא נענתה" in w.value for w in at.warning)


def test_service_failure_shows_friendly_message(monkeypatch):
    import agent

    def failing_chat(session_id, text=None, llm=None, on_step=None, on_status=None):
        messages = agent.start_session(session_id)
        messages.append({"role": "user", "content": text})
        db.save_messages(session_id, messages)                            # כמו chat() האמיתי: ההודעה נשמרת לפני הפנייה למודל
        raise agent.LLMUnavailable("WinError 10060 gemini-3.7-flash: 503 UNAVAILABLE")

    monkeypatch.setattr(agent, "chat", failing_chat)
    at = employee_app()
    at.chat_input[0].set_value("שלום").run()
    warnings = " ".join(w.value for w in at.warning)
    assert "עמוס" in warnings and "WinError" not in warnings and "503" not in warnings
    assert any(b.label == "נסי שוב" for b in at.button)


def test_bot_avatar_is_the_image_from_images_folder():
    from ui import BOT_AVATAR, avatar_for
    assert os.path.exists(BOT_AVATAR) and BOT_AVATAR.replace("\\", "/").endswith("images/bot-image.jpg")
    assert avatar_for("assistant") == BOT_AVATAR
    assert avatar_for("user") is None                           # למשתמש נשארת ברירת המחדל


def test_changed_backend_module_is_reloaded(tmp_path, monkeypatch):
    """שינוי בקובץ backend בזמן שהשרת רץ גורם לטעינה מחדש - במקום ImportError על גרסה ישנה."""
    import importlib
    import sys
    from ui import reload_changed_modules

    module_file = tmp_path / "fake_backend_mod.py"
    module_file.write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    mtimes = {}
    fake = importlib.import_module("fake_backend_mod")
    assert fake.VALUE == 1
    assert reload_changed_modules(str(tmp_path), mtimes) == []            # הרצה ראשונה: רק שומרת מצב התחלתי
    assert reload_changed_modules(str(tmp_path), mtimes) == []            # בלי שינוי - לא נוגעים בכלום

    module_file.write_text("VALUE = 2\nNEW_NAME = 'x'\n", encoding="utf-8")
    later = module_file.stat().st_mtime + 10
    os.utime(module_file, (later, later))
    assert reload_changed_modules(str(tmp_path), mtimes) == ["fake_backend_mod"]
    assert "fake_backend_mod" not in sys.modules
    assert importlib.import_module("fake_backend_mod").NEW_NAME == "x"   # הייבוא הבא טוען את הגרסה החדשה
    sys.modules.pop("fake_backend_mod", None)


# ------------------------------------------------------------------ התחברות (דרך app.py המלא)

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "0")
    monkeypatch.setenv("AUTH_USERS", "dana:secret-1:employee:דנה כהן;moshe:secret-2:rep:משה לוי")
    import auth
    import streamlit as st
    auth._failures.clear()
    st.cache_resource.clear()                     # טעינת המשתמשים (cache_resource) צריכה לרוץ מחדש לכל מסד בדיקה


def app_with_login(username=None, password=None) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=30)
    at.query_params["sid"] = SID
    at.run()
    if username:
        at.text_input[0].set_value(username)
        at.text_input[1].set_value(password)
        at.button[0].click().run()
    return at


def test_login_page_is_shown_before_signing_in(auth_on):
    at = app_with_login()
    assert not at.exception
    assert any("כניסה למערכת" in m.value for m in at.markdown)
    assert not at.chat_input                                         # בלי כניסה אין שיחה


def test_wrong_password_shows_error_and_stays_logged_out(auth_on):
    at = app_with_login("dana", "wrong-password")
    assert any("שם משתמש או סיסמה שגויים" in e.value for e in at.error)
    assert not at.chat_input


def test_employee_login_shows_chat_with_own_first_letter_and_no_rep_screen(auth_on):
    at = app_with_login("dana", "secret-1")
    assert not at.exception and at.chat_input
    css = " ".join(m.value for m in at.markdown)
    assert 'content: "ד"' in css                                      # האות הראשונה של "דנה כהן"
    assert not any("שאלות פתוחות" in s.value for s in at.subheader)


def test_representative_login_shows_rep_screen_not_chat(auth_on):
    at = app_with_login("moshe", "secret-2")
    assert not at.exception
    assert any("שאלות פתוחות" in s.value for s in at.subheader)
    assert not at.chat_input


def test_conversations_are_private_to_their_owner(auth_on):
    import db
    db.claim_session("dana-chat", "dana")
    db.save_messages("dana-chat", [{"role": "assistant", "content": "שלום"},
                                   {"role": "user", "content": "הנתונים הסודיים של דנה"}])
    other = AppTest.from_file(os.path.join(VIEWS, "employee.py"), default_timeout=30)
    other.session_state["user"] = {"username": "eli", "display_name": "אלי", "role": "employee"}
    other.query_params["sid"] = "dana-chat"                          # אלי מנסה לפתוח את השיחה של דנה
    other.run()
    assert not any("הנתונים הסודיים" in m.value for m in other.markdown)
    assert other.query_params["sid"] != "dana-chat"


def test_login_fields_can_be_prefilled_with_an_employee_for_demos(auth_on, monkeypatch):
    monkeypatch.setenv("LOGIN_PREFILL_USER", "dana")
    at = app_with_login()
    assert at.text_input[0].value == "dana" and at.text_input[1].value == "secret-1"
    at.button[0].click().run()                                       # לחיצה על "כניסה" בלי להקליד כלום
    assert at.chat_input and not at.exception


def test_representative_is_never_prefilled(auth_on, monkeypatch):
    monkeypatch.setenv("LOGIN_PREFILL_USER", "moshe")                # נציג - לא ימולא מראש
    at = app_with_login()
    assert at.text_input[0].value == "" and at.text_input[1].value == ""


def test_login_fields_are_empty_by_default(auth_on, monkeypatch):
    monkeypatch.setenv("LOGIN_PREFILL_USER", "")                 # (delenv לא מספיק: .env המקומי היה נטען שוב)
    at = app_with_login()
    assert at.text_input[0].value == "" and at.text_input[1].value == ""
