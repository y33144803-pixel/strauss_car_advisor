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


_KEY_COLUMNS = ["tax_year", "registration_year", "car_type_code", "make_code", "model_code"]
_TEXT_COLUMNS = ["tax_year", "registration_year", "car_type_code", "make_code", "make_name", "model_code",
                 "model_description", "commercial_name", "drive_code"]


@lru_cache(maxsize=1)
def load_db(csv_path: str = CSV_PATH) -> pd.DataFrame:
    """טוען את המאגר כמחרוזות (כדי לשמור אפסים מובילים) ומנקה רווחים.
    עמודות הטקסט החוזרות נשמרות כ-category: כך המאגר (כ-78 אלף שורות) תופס הרבה פחות זיכרון בשרת קטן."""
    df = pd.read_csv(csv_path, encoding="cp1255", dtype=str)
    df.columns = [c.strip() for c in df.columns]
    df = df[list(COLUMNS)].rename(columns=COLUMNS)
    for col in df.columns:
        df[col] = df[col].str.strip()
    df["usage_value"] = pd.to_numeric(df["usage_value"], errors="coerce")
    df["list_price"] = pd.to_numeric(df["list_price"], errors="coerce")
    for col in _TEXT_COLUMNS:
        if col in df.columns:
            df[col] = df[col].astype("category")
    return df


@lru_cache(maxsize=1)
def _index(csv_path: str = CSV_PATH) -> dict:
    """אינדקס לשליפה מיידית: המפתח הרשמי -> מספר שורה במאגר (לא עותק של כל השורות, כדי לחסוך זיכרון)."""
    df = load_db(csv_path)
    keys = zip(*(df[col].astype(object) for col in _KEY_COLUMNS))
    return {key: position for position, key in enumerate(keys)}


def get_usage_value(tax_year, registration_year, car_type_code, make_code, model_code) -> dict:
    """שליפה לפי המפתח הרשמי. מחזיר {'found': True, ...פרטי הרכב} או {'found': False, 'error': ...}."""
    key = make_key(tax_year, registration_year, car_type_code, make_code, model_code)
    position = _index().get(key)
    record = load_db().iloc[position].to_dict() if position is not None else None
    if record is None:
        return {
            "found": False,
            "error": "לא נמצאה רשומה במאגר רשות המסים למפתח: "
                     f"שנת מס {key[0]}, שנת רישום {key[1]}, סוג רכב {key[2]}, תוצר {key[3]}, דגם {key[4]}",
        }
    result = dict(record)
    result["found"] = True
    return result


# שמות יצרנים באנגלית -> כתיב עברי במאגר (העובד או המודל עלולים לכתוב "Jaguar" במקום "יגואר")
MAKE_ALIASES = {
    "JAGUAR": ["יגואר"], "TOYOTA": ["טויוטה"], "HYUNDAI": ["יונדאי"], "KIA": ["קיה"], "MAZDA": ["מזדה"],
    "HONDA": ["הונדה"], "NISSAN": ["ניסאן"], "SUZUKI": ["סוזוקי"], "SUBARU": ["סובארו"], "MITSUBISHI": ["מיצובישי"],
    "LEXUS": ["לקסוס"], "VOLKSWAGEN": ["פולקסווגן", "פולקסוגן"], "VW": ["פולקסווגן", "פולקסוגן"], "SKODA": ["סקודה"],
    "SEAT": ["סיאט"], "AUDI": ["אאודי", "אודי"], "BMW": ["ב.מ.וו", "במוו"], "MERCEDES": ["מרצדס"], "BENZ": ["מרצדס"],
    "OPEL": ["אופל"], "FORD": ["פורד"], "CHEVROLET": ["שברולט"], "RENAULT": ["רנו"], "PEUGEOT": ["פיגו", "פיג'ו"],
    "CITROEN": ["סיטרואן"], "FIAT": ["פיאט"], "VOLVO": ["וולוו", "וולבו"], "TESLA": ["טסלה"], "BYD": ["בי ווי די"],
    "MG": ["מ.ג"], "CHERY": ["צ'רי"], "GEELY": ["ג'ילי", "גילי"], "JEEP": ["ג'יפ"], "PORSCHE": ["פורשה"],
    "MINI": ["מיני"], "DACIA": ["דאציה", "דאצ'יה"], "CUPRA": ["קופרה"], "ALFA": ["אלפא", "אלפה"], "LANDROVER": ["לנדרובר"],
    "ROVER": ["רובר"], "SSANGYONG": ["סאנגיונג"], "ISUZU": ["איסוזו"], "SMART": ["סמארט"], "POLESTAR": ["פולסטאר"],
    "CADILLAC": ["קאדילאק", "קדילאק"], "CHRYSLER": ["קרייזלר"], "DODGE": ["דודג'"], "LOTUS": ["לוטוס"],
    "MASERATI": ["מזארטי"], "FERRARI": ["פרארי"], "LAMBORGHINI": ["למבורגיני"], "BENTLEY": ["בנטלי"],
}


