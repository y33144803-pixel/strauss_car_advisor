"""כלי הסוכן - פונקציות פייתון רגילות, ללא תלות במודל השפה.

כל כלי טוען את מצב השיחה מ-SQLite, פועל עליו, שומר ומחזיר מבנה אחיד:
    {"ok": bool, "data": {...}, "error": str|None, "missing": [...], "next_step": str|None}
התיאור שהסוכן רואה לכל כלי נמצא בנפרד ב-tool_specs.json.
"""
import re
from typing import Optional

import db
from calc_engine import MAX_CREDIT_POINTS, CarSituation, SalaryInput, compare, display_round
from data_store import find_car, find_car_by_name, find_occupation, get_catalog, get_occupations, get_rules
from eligibility import (is_family_car, check_car_choice, check_waiver, family_employee_share,
                         group_budget, lookup_catalog_usage_value)
from state import ConversationState, TargetChoice
from usage_value_db import get_usage_value as db_get_usage_value
from usage_value_db import search_by_name

WAIVER_CHOICE = "ויתור"
ELECTRIC_DRIVE = "חשמלי"

# שדות שאפשר לשאול עליהם את הנציג, והשפעת התשובה על המצב
REP_FIELDS = {
    "new_participation": "השתתפות עובד ברכב החדש",
    "current_participation": "השתתפות עובד ברכב הנוכחי",
    "current_car_usage_value": "שווי שימוש של הרכב הנוכחי",
    "waiver_maintenance": "אחזקת רכב בוויתור (עיסוק לא מופיע בטבלה)",
    "other": "אחר",
}


def _display_name(make_name: str, commercial_name: str, year) -> str:
    """השם המלא של הרכב להצגה, בדיוק כפי שהוא במאגר. הסוכן מונחה להעתיק אותו ולא לתרגם."""
    return f"{str(make_name).strip()} {str(commercial_name).strip()} ({year})"


# נתונים שהעובד אמור לדעת - קודם שואלים אותו, ורק אם אמר שאינו יודע פונים לנציג
ASK_EMPLOYEE_FIRST = {"new_participation", "current_participation"}


def _result(ok: bool, data: dict | None = None, error: str | None = None,
            missing: list | None = None, next_step: str | None = None) -> dict:
    return {"ok": ok, "data": data or {}, "error": error, "missing": missing or [], "next_step": next_step}


def _drive_name(drive_code: str) -> str:
    return get_rules()["valueOfUse"]["driveField"].get(str(drive_code), "לא ידוע")


def _cancel_pending(state: ConversationState, field: str) -> None:
    """שאלה פתוחה לנציג על שדה שנעשה לא רלוונטי - מבטלים אותה ומשחררים את ההמתנה."""
    question_id = state.pending_questions.pop(field, None)
    state.pending_inquiry_car.pop(field, None)
    if question_id is not None:
        db.cancel_question(question_id)
    state.waiting_for_representative = bool(state.pending_questions)


# ---------------------------------------------------------------- 1. עדכון נתונים

