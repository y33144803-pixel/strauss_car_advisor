"""כללי הזכאות: בחירת רכב לפי קבוצה, מסלול ברוכת ילדים, ויתור על רכב.

כל הסכומים והכללים נקראים מקבצי ההגדרות. אם חסר נתון - מחזירים סטטוס 'missing' ולא מנחשים.
"""
from calc_engine import CarSituation
from data_store import find_occupation, get_rules
from usage_value_db import get_usage_value

ELECTRIC_DRIVE = "חשמלי"


def is_family_car(car: dict, rules: dict | None = None) -> bool:
    """רכב מהמסלול המשפחתי - לפי familyTrack.cars בקובץ ההגדרות (ולא לפי שם הקבוצה בקטלוג)."""
    rules = rules or get_rules()
    return car["id"] in rules["familyTrack"]["cars"]


def group_budget(group: str, rules: dict | None = None) -> float:
    rules = rules or get_rules()
    return rules["budgets"][group]


def _validate_group(group: str, rules: dict) -> str | None:
    if group not in rules["groupOrder"]:
        return f"קבוצת זכאות לא חוקית: '{group}'. הקבוצות האפשריות: {', '.join(rules['groupOrder'])}"
    return None


def check_car_choice(employee_group: str, car: dict, children_under_23: int | None = None,
                     rules: dict | None = None, children_ages: list[int] | None = None) -> dict:
    """האם העובד רשאי לבחור את הרכב. מחזיר eligible (True/False/None=חסר מידע), סיבה ותקציב."""
    rules = rules or get_rules()
    error = _validate_group(employee_group, rules)
    if error:
        return {"eligible": False, "reason": error, "budget": None}
    budget = group_budget(employee_group, rules)

    if is_family_car(car, rules):
        family = rules["familyTrack"]
        if children_under_23 is None:
            return {"eligible": None, "budget": budget, "missing": "children_under_23",
                    "reason": f"רכב מהמסלול המשפחתי דורש {family['minChildren']} ילדים ומעלה עד גיל "
                              f"{family['childMaxAge']} - חסר מספר הילדים"}
        if children_under_23 < family["minChildren"]:
            reason = (f"מסלול ברוכת ילדים מחייב {family['minChildren']} ילדים ומעלה שגילם עד {family['childMaxAge']} "
                      f"(נספרו {children_under_23}")
            over_age = [age for age in (children_ages or []) if age > family["childMaxAge"]]
            if over_age:
                reason += (f"; גיל {', '.join(str(a) for a in over_age)} חורג ממגבלת הגיל {family['childMaxAge']} "
                           "ולכן אינו נספר")
            reason += ")"
            if car.get("monthly_cost") and car["monthly_cost"] > budget:
                reason += (f". בנוסף, עלות הרכב {car['monthly_cost']:,} ₪ חורגת מתקציב קבוצה {employee_group} "
                           f"({budget:,} ₪), ולכן אי אפשר לבחור בו מחוץ למסלול הזה")
            return {"eligible": False, "budget": budget, "reason": reason}
        share = int(family["gapSplitEmployee"] * 100)
        return {"eligible": True, "budget": budget,
                "reason": f"מסלול ברוכת ילדים ({children_under_23} ילדים); על הפער מעל התקציב - {share}% עובד"}

    order = rules["groupOrder"]
    if order.index(car["group"]) <= order.index(employee_group):
        return {"eligible": True, "budget": budget, "reason": "הרכב מהקבוצה שלך או נמוכה ממנה"}
    return {"eligible": False, "budget": budget,
            "reason": f"הרכב מקבוצה {car['group']} - גבוהה מקבוצת הזכאות שלך ({employee_group}). "
                      f"אפשר לבחור רכב מקבוצה {employee_group} או נמוכה ממנה"}


def check_waiver(occupation: str | None) -> dict:
    """האם העיסוק מאפשר ויתור על רכב, ובכמה אחזקת רכב."""
    if not occupation:
        return {"eligible": None, "missing": "occupation", "reason": "חסר העיסוק - הוא קובע אם מותר לוותר"}
    occ = find_occupation(occupation)
    if occ is None:
        return {"eligible": None, "missing": "occupation",
                "reason": f"העיסוק '{occupation}' לא נמצא בטבלת העיסוקים - יש לברר מול נציג"}
    if not occ["waiver_allowed"]:
        return {"eligible": False, "maintenance": 0,
                "reason": f"בעיסוק '{occupation}' הרכב הוא חובה - אין אפשרות לוותר עליו"}
    return {"eligible": True, "maintenance": occ["maintenance_amount"], "reason": "העיסוק מאפשר ויתור"}


def family_employee_share(car: dict, employee_group: str, rules: dict | None = None) -> float:
    """חלק העובד בפער שבין עלות רכב המסלול המשפחתי לתקציב הקבוצה (0 לרכב רגיל)."""
    rules = rules or get_rules()
    if not is_family_car(car, rules):
        return 0.0
    gap = max(0.0, car["monthly_cost"] - group_budget(employee_group, rules))
    return gap * rules["familyTrack"]["gapSplitEmployee"]


def lookup_catalog_usage_value(car: dict, rules: dict | None = None) -> dict:
    """שווי השימוש של רכב מהקטלוג - נשלף מהמאגר הרשמי לפי המפתח המלא."""
    rules = rules or get_rules()
    return get_usage_value(rules["valueOfUse"]["taxYear"], car["registration_year"],
                           car["car_type_code"], car["make_code"], car["model_code"])


def car_situation(car: dict, participation: float, employee_group: str, rules: dict | None = None) -> CarSituation:
    """בניית מצב רכב מקטלוג. שווי השימוש תמיד מהמאגר הרשמי, לא מהקטלוג."""
    rules = rules or get_rules()
    record = lookup_catalog_usage_value(car, rules)
    if not record["found"]:
        raise ValueError(record["error"])
    return CarSituation(
        label=f"{car['commercial_name']} ({car['registration_year']})",
        usage_value=record["usage_value"],
        participation=participation,
        is_electric=car["drive"] == ELECTRIC_DRIVE,
        family_employee_share=family_employee_share(car, employee_group, rules),
    )


def waiver_situation(maintenance: float) -> CarSituation:
    return CarSituation(label=f"ויתור - אחזקת רכב {maintenance:,.0f} ₪", maintenance=maintenance)
