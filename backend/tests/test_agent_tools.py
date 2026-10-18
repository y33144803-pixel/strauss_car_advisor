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
    assert "השתתפות עובד ברכב החדש" in v["missing"]

    # לפני שיש השתתפות - החישוב מסרב
    assert calculate_net_salary(SID)["ok"] is False

    # העובד לא יודע את ההשתתפות -> שאלה לנציג + השוואת ביניים
    q = request_representative_info(SID, "new_participation", "השתתפות עובד ב-ATTO 3 לעובד קבוצה ג", True)
    assert q["ok"]
    provisional = q["data"]["provisional_comparison"]
    assert provisional["status"] == "provisional"
    assert provisional["alternative"]["participation"] == 300   # ברירת המחדל מהקטלוג
    assert db.load_state(SID).waiting_for_representative

    # המסך של הנציג רואה את השאלה ועונה
    open_qs = db.list_questions("open")
    assert len(open_qs) == 1 and open_qs[0]["session_id"] == SID
    db.answer_question(open_qs[0]["id"], 350, effective_from="2027-01-01")

    # השיחה ממשיכה מאותה נקודה
    events = apply_representative_answers(SID)
    final = events[-1]["recalculated"]
    assert final["status"] == "final"
    assert final["alternative"]["participation"] == 350
    assert final["diff"] == provisional["diff"] - 50
    state = db.load_state(SID)
    assert not state.waiting_for_representative
    assert state.target.participation_effective_from == "2027-01-01"
    assert apply_representative_answers(SID) == []        # לא קולטים פעמיים


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


def test_unknown_occupation_rejected_with_list():
    r = update_employee_data(SID, occupation="אסטרונאוט")
    assert r["ok"] is False and "מפתח/ת תוכנה" in r["error"]


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
    fill_script_employee()
    set_corolla_cross_today()
    verify_budget_and_eligibility(SID, "CAR-11")
    update_employee_data(SID, new_participation=300)
    verify_budget_and_eligibility(SID, "CAR-10")            # רכב אחר
    assert db.load_state(SID).target.participation is None


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

    # השתתפות לא ידועה -> שאלה לנציג והשוואת ביניים
    out = switch_alternative(SID, "CAR-10", ask_representative=True)
    assert out["ok"] and "ביניים" in out["message"] and db.list_questions("open")

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
    fill_script_employee()
    set_corolla_cross_today()
    verify_budget_and_eligibility(SID, "CAR-10")
    request_representative_info(SID, "new_participation", "ATTO 3", True)
    assert db.load_state(SID).pending_inquiry_car == {"new_participation": "CAR-10"}

    verify_budget_and_eligibility(SID, "CAR-11")                 # העובד בדק בינתיים רכב אחר
    assert len(db.list_questions("open")) == 1                   # השאלה על ה-ATTO 3 נשארת
    update_employee_data(SID, new_participation=300)             # והוא מסר השתתפות ל-EV3
    assert calculate_net_salary(SID)["data"]["status"] == "final"   # השאלה על רכב אחר לא מעכבת

    q = db.list_questions("open")[0]
    db.answer_question(q["id"], 50.0, None, None)
    events = apply_representative_answers(SID)                   # התשובה מחושבת על ה-ATTO 3
    state = db.load_state(SID)
    assert state.target.catalog_id == "CAR-10" and state.target.participation == 50.0
    assert events[0]["car"].startswith("ATTO 3")
    assert state.pending_inquiry_car == {} and not state.waiting_for_representative


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
    fill_script_employee()
    set_corolla_cross_today()
    verify_budget_and_eligibility(SID, "CAR-10")
    request_representative_info(SID, "new_participation", "ATTO 3", True)
    update_employee_data(SID, new_participation=320)
    assert db.list_questions("open") == []
    assert calculate_net_salary(SID)["data"]["status"] == "final"


# ------------------------------------------------------------ רכב נוכחי ממסלול ברוכת ילדים

def test_current_family_car_includes_employee_share_today():
    update_employee_data(SID, eligibility_group="ב", base_salary=9800, commissions=2400, bonus=0,
                         pension_rate=6, study_fund_rate=0, credit_points=2.25, children_under_23=5)
    find_current_car(SID, "CARNIVAL", 2025)
    assert set_current_situation(SID, "car", candidate_number=1, participation=500)["ok"]
    verify_budget_and_eligibility(SID, "CAR-05")                 # NIRO
    update_employee_data(SID, new_participation=250)
    data = calculate_net_salary(SID)["data"]
    # הפוך מהדוגמה E-002 באקסל (נירו -> קרניבל: 8,600 -> 5,078)
    assert data["net_today"] == 5078 and data["net_alternative"] == 8600
    assert data["today"]["family_employee_share"] == 1150
    assert data["alternative"]["family_employee_share"] == 0


# ------------------------------------------------------------ חישוב מחדש אוטומטי בקוד

def test_no_auto_calculation_before_first_result():
    fill_script_employee()
    set_corolla_cross_today()
    verify_budget_and_eligibility(SID, "CAR-11")
    r = update_employee_data(SID, new_participation=300)
    assert "comparison" not in r["data"]                         # עדיין לא הייתה תוצאה - המודל מפעיל חישוב ראשון
    assert db.load_state(SID).last_result is None


def test_changing_alternative_recalculates_once_data_is_complete():
    fill_script_employee()
    set_corolla_cross_today()
    verify_budget_and_eligibility(SID, "CAR-11")
    update_employee_data(SID, new_participation=300)
    calculate_net_salary(SID)

    v = verify_budget_and_eligibility(SID, "CAR-10")             # חלופה אחרת - ההשתתפות עוד חסרה
    assert "comparison" not in v["data"] and db.load_state(SID).result_is_stale
    r = update_employee_data(SID, new_participation=300)         # עכשיו הכול קיים
    assert r["data"]["comparison"]["alternative"]["label"].startswith("ATTO 3")
    assert not db.load_state(SID).result_is_stale


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
    fill_script_employee()
    set_corolla_cross_today()
    verify_budget_and_eligibility(SID, "CAR-10")
    r = request_representative_info(SID, "new_participation", "x")
    assert r["ok"] is False and "employee_confirmed_unknown" in r["next_step"]
    assert db.list_questions("open") == []                       # לא נפתחה שאלה
    assert request_representative_info(SID, "new_participation", "x", employee_confirmed_unknown=True)["ok"]
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
