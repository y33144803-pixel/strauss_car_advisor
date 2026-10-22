"""בדיקות כלי הסוכן - מריצים את התסריטים בלי מודל שפה, כפי שהסוכן היה מפעיל אותם."""
import pytest

import db
from agent_tools import (set_current_situation, apply_representative_answers, calculate_net_salary, find_current_car,
                         get_car_usage_value, request_representative_info, set_current_situation,
                         show_catalog, update_employee_data, verify_budget_and_eligibility)

SID = "test-session"


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("STRAUSS_DB_PATH", str(tmp_path / "test.db"))


def fill_script_employee():
    """העובד מהתסריט ב-PDF: קבוצה ג, 14,200 ₪, עמלות 1,000, פנסיה 6%, קה״ש 2.5%, 2.25 נקודות."""
    r = update_employee_data(SID, eligibility_group="ג", base_salary=14200, commissions=1000, bonus=0,
                             pension_rate=6, study_fund_rate=2.5, credit_points=2.25)
    assert r["ok"], r


def fill_family_employee():
    """עובד במסלול המשפחתי (CAR-19 / CAR-20): ההשתתפות בו לא ידועה מהקטלוג, ולכן נשאלת לנציג."""
    fill_script_employee()                                          # שכר, תנאים ונתוני פנסיה
    r = update_employee_data(SID, eligibility_group="ב", children_ages=[3, 9, 15, 16, 17])
    assert r["ok"], r


def set_corolla_cross_today(participation=300):
    r = find_current_car(SID, "corolla cross", 2025)
    assert r["ok"] and r["data"]["candidates"][0]["source"] == "catalog"
    assert set_current_situation(SID, "car", candidate_number=1)["ok"]
    assert update_employee_data(SID, current_participation=participation)["ok"]


# ------------------------------------------------------------ התסריט המלא מה-PDF

def test_pdf_script_with_representative():
    fill_script_employee()
    set_corolla_cross_today()

    catalog = show_catalog(SID)["data"]
    ids = {c["id"] for c in catalog["cars"]}
    assert "CAR-10" in ids and "CAR-01" in ids          # מהקבוצה שלו ומטה
    assert "CAR-14" not in ids                           # קבוצה ד - לא מוצג
    assert catalog["budget"] == 4500

    v = verify_budget_and_eligibility(SID, "CAR-10")      # ATTO 3 חשמלי
    assert v["ok"] and v["data"]["eligible"] is True
    assert "electric_refund" in v["data"]

    # רכב רגיל: ההשתתפות היא ברירת המחדל שבקטלוג ומאושרת - לא נשאלת לנציג
    assert "representative_question_sent" not in v["data"]
    final = v["data"]["comparison"]
    assert final["status"] == "final"
    assert final["alternative"]["participation"] == 300   # ברירת המחדל מהקטלוג
    assert db.load_state(SID).target.participation_status == "confirmed"
    assert db.list_questions("open") == []


def test_state_survives_reload():
    fill_script_employee()
    # "רענון" = טעינה מחדש מה-DB
    state = db.load_state(SID)
    assert state.employee.base_salary == 14200 and state.employee.eligibility_group == "ג"


# ------------------------------------------------------------ עובדת מקבוצה ב שמוותרת על הרכב (מהטיפים)

def test_group_b_waiver():
    update_employee_data(SID, eligibility_group="ב", base_salary=13000, commissions=0, bonus=0,
                         pension_rate=6, study_fund_rate=2.5, credit_points=2.75)
    find_current_car(SID, "NIRO", 2025)
    set_current_situation(SID, "car", candidate_number=1)
    update_employee_data(SID, current_participation=250)

    v = verify_budget_and_eligibility(SID, "ויתור")
    assert v["ok"] is False and "עיסוק" in v["missing"]   # לא מנחשים עיסוק
    assert update_employee_data(SID, occupation="אנליסט/ית נתונים")["ok"]
    v = verify_budget_and_eligibility(SID, "ויתור")
    assert v["data"]["eligible"] is True and v["data"]["maintenance"] == 1500

    r = calculate_net_salary(SID)
    assert r["ok"] and r["data"]["status"] == "final"
    assert r["data"]["alternative"]["maintenance"] == 1500
    assert r["data"]["alternative"]["usage_value"] == 0
    assert "נוספה אחזקת רכב 1,500 ₪ (חייבת במס)" in r["data"]["changes"]


def test_field_role_cannot_waive():
    update_employee_data(SID, eligibility_group="ב", occupation="נציג/ת מכירות שטח")
    v = verify_budget_and_eligibility(SID, "ויתור")
    assert v["data"]["eligible"] is False


def test_unknown_occupation_rejected_without_listing_the_table():
    r = update_employee_data(SID, occupation="אסטרונאוט")
    assert r["ok"] is False and "לא נמצא" in r["error"] and "מפתח/ת תוכנה" not in r["error"]


# ------------------------------------------------------------ זכאות

def test_higher_group_rejected():
    update_employee_data(SID, eligibility_group="ג")
    v = verify_budget_and_eligibility(SID, "CAR-14")
    assert v["data"]["eligible"] is False
    assert db.load_state(SID).target.kind is None          # לא נשמר כחלופה


def test_family_car_needs_children_count():
    update_employee_data(SID, eligibility_group="ב")
    v = verify_budget_and_eligibility(SID, "CAR-19")
    assert v["ok"] is False and "ילדים עד גיל 23" in v["missing"]
    update_employee_data(SID, children_under_23=5)
    v = verify_budget_and_eligibility(SID, "CAR-19")
    assert v["data"]["eligible"] is True and v["data"]["family_employee_share"] == 1150