def name_matches(query: str, make_name: str, commercial_name: str) -> bool:
    """התאמה חלקית ורפויה של טקסט שהעובד הקליד לרכב: כל מילה בטקסט צריכה להופיע ביצרן או בשם המסחרי
    (בכל סדר, ללא תלות באותיות). כך "BYD DOLPHIN" או "בי ווי די BYD DOLPHIN" או "מזדה 3" מוצאים את הרכב."""
    tokens = re.findall(r"[0-9A-Za-zא-ת]+", query.upper())
    haystack = f"{make_name or ''} {commercial_name or ''}".upper()
    words = set(re.findall(r"[0-9A-Zא-ת]+", haystack))
    # מילה קצרה (למשל "3") חייבת להתאים למילה שלמה; מילה ארוכה מספיק שתופיע בתוך מילה (DOLPHIN, COROLLA)
    def found(token: str) -> bool:
        if token in MAKE_ALIASES:                                    # "JAGUAR" = "יגואר"
            return any(alias.upper() in haystack for alias in MAKE_ALIASES[token]) or token in haystack
        return token in words if len(token) < 3 else token in haystack

    return bool(tokens) and all(found(t) for t in tokens)


def search_by_name(commercial_name: str, registration_year=None, *, tax_year, limit: int = 10) -> list[dict]:
    """חיפוש מועמדים לפי שם מסחרי (ושנת רישום). לא מחזיר שווי שימוש סופי - רק אפשרויות לבחירת העובד."""
    df = load_db()
    mask = df["tax_year"] == str(tax_year)
    if registration_year is not None:
        mask &= df["registration_year"] == str(registration_year)
    names = df.loc[mask, ["make_name", "commercial_name"]].drop_duplicates()     # התאמת טקסט רק לשמות ייחודיים
    hits = [name_matches(commercial_name, make, name) for make, name in zip(names["make_name"], names["commercial_name"])]
    matching = names.loc[hits]          # (names[[...]] עם רשימה ריקה היה בוחר עמודות ולא שורות)
    # התאמה לפי הזוג (יצרן, שם מסחרי) ולא לפי כל אחד לחוד - אחרת נקבל גם שילובים שלא תאמו יחד לטקסט
    pairs = set(zip(matching["make_name"], matching["commercial_name"]))
    candidates = df.loc[mask & df["make_name"].isin(matching["make_name"])
                        & df["commercial_name"].isin(matching["commercial_name"])]
    keep = [(make, name) in pairs for make, name in zip(candidates["make_name"], candidates["commercial_name"])]
    cols = ["tax_year", "registration_year", "car_type_code", "make_code", "make_name",
            "model_code", "model_description", "commercial_name", "drive_code", "list_price", "usage_value"]
    found = candidates.loc[keep, cols]
    return found.head(limit).to_dict("records")