def update_employee_data(session_id: str,
                         eligibility_group: Optional[str] = None,
                         base_salary: Optional[float] = None,
                         commissions: Optional[float] = None,
                         bonus: Optional[float] = None,
                         pension_rate: Optional[float] = None,
                         study_fund_rate: Optional[float] = None,
                         credit_points: Optional[float] = None,
                         occupation: Optional[str] = None,
                         children_under_23: Optional[int] = None,
                         children_ages: Optional[list[int]] = None,
                         current_participation: Optional[float] = None,
                         new_participation: Optional[float] = None) -> dict:
    """שמירת נתונים שהעובד מסר (חדשים או תיקון). כל ערך נבדק לפני שמירה; ערך שגוי לא נשמר."""
    state = db.load_state(session_id)
    rules = get_rules()
    errors, updated, corrected = [], [], []

    def apply(target, field, value, label):
        old = getattr(target, field)
        if old is not None and old != value:
            show = lambda v: f"{v:g}" if isinstance(v, float) else str(v)  # 6.0 -> 6
            corrected.append(f"{label}: {show(old)} → {show(value)}")
        setattr(target, field, value)
        updated.append(label)

    emp = state.employee
    if eligibility_group is not None:
        group = eligibility_group.strip().replace("'", "").replace("׳", "")
        if group in rules["groupOrder"]:
            apply(emp, "eligibility_group", group, "קבוצת זכאות")
            emp.group_source = "employee"
        else:
            errors.append(f"קבוצת זכאות '{eligibility_group}' אינה קיימת. הקבוצות: {', '.join(rules['groupOrder'])}")
    if base_salary is not None:
        if base_salary > 0:
            apply(emp, "base_salary", float(base_salary), "שכר בסיס")
        else:
            errors.append("שכר הבסיס חייב להיות מספר חיובי")
    for value, field, label in ((commissions, "commissions", "עמלות"), (bonus, "bonus", "בונוס")):
        if value is not None:
            if value >= 0:
                apply(emp, field, float(value), label)
            else:
                errors.append(f"{label} לא יכולים להיות שליליים")
    for value, field, label in ((pension_rate, "pension_rate", "אחוז פנסיה"),
                                (study_fund_rate, "study_fund_rate", "אחוז קרן השתלמות")):
        if value is not None:
            if 0 <= value <= 100:
                apply(emp, field, float(value), label)
            else:
                errors.append(f"{label} {value} אינו הגיוני - יש להזין אחוז בין 0 ל-100 (למשל 6 עבור 6%)")
    if credit_points is not None:
        if 0 <= credit_points <= MAX_CREDIT_POINTS:
            apply(emp, "credit_points", float(credit_points), "נקודות זיכוי")
        else:
            errors.append(f"מספר נקודות הזיכוי {credit_points} אינו הגיוני (הטווח 0 עד {MAX_CREDIT_POINTS})")
    group_note = None
    if occupation is not None:
        names = [o["occupation"] for o in get_occupations()]
        occ = find_occupation(occupation)
        if occ is not None:
            apply(emp, "occupation", occ["occupation"], "עיסוק")
            # קבוצת הזכאות נקבעת לפי העיסוק בטבלה - לא שואלים את העובד (אלא אם כבר נמסרה קבוצה: ראו בדיקת סתירה)
            if emp.eligibility_group is None:
                apply(emp, "eligibility_group", occ["group"], "קבוצת זכאות (לפי העיסוק)")
                emp.group_source = "occupation"
        else:
            errors.append(f"העיסוק '{occupation}' לא נמצא בטבלת העיסוקים. העיסוקים המוכרים: {', '.join(names)}. "
                          "יש לאשר עם העובד איזה מהם מתאים, ואם אף אחד - לפנות לנציג")
    # סתירה בין הקבוצה לעיסוק: לא דורסים בשקט אף צד, אלא מחזירים חיווי לסוכן (עד שהעובד מבהיר)
    occ_record = find_occupation(emp.occupation) if emp.occupation else None
    if occ_record and emp.eligibility_group and occ_record["group"] != emp.eligibility_group:
        if eligibility_group is not None and occupation is None and emp.group_conflict:
            emp.group_conflict = None                  # העובד חזר ואישר את הקבוצה שלו אחרי שהסתירה הוצגה לו
        elif eligibility_group is not None or occupation is not None:
            group_note = emp.group_conflict = (
                f"העובד מסר קבוצה {emp.eligibility_group}, אבל לפי טבלת העיסוקים העיסוק '{occ_record['occupation']}' "
                f"שייך לקבוצה {occ_record['group']}. הקבוצה שנשמרה כרגע: {emp.eligibility_group}. "
                "שאל את העובד איזו נכונה, ושלח update_employee_data עם eligibility_group לפי תשובתו")
    else:
        emp.group_conflict = None
    if children_ages is not None:
        if len(children_ages) > 20:
            errors.append("מספר הילדים גדול מדי (עד 20)")
        elif all(0 <= age <= 120 for age in children_ages):
            max_age = rules["familyTrack"]["childMaxAge"]
            apply(emp, "children_ages", [int(a) for a in children_ages], "גילאי הילדים")
            # הספירה נעשית בקוד, לא במודל: רק ילדים עד הגיל המרבי נספרים למסלול
            children_under_23 = sum(1 for age in children_ages if age <= max_age)
        else:
            errors.append("גיל ילד חייב להיות בין 0 ל-120")
    if children_under_23 is not None:
        if children_under_23 >= 0:
            apply(emp, "children_under_23", int(children_under_23), "ילדים עד גיל 23")
        else:
            errors.append("מספר הילדים לא יכול להיות שלילי")
    if current_participation is not None:
        if current_participation >= 0:
            apply(state.current, "participation", float(current_participation), "השתתפות ברכב הנוכחי")
        else:
            errors.append("ההשתתפות ברכב הנוכחי לא יכולה להיות שלילית")
    if new_participation is not None:
        if new_participation < 0:
            errors.append("ההשתתפות ברכב החדש לא יכולה להיות שלילית")
        elif state.target.kind != "car":
            errors.append("עדיין לא נבחר רכב חדש - קודם יש לבחור ולאמת רכב מהקטלוג")
        else:
            apply(state.target, "participation", float(new_participation), "השתתפות ברכב החדש")
            state.target.participation_status = "confirmed"
            if state.pending_inquiry_car.get("new_participation") in (None, state.target.catalog_id):
                _cancel_pending(state, "new_participation")     # העובד מסר בעצמו - השאלה לנציג מיותרת

    if updated:
        state.invalidate_result()
    recalculated = _auto_recalculate(state)
    db.save_state(state)

    missing = state.missing_required_fields()
    data = {"updated": updated, "corrected": corrected}
    if group_note:
        data["group_conflict"] = group_note
    if recalculated:
        data["comparison"] = recalculated
    if errors:
        return _result(False, data, error=" | ".join(errors), missing=missing,
                       next_step="בקש מהעובד לתקן את הנתון השגוי בלבד")
    if recalculated:
        next_step = RECALCULATED_STEP
    else:
        next_step = ("כל הנתונים קיימים - אפשר להפעיל calculate_net_salary" if state.calculation_ready
                     else "שאל את העובד על הנתון החסר הבא")
    if corrected:
        next_step = "אשר לעובד שהנתון תוקן. " + next_step
    if group_note:
        next_step = "הצף לעובד את הסתירה בין הקבוצה לעיסוק (data.group_conflict) לפני שממשיכים. " + next_step
    return _result(True, data, missing=missing, next_step=next_step)


# ---------------------------------------------------------------- 2. זיהוי הרכב הנוכחי

def _exact_name_typed(query: str, candidate: dict) -> bool:
    """האם הטקסט שהעובד הקליד הוא השם המדויק של הרכב (שם מסחרי, עם או בלי יצרן ושנה), ולא רק התאמה חלקית."""
    def tokens(text: str) -> set:
        return set(re.findall(r"[0-9A-Za-zא-ת]+", text.upper()))
    wanted = tokens(query)
    commercial = tokens(candidate["commercial_name"])
    full = commercial | tokens(candidate["make_name"])
    year = {str(candidate["registration_year"])}
    return bool(wanted) and wanted in (commercial, full, commercial | year, full | year)