def test_family_cars_shown_only_when_eligible():
    update_employee_data(SID, eligibility_group="ב", children_under_23=2)
    set_current_situation(SID, "none")                                  # קודם המצב היום, אחר כך חלופות
    ids = {c["id"] for c in show_catalog(SID)["data"]["cars"]}
    assert "CAR-19" not in ids
    update_employee_data(SID, children_under_23=4)
    ids = {c["id"] for c in show_catalog(SID)["data"]["cars"]}
    assert {"CAR-19", "CAR-20"} <= ids


# ------------------------------------------------------------ אימות ותיקונים

def test_invalid_values_not_saved():
    r = update_employee_data(SID, base_salary=-5, pension_rate=150, credit_points=2.25)
    assert r["ok"] is False
    assert "שכר הבסיס" in r["error"] and "אחוז פנסיה" in r["error"]
    state = db.load_state(SID)
    assert state.employee.base_salary is None and state.employee.pension_rate is None
    assert state.employee.credit_points == 2.25            # הערך התקין כן נשמר


def test_correction_overrides_and_marks_stale():
    fill_script_employee()
    set_corolla_cross_today()
    verify_budget_and_eligibility(SID, "CAR-11")
    update_employee_data(SID, new_participation=300)
    first = calculate_net_salary(SID)["data"]

    r = update_employee_data(SID, pension_rate=7)
    assert r["data"]["corrected"] == ["אחוז פנסיה: 6 → 7"]
    # כבר הייתה תוצאה והנתונים מלאים - הקוד מחשב מחדש אוטומטית, בלי להסתמך על המודל
    second = r["data"]["comparison"]
    assert second["net_today"] < first["net_today"]          # פנסיה גבוהה יותר -> נטו נמוך יותר
    assert "data.comparison" in r["next_step"]
    state = db.load_state(SID)
    assert not state.result_is_stale and state.last_result["net_today"] == second["net_today"]


def test_changing_car_resets_participation():
    fill_family_employee()
    set_corolla_cross_today()
    verify_budget_and_eligibility(SID, "CAR-19")
    update_employee_data(SID, new_participation=350)              # חריגה מהקטלוג: נשאלת שאלה לנציג
    verify_budget_and_eligibility(SID, "CAR-20")                  # רכב אחר: ההשתתפות של הקודם לא נשמרת
    target = db.load_state(SID).target
    assert target.participation == 500 and target.participation_status == "confirmed"   # ברירת המחדל של הרכב החדש
    assert db.list_questions("open") == []


def test_new_participation_requires_chosen_car():
    r = update_employee_data(SID, new_participation=300)
    assert r["ok"] is False


# ------------------------------------------------------------ רכב נוכחי ושווי שימוש

def test_current_car_from_tax_db_when_not_in_catalog():
    r = find_current_car(SID, "AUDI S3", 2010)
    assert r["ok"] and r["data"]["candidates"][0]["source"] == "tax_db"


def test_current_car_not_found():
    r = find_current_car(SID, "NO SUCH CAR XYZ", 2025)
    assert r["ok"] is False and "current_car_usage_value" in r["next_step"]


def test_current_waiver_uses_occupation_table():
    update_employee_data(SID, occupation="מפתח/ת תוכנה")
    r = set_current_situation(SID, "waiver")
    assert r["ok"] and db.load_state(SID).current.maintenance == 1500


def test_get_car_usage_value():
    r = get_car_usage_value(SID, 2026, 2025, "1", "01014", "00003")
    assert r["ok"] and r["data"]["usage_value"] == 3130 and r["data"]["drive"] == "חשמלי"
    assert get_car_usage_value(SID, 2026, 2025, "1", "99999", "1")["ok"] is False


# ------------------------------------------------------------ נציג - מקרים נוספים

def test_blocking_question_prevents_calculation():
    fill_script_employee()
    find_current_car(SID, "corolla cross", 2025)
    set_current_situation(SID, "car", candidate_number=1)
    request_representative_info(SID, "current_participation", "העובד לא יודע כמה הוא משתתף היום", True)
    verify_budget_and_eligibility(SID, "CAR-11")
    update_employee_data(SID, new_participation=300)
    # השתתפות נוכחית עדיין חסרה ומחכים לנציג
    assert calculate_net_salary(SID)["ok"] is False

    q = db.list_questions("open")[0]
    db.answer_question(q["id"], 400)
    events = apply_representative_answers(SID)
    assert events[-1]["recalculated"]["today"]["participation"] == 400


def test_duplicate_question_not_created():
    request_representative_info(SID, "other", "שאלה")
    request_representative_info(SID, "other", "שאלה שוב")
    assert len(db.list_questions("open")) == 1


def test_invalid_rep_field():
    assert request_representative_info(SID, "salary", "x")["ok"] is False


# ------------------------------------------------------------ הגנה מפני איבוד נתונים בשקט

def test_set_current_situation_saves_participation():
    find_current_car(SID, "corolla cross", 2025)
    r = set_current_situation(SID, "car", candidate_number=1, participation=300)
    assert r["ok"] and db.load_state(SID).current.participation == 300


def test_set_current_situation_rejects_irrelevant_params():
    find_current_car(SID, "cross", 2025)                          # שם חלקי - לא נשמר אוטומטית
    r = set_current_situation(SID, "car", candidate_number=1, maintenance=300)
    assert r["ok"] is False and "participation" in r["error"]     # לא מתעלמים בשקט
    assert db.load_state(SID).current.kind is None                # ולא נשמר חצי מצב
    assert set_current_situation(SID, "waiver", maintenance=1500, participation=300)["ok"] is False


