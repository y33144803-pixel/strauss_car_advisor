"""המרת קובץ האקסל של האתגר לקבצי JSON שהמנוע קורא.

הרצה:  python scripts/convert_excel.py
בפיילוט מחליפים את האקסל (או את קבצי ה-JSON) בנתונים האמיתיים ומריצים שוב - בלי לגעת בקוד.
"""
import json
import os
import sys

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
sys.path.insert(0, os.path.join(BASE_DIR, "backend"))

from usage_value_db import get_usage_value  # noqa: E402

XLSX_PATH = os.path.join(DATA_DIR, "challenge_data.xlsx")
TAX_YEAR = 2026


def read_sheet(name: str) -> pd.DataFrame:
    return pd.read_excel(XLSX_PATH, sheet_name=name, dtype=str).fillna("")


def find_car_type_code(make_code, model_code, year, commercial_name) -> str:
    """הקטלוג לא כולל קוד סוג רכב - מאתרים אותו במאגר ומוודאים שהשם המסחרי תואם."""
    for car_type in ("1", "2"):
        rec = get_usage_value(TAX_YEAR, year, car_type, make_code, model_code)
        if rec["found"] and rec["commercial_name"] == commercial_name:
            return car_type
    raise ValueError(f"רכב מהקטלוג לא נמצא במאגר: {commercial_name} ({make_code}/{model_code}/{year})")


def convert_catalog() -> list[dict]:
    cars = []
    for _, r in read_sheet("קטלוג רכבים").iterrows():
        car = {
            "id": r["מזהה"],
            "group": r["קבוצה"],
            "make_name": r["תוצר"],
            "make_code": r["קוד תוצר"],
            "commercial_name": r["דגם (כינוי מסחרי)"],
            "model_code": r["קוד דגם"],
            "model_description": r["תאור דגם"],
            "registration_year": int(r["שנת רישום"]),
            "drive": r["הנעה"],
            "list_price": int(r["מחיר מחירון"]),
            "usage_value": int(r["שווי שימוש חודשי"]),
            "monthly_cost": int(r["עלות חודשית לחברה (דמה)"]),
            "default_participation": int(r["השתתפות עובד ברירת מחדל (דמה)"]),
        }
        car["car_type_code"] = find_car_type_code(
            car["make_code"], car["model_code"], car["registration_year"], car["commercial_name"])
        cars.append(car)
    return cars


def convert_occupations() -> list[dict]:
    return [
        {
            "occupation": r["עיסוק"],
            "group": r["קבוצת זכאות"],
            "car_required": r["רכב חובה"] == "כן",
            "waiver_allowed": r["מותר לוותר"] == "כן",
            "maintenance_amount": int(r["אחזקת רכב בוויתור (₪)"]),
        }
        for _, r in read_sheet("עיסוקים").iterrows()
    ]


def convert_sample_employees() -> list[dict]:
    """עובדים לדוגמה + נטו הייחוס מגיליון 'חישובים' - משמשים לבדיקות היחידה."""
    calcs = read_sheet("חישובים").set_index("מזהה")
    employees = []
    for _, r in read_sheet("עובדים לדוגמה").iterrows():
        c = calcs.loc[r["מזהה"]]
        employees.append({
            "id": r["מזהה"],
            "name": r["שם"],
            "occupation": r["עיסוק"],
            "group": r["קבוצה"],
            "base_salary": float(r["שכר בסיס"]),
            "commissions": float(r["עמלות"]),
            "bonus": float(r["בונוס"]),
            "pension_rate": float(r["פנסיה עובד %"]),
            "study_fund_rate": float(r["קרן השתלמות עובד %"]),
            "credit_points": float(r["נקודות זיכוי"]),
            "children_under_23": int(r["ילדים עד 23"]),
            "current_car": r["רכב היום"],
            "current_participation": float(r["השתתפות היום"]),
            "question": r["השאלה"],
            "reference": {
                "today": c["היום"],
                "alternative": c["החלופה"],
                "eligible": c["זכאות"] == "כן",
                "reason": c["סיבה"],
                "net_today": int(c["נטו היום"]),
                "net_alternative": int(c["נטו בחלופה"]),
                "diff": int(c["הפרש"]),
                "tax_today": int(c["מס היום"]),
                "tax_alternative": int(c["מס בחלופה"]),
                "social_today": int(c["ביטוח לאומי+בריאות היום"]),
                "social_alternative": int(c["ביטוח לאומי+בריאות בחלופה"]),
                "electric_gross": int(c["החזר חשמל ברוטו"]),
                "family_employee_share": int(c["חלק העובד במסלול המשפחתי"]),
                "why": c["למה"],
            },
        })
    return employees


def write_json(name: str, data) -> None:
    path = os.path.join(DATA_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"נכתב {path} ({len(data)} רשומות)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    write_json("catalog.json", convert_catalog())
    write_json("occupations.json", convert_occupations())
    write_json("sample_employees.json", convert_sample_employees())