def find_current_car(session_id: str, commercial_name: str, registration_year: Optional[int] = None) -> dict:
    """חיפוש הרכב הנוכחי של העובד: קודם בקטלוג, אחר כך במאגר רשות המסים. מחזיר מועמדים לבחירה."""
    state = db.load_state(session_id)
    # שם מלא כפי שהוצג לעובדת ("טויוטה יפן COROLLA CROSS (2025)"): השנה נלקחת מהטקסט ולא נחשבת חלק מהשם
    year_in_text = re.search(r"\(?(?<!\d)((?:19|20)\d{2})(?!\d)\)?", commercial_name)
    if year_in_text:
        registration_year = registration_year or int(year_in_text.group(1))
        commercial_name = commercial_name.replace(year_in_text.group(0), " ").strip()
    candidates = []
    for car in find_car_by_name(commercial_name):
        if registration_year is None or car["registration_year"] == registration_year:
            candidates.append({
                "source": "catalog", "catalog_id": car["id"], "commercial_name": car["commercial_name"],
                "display_name": _display_name(car["make_name"], car["commercial_name"], car["registration_year"]),
                "make_name": car["make_name"], "registration_year": car["registration_year"],
                "car_type_code": car["car_type_code"], "make_code": car["make_code"],
                "model_code": car["model_code"], "drive": car["drive"], "usage_value": car["usage_value"],
                "default_participation": car["default_participation"],
            })
    if not candidates:
        tax_year = get_rules()["valueOfUse"]["taxYear"]
        for rec in search_by_name(commercial_name, registration_year, tax_year=tax_year):
            candidates.append({
                "source": "tax_db", "catalog_id": None, "commercial_name": rec["commercial_name"],
                "display_name": _display_name(rec["make_name"], rec["commercial_name"], rec["registration_year"]),
                "make_name": rec["make_name"], "registration_year": int(rec["registration_year"]),
                "car_type_code": rec["car_type_code"], "make_code": rec["make_code"],
                "model_code": rec["model_code"], "model_description": rec["model_description"],
                "drive": _drive_name(rec["drive_code"]), "usage_value": rec["usage_value"],
            })
    for i, cand in enumerate(candidates, start=1):
        cand["number"] = i
    state.car_candidates = candidates
    db.save_state(state)

    if not candidates:
        return _result(False, error=f"לא נמצא רכב בשם '{commercial_name}'"
                                    + (f" משנת {registration_year}" if registration_year else ""),
                       next_step="בקש מהעובד לבדוק את השם המסחרי / שנת הרישום. אם עדיין לא נמצא - "
                                 "פנה לנציג עם request_representative_info (current_car_usage_value)")
    if len(candidates) == 1 and _exact_name_typed(commercial_name, candidates[0]):
        # העובד כתב את השם המדויק של הרכב היחיד שנמצא - אין מה לאשר שוב: שומרים מיד
        saved = set_current_situation(session_id, "car", candidate_number=1)
        if saved["ok"]:
            return _result(True, {"candidates": candidates, "auto_confirmed": True,
                                  "saved_as_current_car": candidates[0]["display_name"]},
                           next_step=f"הרכב {candidates[0]['display_name']} נשמר כרכב הנוכחי. אל תשאל שוב אם זה הרכב. "
                                     "אם העובד עוד לא אמר כמה הוא משתתף בו - שאל, ושמור עם update_employee_data")
    if len(candidates) == 1:
        step = ("שאל את העובד 'האם התכוונת ל-[display_name]?'. כשהעובד עונה כן - הפעל מיד set_current_situation "
                "(kind='car', candidate_number=1, ועם participation אם העובד כבר אמר כמה הוא משתתף), "
                "ואל תפעיל find_current_car שוב")
    else:
        step = "הצג לעובד את המועמדים ובקש שיבחר מספר (אל תבחר בשבילו), ואז הפעל set_current_situation"
    return _result(True, {"candidates": candidates}, next_step=step)