# ------------------------------------------------------------ פעולות המסך (actions.py)

def test_switch_alternative_flow():
    from actions import alternative_options, recalculate, switch_alternative
    fill_script_employee()
    set_corolla_cross_today()
    options = {o["choice"] for o in alternative_options(SID)}
    assert "CAR-10" in options and "CAR-14" not in options

    # רכב חדש בלי ערך מהעובד -> ברירת המחדל מהקטלוג מאושרת, בלי שאלה לנציג
    out = switch_alternative(SID, "CAR-10")
    assert out["ok"] and db.list_questions("open") == []
    assert db.load_state(SID).target.participation_status == "confirmed"

    # החלפה לרכב אחר עם השתתפות ידועה -> חישוב סופי
    out = switch_alternative(SID, "CAR-11", new_participation=300)
    assert out["ok"] and "הפרש" in out["message"]
    assert db.load_messages(SID)[-1]["source"] == "screen"

    # רכב מקבוצה גבוהה נדחה בלי לגעת בתוצאה הקודמת
    before = db.load_state(SID).last_result
    out = switch_alternative(SID, "CAR-14", new_participation=500)
    assert out["ok"] is False and "לא זמינה" in out["message"]
    assert db.load_state(SID).last_result == before
    assert recalculate(SID)["ok"]


def test_switch_to_waiver_requires_occupation():
    from actions import alternative_options, switch_alternative
    fill_script_employee()
    set_corolla_cross_today()
    assert "ויתור" not in {o["choice"] for o in alternative_options(SID)}
    assert switch_alternative(SID, "ויתור")["ok"] is False
    update_employee_data(SID, occupation="מנהל/ת פיתוח")
    assert "ויתור" in {o["choice"] for o in alternative_options(SID)}
    assert switch_alternative(SID, "ויתור")["ok"] is True


def test_question_stays_tied_to_its_car_and_does_not_block_other_alternatives():
    fill_family_employee()
    set_corolla_cross_today()
    verify_budget_and_eligibility(SID, "CAR-19")
    update_employee_data(SID, new_participation=350)             # חריגה מהקטלוג: שאלה על הרכב הנוכחי
    assert db.load_state(SID).pending_inquiry_car == {"new_participation": "CAR-19"}

    verify_budget_and_eligibility(SID, "CAR-20")                 # העובד עבר לרכב אחר: השאלה הישנה מתבטלת
    assert db.list_questions("open") == []
    assert db.load_state(SID).pending_inquiry_car == {}
    update_employee_data(SID, new_participation=350)             # חריגה גם ברכב החדש: שאלה חדשה עליו
    assert db.load_state(SID).pending_inquiry_car == {"new_participation": "CAR-20"}
    assert len(db.list_questions("open")) == 1


def test_representative_answer_computes_on_inquiry_car_after_waiver_check():
    """הבאג שדווח: שאלה על רכב, ביקשו בינתיים לבדוק ויתור, והתשובה חושבה על רכב אחר."""
    from actions import switch_alternative
    fill_script_employee()
    update_employee_data(SID, occupation="מנהל/ת פיתוח")
    set_corolla_cross_today()
    verify_budget_and_eligibility(SID, "CAR-10")                 # ATTO 3
    request_representative_info(SID, "new_participation", "ATTO 3", True)
    assert switch_alternative(SID, "ויתור")["ok"] is True        # בדיקת ויתור בזמן ההמתנה
    assert db.load_state(SID).target.kind == "waiver"
    assert len(db.list_questions("open")) == 1                   # השאלה לא בוטלה
    assert calculate_net_salary(SID)["ok"] is True               # ואפשר לחשב ויתור בזמן ההמתנה

    db.answer_question(db.list_questions("open")[0]["id"], 50.0, None, None)
    events = apply_representative_answers(SID)
    state = db.load_state(SID)
    assert state.target.catalog_id == "CAR-10" and state.target.participation == 50.0
    recalculated = next(e["recalculated"] for e in events if "recalculated" in e)
    assert recalculated["alternative"]["label"].startswith("ATTO 3")


def test_employee_supplying_value_cancels_question():
    fill_family_employee()
    set_corolla_cross_today()
    verify_budget_and_eligibility(SID, "CAR-19")
    update_employee_data(SID, new_participation=350)             # חריגה: נשאלת שאלה לנציג
    assert len(db.list_questions("open")) == 1
    update_employee_data(SID, new_participation=500)             # העובד מסר את הערך הרשמי: השאלה מיותרת
    assert db.list_questions("open") == []
    assert calculate_net_salary(SID)["data"]["status"] == "final"


def test_changing_alternative_recalculates_once_data_is_complete():
    fill_family_employee()
    set_corolla_cross_today()
    verify_budget_and_eligibility(SID, "CAR-19")
    update_employee_data(SID, new_participation=350)             # חריגה מהקטלוג על הרכב הראשון
    calculate_net_salary(SID)

    v = verify_budget_and_eligibility(SID, "CAR-20")             # חלופה אחרת: ברירת המחדל שלה מאושרת, החישוב מיד
    assert v["data"]["comparison"]["status"] == "final"
    assert "representative_question_sent" not in v["data"]
    assert v["data"]["comparison"]["alternative"]["label"].startswith("SANTA FE")


