"""מאגר שווי השימוש של רשות המסים (הקובץ המורחב 2026).

הקובץ נטען פעם אחת לזיכרון, ונבנה אינדקס לפי המפתח הרשמי:
שנת מס + שנת רישום + קוד סוג רכב + קוד תוצר + קוד דגם.
שם מסחרי לבדו אינו מספיק לשליפה - חיפוש לפי שם מחזיר רשימת מועמדים בלבד.
"""
import re
import os
from functools import lru_cache

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(BASE_DIR, "data", "car_usage_value_2026.csv")

COLUMNS = {
    "שנת מס": "tax_year",
    "שנת רישום": "registration_year",
    "קוד סוג רכב": "car_type_code",
    "קוד תוצר": "make_code",
    "תוצר": "make_name",
    "קוד דגם": "model_code",
    "תאור דגם": "model_description",
    "סוג דלק": "fuel_type",
    "כינוי מסחרי": "commercial_name",
    "לא-0.כן-1 אוטומט": "is_automatic",
    # הכותרת בקובץ שגויה (1-חשמלי...); בפועל המיפוי הוא לפי rules_config: 0 בנזין, 1 היברידי, 2 פלאג-אין, 3 חשמלי
    "1-חשמלי.2-היברידי.3-פלג-אין": "drive_code",
    "מחיר מחירון": "list_price",
    "שווי שימוש": "usage_value",
}

# הקודים בקובץ מרופדים באפסים לרוחב 5 (למשל 00885)
CODE_WIDTH = 5


def normalize_code(code) -> str:
    """מנרמל קוד תוצר/דגם לפורמט המאגר: '885', 885, '00885' -> '00885'"""
    return str(code).strip().zfill(CODE_WIDTH)


def make_key(tax_year, registration_year, car_type_code, make_code, model_code) -> tuple:
    return (
        str(tax_year).strip(),
        str(registration_year).strip(),
        str(car_type_code).strip(),
        normalize_code(make_code),
        normalize_code(model_code),
    )


@lru_cache(maxsize=1)
def load_db(csv_path: str = CSV_PATH) -> pd.DataFrame:
    """טוען את המאגר כמחרוזות (כדי לשמור אפסים מובילים) ומנקה רווחים."""
    df = pd.read_csv(csv_path, encoding="cp1255", dtype=str)
    df.columns = [c.strip() for c in df.columns]
    df = df[list(COLUMNS)].rename(columns=COLUMNS)
    for col in df.columns:
        df[col] = df[col].str.strip()
    df["usage_value"] = pd.to_numeric(df["usage_value"], errors="coerce")
    df["list_price"] = pd.to_numeric(df["list_price"], errors="coerce")
    df["key"] = list(zip(df.tax_year, df.registration_year, df.car_type_code, df.make_code, df.model_code))
    return df


@lru_cache(maxsize=1)
def _index(csv_path: str = CSV_PATH) -> dict:
    """אינדקס מילוני לשליפה מיידית לפי המפתח הרשמי."""
    return {record["key"]: record for record in load_db(csv_path).to_dict("records")}


def get_usage_value(tax_year, registration_year, car_type_code, make_code, model_code) -> dict:
    """שליפה לפי המפתח הרשמי. מחזיר {'found': True, ...פרטי הרכב} או {'found': False, 'error': ...}."""
    key = make_key(tax_year, registration_year, car_type_code, make_code, model_code)
    record = _index().get(key)
    if record is None:
        return {
            "found": False,
            "error": "לא נמצאה רשומה במאגר רשות המסים למפתח: "
                     f"שנת מס {key[0]}, שנת רישום {key[1]}, סוג רכב {key[2]}, תוצר {key[3]}, דגם {key[4]}",
        }
    result = {k: v for k, v in record.items() if k != "key"}
    result["found"] = True
    return result


def name_matches(query: str, make_name: str, commercial_name: str) -> bool:
    """התאמה חלקית ורפויה של טקסט שהעובד הקליד לרכב: כל מילה בטקסט צריכה להופיע ביצרן או בשם המסחרי
    (בכל סדר, ללא תלות באותיות). כך "BYD DOLPHIN" או "בי ווי די BYD DOLPHIN" או "מזדה 3" מוצאים את הרכב."""
    tokens = re.findall(r"[0-9A-Za-zא-ת]+", query.upper())
    haystack = f"{make_name or ''} {commercial_name or ''}".upper()
    words = set(re.findall(r"[0-9A-Zא-ת]+", haystack))
    # מילה קצרה (למשל "3") חייבת להתאים למילה שלמה; מילה ארוכה מספיק שתופיע בתוך מילה (DOLPHIN, COROLLA)
    return bool(tokens) and all(t in words if len(t) < 3 else t in haystack for t in tokens)


def search_by_name(commercial_name: str, registration_year=None, tax_year=2026, limit: int = 10) -> list[dict]:
    """חיפוש מועמדים לפי שם מסחרי (ושנת רישום). לא מחזיר שווי שימוש סופי - רק אפשרויות לבחירת העובד."""
    df = load_db()
    mask = df["tax_year"] == str(tax_year)
    if registration_year is not None:
        mask &= df["registration_year"] == str(registration_year)
    names = df.loc[mask, ["make_name", "commercial_name"]].drop_duplicates()     # התאמת טקסט רק לשמות ייחודיים
    matching = names[[name_matches(commercial_name, make, name)
                      for make, name in zip(names["make_name"], names["commercial_name"])]]
    mask &= df["make_name"].isin(matching["make_name"]) & df["commercial_name"].isin(matching["commercial_name"])
    cols = ["tax_year", "registration_year", "car_type_code", "make_code", "make_name",
            "model_code", "model_description", "commercial_name", "drive_code", "list_price", "usage_value"]
    return df.loc[mask, cols].head(limit).to_dict("records")