def set_current_situation(session_id: str, kind: str, candidate_number: Optional[int] = None,
                          maintenance: Optional[float] = None, participation: Optional[float] = None) -> dict:
    """קביעת המצב היום: car (רכב מתוך המועמדים), waiver (אחזקת רכב), none (ללא רכב)."""
    state = db.load_state(session_id)
    cur = state.current
    # פרמטר שלא רלוונטי לסוג המצב נדחה בשגיאה - לא מתעלמים ממנו בשקט (אחרת הנתון הולך לאיבוד והבוט מדווח שנשמר)
    if maintenance is not None and kind != "waiver":
        return _result(False, error="maintenance רלוונטי רק ל-kind=waiver. השתתפות ברכב נשלחת בפרמטר participation",
                       next_step="הפעל שוב את הכלי עם הפרמטרים הנכונים")
    if participation is not None and kind != "car":
        return _result(False, error="participation רלוונטי רק ל-kind=car", next_step="הפעל שוב את הכלי")
    if participation is not None and participation < 0:
        return _result(False, error="ההשתתפות לא יכולה להיות שלילית")
    if kind == "car":
        if candidate_number is None or not (1 <= candidate_number <= len(state.car_candidates)):
            return _result(False, error="מספר מועמד לא תקין - קודם יש להפעיל find_current_car ולבחור מהרשימה",
                           next_step="הפעל find_current_car")
        cand = state.car_candidates[candidate_number - 1]
        tax_year = get_rules()["valueOfUse"]["taxYear"]
        rec = db_get_usage_value(tax_year, cand["registration_year"], cand["car_type_code"],
                                 cand["make_code"], cand["model_code"])
        if not rec["found"]:
            return _result(False, error=rec["error"],
                           next_step="פנה לנציג עם request_representative_info (current_car_usage_value)")
        cur.kind = "car"
        cur.label = f"{cand['commercial_name']} ({cand['registration_year']})"
        cur.catalog_id = cand["catalog_id"]
        cur.registration_year = cand["registration_year"]
        cur.car_type_code, cur.make_code, cur.model_code = cand["car_type_code"], cand["make_code"], cand["model_code"]
        cur.usage_value = float(rec["usage_value"])
        cur.is_electric = _drive_name(rec["drive_code"]) == ELECTRIC_DRIVE
        cur.maintenance = 0
        if participation is not None:
            cur.participation = float(participation)
    elif kind == "waiver":
        if maintenance is None:
            occ = check_waiver(state.employee.occupation) if state.employee.occupation else None
            if occ and occ["eligible"]:
                maintenance = occ["maintenance"]
            else:
                return _result(False, error="חסר סכום אחזקת הרכב שהעובד מקבל היום",
                               missing=["אחזקת רכב היום"], next_step="שאל את העובד כמה אחזקת רכב הוא מקבל היום")
        if maintenance < 0:
            return _result(False, error="סכום האחזקה לא יכול להיות שלילי")
        cur.kind, cur.label = "waiver", f"ויתור - אחזקת רכב {maintenance:,.0f} ₪"
        cur.maintenance, cur.usage_value, cur.participation, cur.is_electric = float(maintenance), 0, 0, False
        cur.catalog_id = cur.make_code = cur.model_code = cur.car_type_code = None
    elif kind == "none":
        cur.kind, cur.label = "none", "ללא רכב וללא אחזקה"
        cur.maintenance, cur.usage_value, cur.participation, cur.is_electric = 0, 0, 0, False
    else:
        return _result(False, error="kind חייב להיות car / waiver / none")

    state.invalidate_result()
    recalculated = _auto_recalculate(state)
    db.save_state(state)
    missing = state.missing_required_fields()
    data = {"current": cur.model_dump(exclude_none=True)}
    if recalculated:
        data["comparison"] = recalculated
    next_step = ("שאל את העובד כמה הוא משתתף היום ברכב" if kind == "car" and cur.participation is None
                 else "המשך לנתון החסר הבא")
    return _result(True, data, missing=missing, next_step=RECALCULATED_STEP if recalculated else next_step)


# ---------------------------------------------------------------- 3. קטלוג וזכאות

def allowed_catalog(state: ConversationState) -> dict | None:
    """הרכבים שהעובד רשאי לבחור: מהקבוצה שלו ומטה, ורכבי המסלול המשפחתי אם הוא זכאי (+ אפשרות ויתור).
    פונקציה טהורה, בלי שינוי מצב - משמשת גם את הכלי show_catalog וגם את בורר הרכבים במסך.
    מחזירה None אם קבוצת הזכאות עדיין לא ידועה."""
    group = state.employee.eligibility_group
    if group is None:
        return None
    rules = get_rules()
    order = rules["groupOrder"]
    children = state.employee.children_under_23
    allowed, family_note = [], None
    for car in get_catalog():
        if is_family_car(car, rules):
            if children is None:
                family_note = (f"קיימים רכבי מסלול ברוכת ילדים ({rules['familyTrack']['minChildren']} ילדים ומעלה "
                               f"עד גיל {rules['familyTrack']['childMaxAge']}) - אפשר לשאול את העובד אם רלוונטי")
                continue
            if children < rules["familyTrack"]["minChildren"]:
                continue
        elif order.index(car["group"]) > order.index(group):
            continue
        allowed.append({
            "id": car["id"], "group": car["group"], "name": f"{car['make_name']} {car['commercial_name']}",
            "commercial_name": car["commercial_name"],
            "display_name": _display_name(car["make_name"], car["commercial_name"], car["registration_year"]),
            "registration_year": car["registration_year"], "drive": car["drive"],
            "usage_value": car["usage_value"], "monthly_cost": car["monthly_cost"],
        })
    waiver = check_waiver(state.employee.occupation) if state.employee.occupation else None
    data = {"employee_group": group, "budget": group_budget(group), "cars": allowed,
            "waiver_option": (waiver["reason"] + (f" - אחזקה {waiver['maintenance']:,} ₪" if waiver["eligible"] else "")
                              if waiver else "ויתור תלוי בעיסוק - יש לשאול את העובד מה העיסוק שלו")}
    data["waiver_allowed"] = bool(waiver and waiver["eligible"])
    if family_note:
        data["family_track_note"] = family_note
    return data


def current_situation_known(state: ConversationState) -> bool:
    """האם המצב היום ידוע מספיק כדי להציג חלופות: נקבע רכב/ויתור/ללא רכב, ובמקרה של רכב - גם ההשתתפות עליו
    (או שהיא כבר נשלחה לנציג). סדר השיחה: קודם המצב הנוכחי, ורק אחר כך החלופות."""
    current = state.current
    if current.kind is None:
        return False
    if current.kind == "car":
        return current.participation is not None or state.waiting_for_representative
    return True


def _employee_asked_for_waiver(session_id: str) -> bool:
    """האם ההודעה האחרונה של העובד ביקשה ויתור על רכב (בטקסט חופשי או בלחיצה): אז לא פותחים בורר רכבים."""
    last = next((m["content"] for m in reversed(db.load_messages(session_id)) if m["role"] == "user"), "")
    return "ויתור" in last or "לוותר" in last