def test_current_situation_change_recalculates():
    fill_script_employee()
    set_corolla_cross_today()
    verify_budget_and_eligibility(SID, "CAR-11")
    update_employee_data(SID, new_participation=300)
    before = calculate_net_salary(SID)["data"]["net_today"]
    r = set_current_situation(SID, "car", candidate_number=1, participation=100)
    assert r["data"]["comparison"]["net_today"] == before + 200  # משתתף 200 ₪ פחות היום -> נטו היום גבוה ב-200 ₪


# ------------------------------------------------------------ פנייה לנציג רק אחרי ששאלו את העובד

def test_representative_requires_employee_confirmation_for_known_fields():
    fill_family_employee()
    set_corolla_cross_today()
    verify_budget_and_eligibility(SID, "CAR-19")
    r = request_representative_info(SID, "current_participation", "x")
    assert r["ok"] is False and "employee_confirmed_unknown" in r["next_step"]
    assert db.list_questions("open") == []                       # לא נפתחה שאלה
    assert request_representative_info(SID, "current_participation", "x", employee_confirmed_unknown=True)["ok"]
    # נתון שהעובד לא יכול לדעת - אין צורך באישור
    assert request_representative_info(SID, "current_car_usage_value", "x")["ok"]


def test_credit_points_limit_is_100():
    assert update_employee_data(SID, credit_points=100)["ok"]
    assert db.load_state(SID).employee.credit_points == 100
    r = update_employee_data(SID, credit_points=101)
    assert r["ok"] is False and "0 עד 100" in r["error"]
    assert db.load_state(SID).employee.credit_points == 100      # הערך השגוי לא דרס את התקין
    assert update_employee_data(SID, credit_points=0)["ok"]      # 0 חוקי


# ------------------------------------------------------------ בורר הרכבים: הכלי show_catalog והחלפת חלופה

def test_show_catalog_opens_picker_and_choice_closes_it():
    update_employee_data(SID, eligibility_group="ג")
    set_current_situation(SID, "none")                                  # קודם המצב היום, אחר כך חלופות
    assert db.load_state(SID).picker_open is False
    r = show_catalog(SID)
    assert r["ok"] and db.load_state(SID).picker_open is True
    assert "בורר" in r["next_step"] and "אל תרשום" in r["next_step"]
    verify_budget_and_eligibility(SID, "CAR-10")
    assert db.load_state(SID).picker_open is False                       # נבחרה חלופה - הבורר נסגר


def test_show_catalog_requires_current_situation_first():
    update_employee_data(SID, eligibility_group="ג")
    r = show_catalog(SID)
    assert r["ok"] is False and "רכב נוכחי" in r["missing"][0]
    assert db.load_state(SID).picker_open is False
    find_current_car(SID, "NIRO", 2025)
    set_current_situation(SID, "car", candidate_number=1)                # רכב בלי השתתפות - עדיין חסר
    assert show_catalog(SID)["ok"] is False
    set_current_situation(SID, "car", candidate_number=1, participation=250)
    assert show_catalog(SID)["ok"] is True


def test_verify_accepts_partial_name_and_enforces_group():
    update_employee_data(SID, eligibility_group="ג")
    r = verify_budget_and_eligibility(SID, "BYD DOLPHIN")                # שם חלקי, בלי מזהה
    assert r["ok"] and r["data"]["eligible"] is True
    assert db.load_state(SID).target.catalog_id == "CAR-03"
    r = verify_budget_and_eligibility(SID, "MODEL 3")                    # קבוצה ד' לעובד בקבוצה ג'
    assert r["data"]["eligible"] is False and "גבוהה" in r["data"]["reason"]
    assert verify_budget_and_eligibility(SID, "TOYOTA")["ok"] is False   # כמה התאמות - שואלים את העובד
    assert verify_budget_and_eligibility(SID, "ZZZ")["ok"] is False


def test_name_matches_is_partial_and_order_free():
    from usage_value_db import name_matches
    assert name_matches("BYD DOLPHIN", "בי ווי די", "BYD DOLPHIN")
    assert name_matches("בי ווי די BYD DOLPHIN", "בי ווי די", "BYD DOLPHIN")
    assert name_matches("dolphin", "בי ווי די", "BYD DOLPHIN")
    assert name_matches("מזדה 3", "מזדה יפן", "MAZDA 3")
    assert not name_matches("מזדה 3", "מזדה תאילנד", "MAZDA CX-30")
    assert not name_matches("", "x", "y")


def test_occupation_sets_eligibility_group_automatically():
    r = update_employee_data(SID, occupation="מנהל/ת אזור מכירות")        # בטבלה: קבוצה ג
    assert r["ok"] and db.load_state(SID).employee.eligibility_group == "ג"
    assert "קבוצת זכאות (לפי העיסוק)" in r["data"]["updated"]


def test_occupation_matched_loosely_and_conflict_reported():
    update_employee_data(SID, eligibility_group="א")
    r = update_employee_data(SID, occupation="מנהל אזור מכירות")           # בלי "/ת"
    assert r["ok"] and db.load_state(SID).employee.occupation == "מנהל/ת אזור מכירות"
    assert db.load_state(SID).employee.eligibility_group == "א"            # קבוצה שהעובד מסר לא נדרסת
    assert "group_conflict" in r["data"]                                    # אבל מדווחים על הסתירה


def test_unknown_occupation_is_not_guessed():
    r = update_employee_data(SID, occupation="אסטרונאוט")
    assert r["ok"] is False and db.load_state(SID).employee.eligibility_group is None


def test_family_track_counts_ages_in_code_and_explains_age_and_budget():
    update_employee_data(SID, eligibility_group="ב", children_ages=[3, 9, 15, 24])   # אחד בן 24 - לא נספר
    assert db.load_state(SID).employee.children_under_23 == 3
    r = verify_budget_and_eligibility(SID, "CAR-19")                      # קרניבל, 6,000 ₪
    reason = r["data"]["reason"]
    assert r["data"]["eligible"] is False
    assert "24" in reason and "מגבלת הגיל" in reason                      # הסבר מפורש לגיל
    assert "6,000" in reason and "3,700" in reason and "חורגת מתקציב" in reason   # והסבר לחריגה מהתקציב
    assert db.load_state(SID).target.kind is None                          # לא נשמר כחלופה
    update_employee_data(SID, children_ages=[3, 9, 15, 23])
    assert verify_budget_and_eligibility(SID, "CAR-19")["data"]["eligible"] is True   # בן 23 נספר


def test_exact_car_name_is_saved_immediately_without_second_confirmation():
    r = find_current_car(SID, "COROLLA CROSS", 2025)
    assert r["data"]["auto_confirmed"] is True
    assert db.load_state(SID).current.label == "COROLLA CROSS (2025)"
    assert "אל תשאל שוב" in r["next_step"]
    r = find_current_car(SID, "טויוטה יפן COROLLA CROSS (2025)", None)   # גם השם המלא כפי שהוצג לעובדת
    assert r["data"].get("auto_confirmed") is True


def test_partial_car_name_still_asks_for_confirmation():
    r = find_current_car(SID, "cross", 2025)
    assert r["ok"] and "auto_confirmed" not in r["data"]
    assert db.load_state(SID).current.kind is None                    # ממתין לאישור העובד


def test_group_and_occupation_conflict_in_same_call_is_reported_not_overwritten():
    r = update_employee_data(SID, eligibility_group="ד", occupation="מפתח/ת תוכנה")   # בטבלה: קבוצה ב
    assert r["ok"] and "group_conflict" in r["data"]
    emp = db.load_state(SID).employee
    assert emp.eligibility_group == "ד" and emp.group_conflict            # הקבוצה של העובדת לא נדרסה
    assert "סתירה" in r["next_step"]
    r = update_employee_data(SID, eligibility_group="ב")                  # העובדת הבהירה: ב
    emp = db.load_state(SID).employee
    assert emp.eligibility_group == "ב" and emp.group_conflict is None and "group_conflict" not in r["data"]


def test_group_stated_after_occupation_is_flagged_too():
    update_employee_data(SID, occupation="מפתח/ת תוכנה")                  # הקבוצה נקבעת לפי העיסוק: ב
    r = update_employee_data(SID, eligibility_group="ד")
    assert "group_conflict" in r["data"] and db.load_state(SID).employee.eligibility_group == "ד"
    r = update_employee_data(SID, eligibility_group="ד")                  # העובדת מאשרת שוב - הסתירה נסגרת
    assert db.load_state(SID).employee.group_conflict is None


def test_waiver_request_never_opens_the_picker():
    from actions import picker_state
    fill_script_employee()
    update_employee_data(SID, occupation="מנהל/ת פיתוח")
    set_corolla_cross_today()
    messages = db.load_messages(SID) or []
    messages.append({"role": "user", "content": "אני רוצה לוותר על הרכב"})
    db.save_messages(SID, messages)
    assert verify_budget_and_eligibility(SID, "ויתור")["ok"]
    r = show_catalog(SID)                                              # גם אם הסוכן ינסה
    assert r["ok"] is False and "ויתור" in r["error"]
    assert db.load_state(SID).picker_open is False
    picker = picker_state(SID)
    assert picker["visible"] is False and picker["can_reopen"] is False   # וגם אין כפתור "בחירת רכב אחר"


def test_show_catalog_requires_group():
    r = show_catalog(SID)
    assert r["ok"] is False and "קבוצת זכאות" in r["missing"]
    assert db.load_state(SID).picker_open is False


def test_picker_state_and_helpers():
    from actions import alternative_options, pick_message, picker_state, set_picker
    assert picker_state(SID) is None                                     # הקבוצה עוד לא ידועה
    update_employee_data(SID, eligibility_group="א")
    assert picker_state(SID) is None                                     # המצב היום עוד לא ידוע - אין חלופות
    set_current_situation(SID, "none")
    picker = picker_state(SID)
    assert picker["visible"] and not picker["closable"] and not picker["as_table"] and len(picker["cars"]) == 4
    alternative_options(SID)                                             # קריאה למסך לא פותחת את הבורר ולא משנה מצב
    assert db.load_state(SID).picker_open is False

    update_employee_data(SID, eligibility_group="ג")
    assert picker_state(SID)["as_table"]                                 # 13 רכבים - טבלה
    verify_budget_and_eligibility(SID, "CAR-10")
    assert picker_state(SID)["visible"] is False and picker_state(SID)["closable"] is True
    set_picker(SID, True)
    assert picker_state(SID)["visible"] is True

    cars = picker_state(SID)["cars"]
    assert pick_message("CAR-10", cars) == "אני רוצה לבדוק את בי ווי די ATTO 3 (2025) (מזהה CAR-10)"
    assert pick_message("ויתור", cars) == "אני רוצה לוותר על הרכב"