def show_catalog(session_id: str) -> dict:
    """פותח לעובד מעל תיבת השיחה בורר רכבים (כפתורים או טבלה לבחירה בלחיצה), ומחזיר את רשימת הרכבים המותרים."""
    state = db.load_state(session_id)
    data = allowed_catalog(state)
    if data is None:
        return _result(False, error="חסרה קבוצת הזכאות של העובד", missing=["קבוצת זכאות"],
                       next_step="שאל את העובד באיזו קבוצת זכאות הוא")
    if state.target.kind == "waiver" and _employee_asked_for_waiver(session_id):
        return _result(False, error="העובד ביקש ויתור על רכב - אין צורך בבורר רכבים",
                       next_step="אל תפתח את הבורר. הצג את חישוב הוויתור (calculate_net_salary אם עוד לא חושב)")
    if not current_situation_known(state):
        return _result(False, error="עוד לא ידוע הרכב הנוכחי של העובד (או ההשתתפות עליו)",
                       missing=["רכב נוכחי והשתתפות עליו"],
                       next_step="לפני הצגת חלופות אסוף את המצב היום: find_current_car -> set_current_situation, "
                                 "ושאל כמה העובד משתתף ברכב. רק אחר כך הפעל show_catalog")
    state.picker_open = True
    db.save_state(state)
    return _result(True, data, next_step="בורר הרכבים נפתח לעובד מעל תיבת השיחה. אל תרשום/י את הרכבים בצ'אט: "
                                         "אמור/י רק שאפשר לבחור בלחיצה (או להקליד שם רכב)")


def verify_budget_and_eligibility(session_id: str, choice: str) -> dict:
    """בדיקת זכאות לחלופה (מזהה רכב או 'ויתור'). אם מאושרת - נשמרת כחלופה המבוקשת."""
    state = db.load_state(session_id)
    group = state.employee.eligibility_group
    if group is None:
        return _result(False, error="חסרה קבוצת הזכאות של העובד", missing=["קבוצת זכאות"],
                       next_step="שאל את העובד באיזו קבוצת זכאות הוא")
    target = state.target
    choice = choice.strip()

    if choice == WAIVER_CHOICE:
        check = check_waiver(state.employee.occupation)
        if check["eligible"] is None:
            return _result(False, {"eligible": None, "reason": check["reason"]}, missing=["עיסוק"],
                           next_step="שאל את העובד מה העיסוק שלו (מתוך טבלת העיסוקים)")
        if not check["eligible"]:
            return _result(True, {"eligible": False, "reason": check["reason"]},
                           next_step="הסבר לעובד שאין אפשרות ויתור, והצע לבחור רכב מהקטלוג")
        # שאלה פתוחה לנציג על השתתפות ברכב קודם נשארת (pending_inquiry_car): התשובה תחושב על אותו רכב
        target = state.target = TargetChoice(kind="waiver", label=f"ויתור - אחזקת רכב {check['maintenance']:,} ₪",
                        maintenance=float(check["maintenance"]), eligibility_reason=check["reason"],
                        budget=group_budget(group), participation=0, participation_status="confirmed")
        state.picker_open = False           # נבחרה חלופה - בורר הרכבים נסגר
        state.invalidate_result()
        recalculated = _auto_recalculate(state) or _compute_if_ready(state)
        db.save_state(state)
        data = {"eligible": True, "reason": check["reason"], "maintenance": check["maintenance"],
                "note": "אחזקת הרכב חייבת במס ואינה פנסיונית; תקציב שלא נוצל לא הופך לכסף בשכר"}
        if recalculated:
            data["comparison"] = recalculated
        return _result(True, data, missing=state.missing_required_fields(),
                       next_step=RECALCULATED_STEP if recalculated
                       else "אם אין נתונים חסרים - הפעל calculate_net_salary")

    car = find_car(choice.upper())
    if car is None:
        # העובד הקליד שם ולא בחר בבורר: חיפוש חלקי בקטלוג (יצרן + שם מסחרי), ואם יש התאמה אחת ממשיכים איתה
        matches = find_car_by_name(choice)
        if len(matches) == 1:
            car = matches[0]
        elif matches:
            names = [_display_name(c["make_name"], c["commercial_name"], c["registration_year"]) for c in matches]
            return _result(False, {"matches": [{"id": c["id"], "display_name": n} for c, n in zip(matches, names)]},
                           error=f"'{choice}' מתאים ליותר מרכב אחד בקטלוג",
                           next_step="שאל את העובד לאיזה מהם התכוון (display_name), ואז הפעל שוב עם המזהה")
        else:
            everything = [{"id": c["id"], "display_name": _display_name(c["make_name"], c["commercial_name"],
                                                                        c["registration_year"])}
                          for c in get_catalog()]
            return _result(False, {"catalog": everything}, error=f"הרכב '{choice}' לא נמצא בקטלוג לפי השם שהוקלד",
                           next_step="ייתכן שהעובד כתב את השם בעברית או בכתיב אחר: בדוק ב-data.catalog אם אחד הרכבים "
                                     "מתאים (למשל 'קרניבל' = CARNIVAL) והפעל שוב עם המזהה שלו. אם אין התאמה - "
                                     "שאל את העובד 'האם התכוונת ל...?' ורק אם הוא מבקש הפעל show_catalog")
    check = check_car_choice(group, car, state.employee.children_under_23,
                             children_ages=state.employee.children_ages)
    if check["eligible"] is None:
        return _result(False, {"eligible": None, "reason": check["reason"]}, missing=["ילדים עד גיל 23"],
                       next_step="שאל את העובד כמה ילדים עד גיל 23 יש לו")
    if not check["eligible"]:
        return _result(True, {"eligible": False, "reason": check["reason"]},
                       next_step="הסבר לעובד למה הרכב לא זמין לו והצע רכב מהקבוצה שלו או נמוכה ממנה")

    record = lookup_catalog_usage_value(car)
    if not record["found"]:
        return _result(False, error=record["error"], next_step="פנה לנציג - שווי השימוש לא נמצא במאגר")
    same_car = target.kind == "car" and target.catalog_id == car["id"]
    keep_participation = (target.participation, target.participation_status,
                          target.participation_effective_from) if same_car else (None, "missing", None)
    target = state.target = TargetChoice(
        kind="car", label=f"{car['commercial_name']} ({car['registration_year']})", catalog_id=car["id"],
        car_group=car["group"], usage_value=float(record["usage_value"]), is_electric=car["drive"] == ELECTRIC_DRIVE,
        monthly_cost=car["monthly_cost"], budget=check["budget"],
        family_employee_share=family_employee_share(car, group), eligibility_reason=check["reason"],
        participation=keep_participation[0], participation_status=keep_participation[1],
        participation_effective_from=keep_participation[2],
    )
    state.picker_open = False               # נבחרה חלופה - בורר הרכבים נסגר
    state.invalidate_result()
    recalculated = _auto_recalculate(state) or _compute_if_ready(state)
    db.save_state(state)
    data = {"eligible": True, "reason": check["reason"], "car": target.label, "car_group": car["group"],
            "display_name": _display_name(car["make_name"], car["commercial_name"], car["registration_year"]),
            "budget": check["budget"], "monthly_cost": car["monthly_cost"], "usage_value": target.usage_value,
            "drive": car["drive"], "family_employee_share": target.family_employee_share}
    if target.is_electric:
        data["electric_refund"] = f"{get_rules()['electric']['netBonus']} ₪ נטו בחודש (החזר חשמל)"
    if recalculated:
        data["comparison"] = recalculated
    step = ("שאל את העובד אם ידוע לו סכום ההשתתפות שלו ברכב החדש. רק אם ענה שהוא לא יודע - הפעל "
            "request_representative_info עם new_participation ו-employee_confirmed_unknown=true"
            if target.participation is None else "אם אין נתונים חסרים - הפעל calculate_net_salary")
    return _result(True, data, missing=state.missing_required_fields(),
                   next_step=RECALCULATED_STEP if recalculated else step)