def test_waiting_without_any_question_at_the_representative_is_cleared():
    """המצב מחכה לנציג אבל אין שאלה פתוחה (למשל בוטלה) - העובד לא נשאר עם "ממתין" לנצח."""
    update_employee_data(SID, eligibility_group="ג")
    state = db.load_state(SID)
    state.pending_questions = {"new_participation": 99}              # שאלה שלא קיימת בנציג
    state.waiting_for_representative = True
    db.save_state(state)
    apply_representative_answers(SID)
    state = db.load_state(SID)
    assert state.pending_questions == {} and state.waiting_for_representative is False


@pytest.fixture(autouse=True)
def employee_said_unknown(temp_db):
    """רוב הבדיקות מדמות עובד שאמר שאינו יודע (הכלי בודק זאת בהודעות העובד); ראו test_representative_*."""
    db.save_messages(SID, [{"role": "assistant", "content": "מה ההשתתפות שלך?"},
                           {"role": "user", "content": "אני לא יודע"}])


def test_representative_flag_alone_is_not_enough_without_employee_saying_unknown():
    fill_family_employee()
    set_corolla_cross_today()
    verify_budget_and_eligibility(SID, "CAR-19")
    db.save_messages(SID, [{"role": "user", "content": "תחשב לי את הנטו"}])          # העובד לא אמר שאינו יודע
    r = request_representative_info(SID, "current_participation", "x", employee_confirmed_unknown=True)
    assert r["ok"] is False and db.list_questions("open") == []
    db.append_messages(SID, [{"role": "user", "content": "אני לא יודע כמה אני משתתף, תשאל את הנציג"}])
    assert request_representative_info(SID, "current_participation", "x", employee_confirmed_unknown=True)["ok"]


# ---------------------------------------------------------------- הגנה: רק מספרים שהעובד כתב

def _model_update(**values):
    from agent_tools_registry import model_function
    return model_function("update_employee_data")(SID, **values)


def test_model_cannot_save_numbers_the_employee_never_wrote():
    db.append_messages(SID, [{"role": "user", "content": "12K 7 89"}])
    result = _model_update(base_salary=12000, pension_rate=6, study_fund_rate=2.5, credit_points=2.25)
    assert result["ok"] is False and "לא נאמר" in result["error"]
    emp = db.load_state(SID).employee
    assert emp.base_salary == 12000                                   # "12K" נאמר
    assert emp.pension_rate is None and emp.study_fund_rate is None and emp.credit_points is None


def test_model_may_save_numbers_in_any_written_form():
    db.append_messages(SID, [{"role": "user", "content": "שכר 14,200, עמלות 1.000, פנסיה 6%, קה״ש 2,5 ונקודות 2.25"}])
    assert _model_update(base_salary=14200, commissions=1000, pension_rate=6, study_fund_rate=2.5, credit_points=2.25)["ok"]
    db.append_messages(SID, [{"role": "user", "content": "בעצם 15 אלף"}])
    assert _model_update(base_salary=15000)["ok"]
    assert _model_update(bonus=0)["ok"]                               # "אין" = 0 תמיד מותר


def test_representative_answers_eligibility_group():
    update_employee_data(SID, occupation="מנהל/ת מחלקה")
    db.append_messages(SID, [{"role": "user", "content": "לא יודע לאיזו קבוצה אני שייך, תשאל את הנציג"}])
    assert request_representative_info(SID, "eligibility_group", "העיסוק לא בטבלה", True)["ok"]
    question = db.list_questions("open")[0]
    db.answer_question(question["id"], None, None, "ב", answered_by="rep")
    events = apply_representative_answers(SID)
    assert events and db.load_state(SID).employee.eligibility_group == "ב"
    assert db.load_state(SID).employee.group_source == "representative"


def test_free_text_representative_answer_goes_to_the_bot_not_to_the_employee():
    from agent import check_representative_updates
    db.append_messages(SID, [{"role": "user", "content": "לא יודע"}])
    assert request_representative_info(SID, "other", "שאלה כלשהי", True)["ok"]
    db.answer_question(db.list_questions("open")[0]["id"], None, None, "t", answered_by="rep")
    check_representative_updates(SID)
    last = db.load_messages(SID)[-1]
    assert last["role"] == "user" and last["hidden"] and "t" in last["content"]      # הודעת מערכת נסתרת: הבוט יגיב לעובד
    assert not any(m["role"] == "assistant" and m.get("source") == "representative" for m in db.load_messages(SID))


def test_unknown_occupation_error_never_lists_the_whole_table():
    result = update_employee_data(SID, occupation="מנהל מכירות")
    assert result["ok"] is False
    assert "אסור להציג" in result["error"] and "מפתח/ת תוכנה" not in result["error"]    # רק קרובים, לא כל הרשימה
    assert "מנהל/ת אזור מכירות" in result["error"]


def test_session_topic_is_focused():
    from db import _topic
    assert _topic(db.load_state(SID)) is None
    update_employee_data(SID, eligibility_group="ג")
    assert _topic(db.load_state(SID)) == "בדיקת נטו · קבוצה ג"
    fill_script_employee()
    set_corolla_cross_today()
    verify_budget_and_eligibility(SID, "CAR-10")
    assert _topic(db.load_state(SID)).startswith("מ-COROLLA CROSS") and "ATTO 3" in _topic(db.load_state(SID))
    update_employee_data(SID, eligibility_group="ב", occupation="מפתח/ת תוכנה")
    from actions import switch_alternative
    assert switch_alternative(SID, "ויתור")["ok"]
    assert _topic(db.load_state(SID)) == "ויתור על רכב · מפתח/ת תוכנה"


def test_absurd_salary_is_rejected_when_typed_not_only_at_calculation():
    for field in ("base_salary", "commissions", "bonus"):
        r = update_employee_data(SID, **{field: 55_000_000})
        assert r["ok"] is False and "גבוה מדי" in r["error"]
    emp = db.load_state(SID).employee
    assert emp.base_salary is None and emp.commissions is None and emp.bonus is None
    assert update_employee_data(SID, base_salary=1_000_000)["ok"]            # הגבול עצמו מותר


def test_feminine_forms_match_table_occupations():
    from data_store import find_occupation
    for typed, expected in (("מפתחת תוכנה", "מפתח/ת תוכנה"), ("מנהלת מחלקה", "מנהל/ת מחלקה"), ("טכנאית שירות", "טכנאי/ת שירות"),
                            ("אנליסטית נתונים", "אנליסט/ית נתונים"), ("מפקחת איכות במפעל", "מפקח/ת איכות במפעל"),
                            ("נציגת מכירות שטח", "נציג/ת מכירות שטח"), ("מפתח תוכנה", "מפתח/ת תוכנה")):
        assert find_occupation(typed)["occupation"] == expected, typed
    assert find_occupation("מנקה") is None and find_occupation("מנהל") is None        # לא מנחשים: חלקי/לא קיים


def test_find_occupation_tolerates_definite_article_and_extra_words():
    from data_store import find_occupation
    assert find_occupation("רכזת הרכש")["occupation"] == "רכז/ת רכש"
    assert find_occupation("רכזת רכש וקניות")["occupation"] == "רכז/ת רכש"
    assert find_occupation("רכזת") is None and find_occupation("מנהלת רכש") is None


def test_word_representative_alone_is_not_a_request_to_ask_the_rep():
    import agent_tools
    assert not agent_tools._UNKNOWN_RE.search("הנציג אמר לי שזה 300")
    assert agent_tools._UNKNOWN_RE.search("תשאל את הנציג") and agent_tools._UNKNOWN_RE.search("אני לא יודע")


def test_english_make_name_finds_the_hebrew_listing():
    import agent_tools
    r = agent_tools.find_current_car("alias1", "Jaguar", 2010)
    assert r["ok"] and r["data"]["candidates"]
    assert agent_tools.find_current_car("alias2", "jaguar XF", 2011)["ok"]


def test_rejected_eligibility_check_does_not_reopen_the_picker():
    from actions import picker_state, set_picker
    update_employee_data(SID, eligibility_group="א")
    set_current_situation(SID, "none")
    assert picker_state(SID)["visible"]                                  # לפני שביקש רכב מסוים: הבורר פתוח כרגיל
    result = verify_budget_and_eligibility(SID, "CAR-10")                # רכב מקבוצה ג - נדחה (או חסר נתון)
    assert db.load_state(SID).target.kind is None
    picker = picker_state(SID)
    assert picker["visible"] is False and picker["can_reopen"] is True   # לא נפתח מעצמו, אפשר לפתוח בכפתור
    set_picker(SID, True)
    assert picker_state(SID)["visible"] is True


def test_breakdown_rows_sum_to_the_net_and_cite_the_rules():
    from data_store import get_rules
    from explain_calc import breakdown, check
    update_employee_data(SID, eligibility_group="ג", base_salary=18500, commissions=0, bonus=0, pension_rate=6,
                         study_fund_rate=2.5, credit_points=2.25)
    set_current_situation(SID, "none")
    verify_budget_and_eligibility(SID, "CAR-10")
    calculate_net_salary(SID)
    result = db.load_state(SID).last_result
    assert check(result)["ok"] is True                       # הפירוט מסתכם לנטו (עד עיגול)
    rows = {r["key"]: r for r in breakdown(result, get_rules())}
    assert rows["net"]["today"] == result["net_today"] and rows["net"]["alt"] == result["net_alternative"]
    assert "242" in rows["credit_points_value"]["rule"]            # כל שורה מצביעה על הכלל (בעברית) עם הערך מהקובץ
    assert "מדרגות" in rows["tax_before_credits"]["rule"]
    assert not any(ch.isascii() and ch.isalpha() for r in rows.values() for ch in r["rule"] + r["label"])   # הכול בעברית


def test_data_sources_are_tracked_per_field():
    update_employee_data(SID, base_salary=15000, pension_rate=6)
    from payslip import apply_payslip
    apply_payslip(SID, {"commissions": 1000.0})
    state = db.load_state(SID)
    assert state.sources["base_salary"] == "employee" and state.sources["commissions"] == "payslip"


# ------------------------------------------------------------ הפרש משוער לפני שהושלמו כל הנתונים

def _fill_until_pension():
    """הכול חוץ מאחוזי פנסיה וקרן השתלמות."""
    update_employee_data(SID, eligibility_group="ג", base_salary=14200, commissions=1000, bonus=0, credit_points=2.25)
    find_current_car(SID, "corolla cross", 2025)
    set_current_situation(SID, "car", candidate_number=1)
    update_employee_data(SID, current_participation=300)
    verify_budget_and_eligibility(SID, "CAR-10")


def test_preview_difference_matches_final_diff_when_only_pension_fields_missing():
    from agent_tools import preview_difference
    _fill_until_pension()
    preview = preview_difference(SID)
    assert preview["ok"] and preview["data"]["status"] == "preview"
    assert "net_today" not in preview["data"] and "net_alternative" not in preview["data"]    # נטו מוחלט לא מוצג
    update_employee_data(SID, pension_rate=6, study_fund_rate=2.5)
    final = calculate_net_salary(SID)["data"]
    assert preview["data"]["diff"] == final["diff"]