# ---------------------------------------------------------------- 4. שליפת שווי שימוש

def get_car_usage_value(session_id: str, tax_year: int, registration_year: int, car_type_code: str,
                        make_code: str, model_code: str) -> dict:
    """שליפה ישירה ממאגר רשות המסים לפי המפתח הרשמי המלא."""
    rec = db_get_usage_value(tax_year, registration_year, car_type_code, make_code, model_code)
    if not rec["found"]:
        return _result(False, error=rec["error"], next_step="אל תנחש שווי שימוש - בדוק את הקודים או פנה לנציג")
    data = {k: rec[k] for k in ("commercial_name", "make_name", "registration_year", "usage_value", "list_price")}
    data["drive"] = _drive_name(rec["drive_code"])
    return _result(True, data)


# ---------------------------------------------------------------- 5. חישוב

def _build_inputs(state: ConversationState) -> tuple[SalaryInput, CarSituation, CarSituation]:
    emp, cur, tgt = state.employee, state.current, state.target
    salary = SalaryInput(base_salary=emp.base_salary, commissions=emp.commissions, bonus=emp.bonus,
                         pension_rate=emp.pension_rate, study_fund_rate=emp.study_fund_rate,
                         credit_points=emp.credit_points)
    today = CarSituation(label=cur.label, usage_value=cur.usage_value or 0, participation=cur.participation or 0,
                         maintenance=cur.maintenance, is_electric=cur.is_electric)
    alt = CarSituation(label=tgt.label, usage_value=tgt.usage_value, participation=tgt.participation or 0,
                       maintenance=tgt.maintenance, is_electric=tgt.is_electric,
                       family_employee_share=tgt.family_employee_share)
    # אם הרכב הנוכחי הוא מהמסלול המשפחתי, גם היום העובד משלם חלק מהפער מעל התקציב
    current_car = find_car(cur.catalog_id) if cur.catalog_id else None
    if current_car:
        today.family_employee_share = family_employee_share(current_car, emp.eligibility_group)
    return salary, today, alt


def _side_summary(side: dict) -> dict:
    r = display_round
    return {
        "label": side["label"], "net": r(side["net"]), "gross_salary": r(side["gross_salary"]),
        "usage_value": r(side["usage_value"]), "maintenance": r(side["maintenance"]),
        "electric_refund_net": side["electric_net_bonus"], "electric_refund_gross": r(side["electric_gross"]),
        "income_tax": r(side["income_tax"]), "national_insurance_and_health": r(side["social_total"]),
        "pension": r(side["pension"]), "study_fund": r(side["study_fund"]),
        "participation": r(side["participation"]), "family_employee_share": r(side["family_employee_share"]),
    }


def _compute(state: ConversationState, status: str) -> dict:
    salary, today, alt = _build_inputs(state)
    result = compare(salary, today, alt)
    tgt = state.target
    summary = {
        "status": status,
        "today": _side_summary(result["today"]),
        "alternative": _side_summary(result["alternative"]) | {
            "eligibility_reason": tgt.eligibility_reason, "budget": tgt.budget, "monthly_cost": tgt.monthly_cost,
            "participation_status": tgt.participation_status,
            "participation_effective_from": tgt.participation_effective_from,
        },
        "net_today": result["net_today"],
        "net_alternative": result["net_alternative"],
        "diff": result["diff"],
        "changes": result["changes"],
        "explanation": result["explanation"],
    }
    if status == "provisional":
        summary["note"] = (f"השוואת ביניים: ההשתתפות ברכב החדש ({display_round(tgt.participation):,} ₪) היא ברירת "
                           "המחדל מהקטלוג וממתינה לאישור הנציג - התוצאה תתעדכן כשתגיע תשובה")
    state.last_result = summary
    state.result_is_stale = False
    return summary


def _calculation_status(state: ConversationState) -> str | None:
    """'final' / 'provisional' אם אפשר לחשב עכשיו, אחרת None.
    השוואת ביניים מותרת רק כשהדבר היחיד שממתין לנציג הוא ההשתתפות ברכב החדש (ברירת מחדל מהקטלוג)."""
    if state.missing_required_fields():
        return None
    if state.blocking_pending:
        only_participation = set(state.blocking_pending) <= {"new_participation"}
        if not (only_participation and state.target.participation_status == "provisional"):
            return None
    return "provisional" if state.target.participation_status == "provisional" else "final"


def _compute_if_ready(state: ConversationState) -> dict | None:
    """אחרי אימות חלופה: אם כל הנתונים כבר קיימים (למשל ויתור, שאין בו השתתפות) - מחשבים מיד בקוד,
    בלי להמתין שהמודל יזכור להפעיל calculate_net_salary."""
    if not state.calculation_ready:
        return None
    try:
        return _compute(state, "final")
    except ValueError:
        return None


def _auto_recalculate(state: ConversationState) -> dict | None:
    """אחרי שינוי נתון: אם כבר הייתה תוצאה והחישוב אפשרי - מחשבים מחדש בקוד, בלי להסתמך על המודל.
    מחזיר את התוצאה המעודכנת, או None אם אין מה לחשב מחדש / עדיין חסר נתון."""
    if state.last_result is None or not state.result_is_stale:
        return None
    status = _calculation_status(state)
    if status is None:
        return None
    try:
        return _compute(state, status)
    except ValueError:
        return None


RECALCULATED_STEP = ("החישוב עודכן אוטומטית לפי השינוי - הצג לעובד את ההשוואה המעודכנת מתוך data.comparison "
                     "(אין צורך להפעיל calculate_net_salary)")


def calculate_net_salary(session_id: str) -> dict:
    """הרצת מנוע החישוב על הנתונים שבמצב השיחה. מסרב אם חסר נתון חובה."""
    state = db.load_state(session_id)
    missing = state.missing_required_fields()
    if missing:
        return _result(False, error="לא ניתן לחשב - חסרים נתונים", missing=missing,
                       next_step="שאל את העובד על הנתונים החסרים. אל תנחש")
    status = _calculation_status(state)
    if status is None:
        fields = ", ".join(REP_FIELDS.get(f, f) for f in state.pending_questions)
        return _result(False, error=f"ממתינים לתשובת הנציג על: {fields}",
                       next_step="עדכן את העובד שהשאלה אצל הנציג והחישוב ימשיך כשתגיע תשובה")
    try:
        summary = _compute(state, status)
    except ValueError as e:  # הגנה נוספת - אימות Pydantic במנוע
        return _result(False, error=f"נתון לא תקין: {e}", next_step="בקש מהעובד לתקן את הנתון")
    db.save_state(state)
    step = ("הצג לעובד את ההשוואה וההסבר, וציין שזו השוואת ביניים עד תשובת הנציג" if status == "provisional"
            else "הצג לעובד את ההשוואה וההסבר, ושאל אם ירצה לבדוק חלופה אחרת")
    return _result(True, summary, next_step=step)


# ---------------------------------------------------------------- 6. פנייה לנציג

_UNKNOWN_RE = re.compile(
    r"לא יודע|לא יודעת|אינני יודע|אינני יודעת|אין לי מושג|לא ידוע|לא זוכר|לא זוכרת|לא בטוח|לא בטוחה|"
    r"אין לי מידע|אין לי את הנתון|לא מכיר|נציג|תשאל|תשאלי|תברר|תבררי|תבדוק|תבדקי|תפנה|תפני|"
    r"^\s*(?:לא|אין|לא ממש|אין לי)[\s.!?]*$")


def _employee_said_unknown(session_id: str, last: int = 3) -> bool:
    """האם באחת מ-3 הודעות העובד האחרונות הוא אמר שאינו יודע את הנתון (או ביקש לברר מול הנציג)."""
    user_messages = [m["content"] for m in db.load_messages(session_id) if m["role"] == "user"][-last:]
    return any(_UNKNOWN_RE.search(text) for text in user_messages)