def test_preview_difference_refuses_when_income_or_choice_is_missing():
    from agent_tools import preview_difference
    update_employee_data(SID, eligibility_group="ג", base_salary=14200)          # חסרים עמלות/בונוס, רכב, חלופה
    result = preview_difference(SID)
    assert result["ok"] is False and result["data"] == {}


def test_preview_difference_refuses_when_nothing_is_missing():
    from agent_tools import preview_difference
    fill_script_employee()
    set_corolla_cross_today()
    verify_budget_and_eligibility(SID, "CAR-10")
    assert preview_difference(SID)["ok"] is False                                 # יש הכול: משתמשים ב-calculate_net_salary


def test_rep_answer_without_amount_resolves_provisional_participation():
    from agent_tools import apply_representative_answers
    fill_script_employee()
    set_corolla_cross_today()
    verify_budget_and_eligibility(SID, "CAR-10")
    update_employee_data(SID, new_participation=100)                  # חריגה מהקטלוג (300): ממתין לנציג
    qid = db.list_questions("open")[0]["id"]
    db.answer_question(qid, None, None, "אין סכום מיוחד")             # הנציג לא מאשר חריגה
    events = apply_representative_answers(SID)
    state = db.load_state(SID)
    assert state.target.participation == 300 and state.target.participation_status == "confirmed"
    assert not state.pending_questions
    assert events[-1]["recalculated"]["status"] == "final"


# ------------------------------------------------------------ דירוג החלופות לפי נטו

def test_best_alternatives_ranks_by_net_and_matches_a_direct_comparison():
    from agent_tools import best_alternatives
    fill_script_employee()
    set_corolla_cross_today()
    ranking = best_alternatives(SID)
    assert ranking["ok"], ranking
    top = ranking["data"]["top"]
    nets = [o["net_alternative"] for o in top]
    assert len(top) == 3 and nets == sorted(nets, reverse=True)
    assert all(o["choice"] not in ("CAR-14", "CAR-15") for o in top)                   # קבוצה ד אינה זמינה לקבוצה ג
    best = top[0]["choice"]
    verify_budget_and_eligibility(SID, best)                                         # אותה חלופה, חישוב רגיל
    direct = calculate_net_salary(SID)["data"]
    assert direct["net_alternative"] == top[0]["net_alternative"] and direct["diff"] == top[0]["diff"]


def test_best_alternatives_excludes_current_car_and_notes_net_only_basis():
    from agent_tools import best_alternatives
    fill_script_employee()
    set_corolla_cross_today()
    ranking = best_alternatives(SID)["data"]
    current_id = db.load_state(SID).current.catalog_id
    assert all(o["choice"] != current_id for o in ranking["top"])
    assert any("נטו" in note for note in ranking["notes"])


def test_best_alternatives_refuses_when_data_is_missing():
    from agent_tools import best_alternatives
    update_employee_data(SID, eligibility_group="ג", base_salary=14200)
    result = best_alternatives(SID)
    assert result["ok"] is False and result["data"] == {} and result["missing"]


def test_best_alternatives_includes_waiver_only_when_the_occupation_allows():
    from agent_tools import best_alternatives
    fill_script_employee()
    set_corolla_cross_today()
    assert all(o["choice"] != "ויתור" for o in best_alternatives(SID)["data"]["top"])
    update_employee_data(SID, occupation="מנהל/ת פיתוח")
    ranking = best_alternatives(SID)["data"]
    assert ranking["checked"] >= 1 and any(o["choice"] == "ויתור" for o in ranking["top"]) or ranking["checked"] > 3


def test_best_alternatives_returns_precomputed_gaps_between_options():
    from agent_tools import best_alternatives
    fill_script_employee()
    set_corolla_cross_today()
    data = best_alternatives(SID)["data"]
    top = data["top"]
    assert len(data["gaps_between_options"]) == 3                                   # שלוש זוגות מתוך שלוש חלופות
    first = data["gaps_between_options"][0]
    assert first["gap"] == top[0]["net_alternative"] - top[1]["net_alternative"] >= 0


# ------------------------------------------------------------ מעבר לתרחיש אחר: הצעת איפוס

def test_offer_reset_only_marks_the_offer_and_keeps_the_data():
    from agent_tools import offer_reset
    fill_script_employee()
    assert offer_reset(SID)["ok"]
    state = db.load_state(SID)
    assert state.reset_offer and state.employee.base_salary == 14200


def test_reset_data_clears_the_state_and_cancels_open_questions():
    from actions import reset_data
    from agent_tools import offer_reset
    fill_family_employee()
    db.save_messages(SID, [{"role": "user", "content": "שכר 14200"}, {"role": "assistant", "content": "בסדר"}])
    offer_reset(SID)
    reset_data(SID)
    state = db.load_state(SID)
    assert state.employee.base_salary is None and not state.reset_offer and not state.pending_questions
    messages = db.load_messages(SID)
    assert len(messages) == 3 and messages[-1].get("reset")                 # ההודעות נשארות, ובסוף הודעת האיפוס
    assert [m["content"] for m in db.since_reset(messages)] == [messages[-1]["content"]]


def test_numbers_before_a_reset_are_no_longer_evidence():
    from actions import reset_data
    from agent_tools import stated_numbers
    db.save_messages(SID, [{"role": "user", "content": "שכר 14200"}])
    assert 14200 in stated_numbers(SID)
    reset_data(SID)
    assert 14200 not in stated_numbers(SID)