def request_representative_info(session_id: str, missing_field: str, context_message: str,
                                employee_confirmed_unknown: bool = False) -> dict:
    """פתיחת שאלה לנציג התחבורה על נתון שהעובד לא יודע. לעולם לא מנחשים."""
    if missing_field not in REP_FIELDS:
        return _result(False, error=f"missing_field חייב להיות אחד מ: {', '.join(REP_FIELDS)}")
    if missing_field in ASK_EMPLOYEE_FIRST and not (employee_confirmed_unknown and _employee_said_unknown(session_id)):
        # הדגל שהמודל מעביר לא מספיק: נבדק בקוד שבהודעות האחרונות של העובד הוא באמת אמר שאינו יודע / ביקש לפנות לנציג
        return _result(False, error="אי אפשר לפנות לנציג לפני שהעובד אמר במפורש שהוא לא יודע את הנתון",
                       next_step=f"שאל את העובד: {REP_FIELDS[missing_field]}? רק אם ענה שאינו יודע - הפעל שוב את הכלי "
                                 "עם employee_confirmed_unknown=true")
    state = db.load_state(session_id)

    inquiry_car = state.target.catalog_id if missing_field == "new_participation" and state.target.kind == "car" else None
    existing = db.find_open_question(session_id, missing_field)
    if existing and inquiry_car and state.pending_inquiry_car.get(missing_field) not in (None, inquiry_car):
        _cancel_pending(state, missing_field)         # שאלה פתוחה על רכב אחר מוחלפת בשאלה על הרכב הנוכחי
        existing = None
    if existing:
        question_id = existing["id"]
    else:
        emp, cur, tgt = state.employee, state.current, state.target
        summary = (f"קבוצה {emp.eligibility_group or '?'} · היום: {cur.label or '?'} · "
                   f"חלופה: {tgt.label or '?'}")
        question_id = db.create_question(session_id, missing_field, context_message, summary)
    state.pending_questions[missing_field] = question_id
    if inquiry_car:
        state.pending_inquiry_car[missing_field] = inquiry_car     # הרכב שעליו נשאלה השאלה
    state.waiting_for_representative = True

    data = {"question_id": question_id, "field": REP_FIELDS[missing_field]}
    if missing_field == "new_participation" and state.target.kind == "car":
        car = find_car(state.target.catalog_id)
        state.target.participation = float(car["default_participation"])
        state.target.participation_status = "provisional"
        if not state.missing_required_fields():
            data["provisional_comparison"] = _compute(state, "provisional")
    db.save_state(state)
    step = ("עדכן את העובד: 'שלחתי שאלה לנציג התחבורה'. "
            + ("הצג את השוואת הביניים וציין שהיא תתעדכן כשתגיע תשובה" if "provisional_comparison" in data
               else "החישוב ימשיך כשתגיע תשובה"))
    return _result(True, data, next_step=step)


# ---------------------------------------------------------------- לא כלי: קליטת תשובות הנציג (מופעל ע"י האפליקציה)

def _drop_orphan_pending(session_id: str, answered_fields: set) -> None:
    """ניקוי: שאלה שהמצב מחכה לה אבל אין לה שאלה פתוחה (או תשובה שממתינה לקליטה) בנציג - לא ממשיכים לחכות לה.
    כך העובד לא נשאר עם "ממתין לנציג" כשבמסך הנציג אין שום שאלה."""
    state = db.load_state(session_id)
    orphans = [f for f in state.pending_questions
               if f not in answered_fields and db.find_open_question(session_id, f) is None]
    if orphans:
        for field in orphans:
            state.pending_questions.pop(field, None)
            state.pending_inquiry_car.pop(field, None)
        state.waiting_for_representative = bool(state.pending_questions)
        state.invalidate_result()
        db.save_state(state)


def apply_representative_answers(session_id: str) -> list[dict]:
    """קולט תשובות נציג שעוד לא נקלטו, מעדכן את המצב ומחשב מחדש אם אפשר.
    מחזיר רשימת אירועים להצגה בשיחה (ריקה אם אין חדש)."""
    answers = db.answered_unapplied(session_id)
    _drop_orphan_pending(session_id, {q["missing_field"] for q in answers})
    if not answers:
        return []
    state = db.load_state(session_id)
    events = []
    for q in answers:
        field, value = q["missing_field"], q["answer_value"]
        asked_about = state.pending_inquiry_car.get(field)
        if field == "new_participation" and value is not None and asked_about and (
                state.target.kind != "car" or state.target.catalog_id != asked_about):
            # בזמן ההמתנה העובד בדק חלופה אחרת (ויתור / רכב אחר): התשובה מחושבת על הרכב שעליו נשאלה השאלה
            state.pending_questions.pop(field, None)
            db.save_state(state)
            verify_budget_and_eligibility(session_id, asked_about)
            state = db.load_state(session_id)
        if value is not None:
            if field == "new_participation" and state.target.kind == "car":
                state.target.participation = float(value)
                state.target.participation_status = "confirmed"
                state.target.participation_effective_from = q["effective_from"]
            elif field == "current_participation":
                state.current.participation = float(value)
            elif field == "current_car_usage_value":
                state.current.kind = "car"
                state.current.usage_value = float(value)
                state.current.label = state.current.label or "הרכב הנוכחי (לפי הנציג)"
            elif field == "waiver_maintenance":
                state.target = TargetChoice(kind="waiver", label=f"ויתור - אחזקת רכב {value:,.0f} ₪",
                                      maintenance=float(value), eligibility_reason="אושר על ידי הנציג",
                                      participation=0, participation_status="confirmed")
        state.pending_questions.pop(field, None)
        state.pending_inquiry_car.pop(field, None)
        events.append({"field": REP_FIELDS.get(field, field), "value": value,
                       "effective_from": q["effective_from"], "note": q["answer_note"],
                       "car": state.target.label if field == "new_participation" else None})
        db.mark_applied(q["id"])
    state.waiting_for_representative = bool(state.pending_questions)
    state.invalidate_result()
    if state.calculation_ready:
        events.append({"recalculated": _compute(state, "final")})
    db.save_state(state)
    return events
