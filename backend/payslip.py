"""קריאת תלוש שכר (תמונה / PDF) והפיכתו לנתוני העובד. אופציונלי: העובד יכול גם להקליד.

העיקרון: המודל רק קורא את המסמך לשדות מוגדרים; הערכים מוצגים לעובד לאישור, ורק אחרי האישור נשמרים
במצב השיחה דרך אותו כלי עדכון (שבודק תקינות). הקובץ עצמו לא נשמר - לא בדיסק ולא במסד הנתונים.
(הפרטים בפועל: קריאה "ודאית" - כל השדות ברורים ועקביים - נשמרת מיד והעובד רואה אותה בשיחה ויכול לתקן;
קריאה לא ודאית מוצגת בכרטיס אישור ונשמרת רק אחרי שהעובד מאשר.)
"""
import difflib
import logging
import re
import time
from typing import Optional

from pydantic import BaseModel, Field

from calc_engine import MAX_CREDIT_POINTS, MAX_MONTHLY_AMOUNT
from data_store import find_occupation, get_occupations

logger = logging.getLogger("strauss.payslip")

MAX_BYTES = 5 * 1024 * 1024
ALLOWED_TYPES = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}
TIMEOUT_MS = 25_000          # לכל ניסיון
TOTAL_BUDGET_SECONDS = 70    # סך ההמתנה לעובד, גם כשהשירות עמוס

# שדה -> תווית לתצוגה (הסדר גם הסדר בכרטיס האישור)
FIELD_LABELS = {
    "base_salary": "שכר בסיס (₪)",
    "commissions": "עמלות (₪)",
    "bonus": "בונוס (₪)",
    "pension_rate": "פנסיה - עובד (%)",
    "study_fund_rate": "קרן השתלמות - עובד (%)",
    "credit_points": "נקודות זיכוי",
}
OCCUPATION_LABEL = "תפקיד"

def build_prompt() -> str:
    names = "; ".join(o["occupation"] for o in get_occupations())
    return PROMPT + (
        "\n- job_title: התפקיד כפי שכתוב בתלוש, מילה במילה (או null)\n"
        "- occupation: אם התפקיד בתלוש מתאים לאחד העיסוקים הבאים - גם בניסוח שונה במעט או בלשון זכר/נקבה "
        "(למשל \"מנהל מכירות\" מתאים ל\"מנהל/ת אזור מכירות\") - שם העיסוק בדיוק כפי שמופיע ברשימה; "
        f"אם יש כמה אפשרויות סבירות או שאין התאמה סבירה - null. העיסוקים: {names}\n"
        "- gross_total: סה\"כ ברוטו כפי שכתוב בתלוש (או null)\n"
        "- pension_amount: סכום ניכוי הפנסיה של העובד בשקלים (או null)\n"
        "- study_fund_amount: סכום ניכוי קרן ההשתלמות של העובד בשקלים (או null)\n"
        "- image_quality: \"good\" רק אם כל הספרות חדות וקריאות לגמרי; \"fair\" או \"poor\" אם התמונה מטושטשת, מסובבת, "
        "חתוכה, מוטה, כתובה בכתב יד או שיש מספרים שחופפים\n"
        "- unclear_fields: רשימת שמות השדות שאינך בטוח בקריאה שלהם: טשטוש, ספרות שחופפות זו לזו או כתובות מעל "
        "מספר אחר, מחיקות, כמה מספרים אפשריים. רשימה ריקה רק אם כל המספרים ברורים לגמרי.")


PROMPT = (
    "זהו תלוש שכר ישראלי. חלץ ממנו אך ורק את השדות הבאים, כפי שהם כתובים בו, בלי לחשב ובלי לנחש:\n"
    "- base_salary: שכר היסוד / שכר בסיס חודשי (לא הסה\"כ ברוטו)\n"
    "- commissions: עמלות חודשיות (0 אם אין שורה כזו)\n"
    "- bonus: בונוס / פרמיה (0 אם אין שורה כזו)\n"
    "- pension_rate: אחוז הפרשת העובד לפנסיה (לא המעסיק), כמספר: 6 עבור 6%\n"
    "- study_fund_rate: אחוז הפרשת העובד לקרן השתלמות (לא המעסיק), כמספר: 2.5 עבור 2.5%\n"
    "- credit_points: נקודות זיכוי\n"
    "שדה שלא מופיע בתלוש במפורש - החזר null. אל תחשב אחוז מתוך סכום אם האחוז לא כתוב. "
    "אם המסמך אינו תלוש שכר - החזר null בכל השדות."
)


class PayslipFields(BaseModel):
    base_salary: Optional[float] = Field(None, description="שכר יסוד חודשי בשקלים")
    commissions: Optional[float] = Field(None, description="עמלות חודשיות בשקלים")
    bonus: Optional[float] = Field(None, description="בונוס חודשי בשקלים")
    pension_rate: Optional[float] = Field(None, description="אחוז פנסיה של העובד")
    study_fund_rate: Optional[float] = Field(None, description="אחוז קרן השתלמות של העובד")
    credit_points: Optional[float] = Field(None, description="נקודות זיכוי")
    job_title: Optional[str] = Field(None, description="התפקיד כפי שכתוב בתלוש")
    occupation: Optional[str] = Field(None, description="העיסוק מהרשימה, או null")
    gross_total: Optional[float] = Field(None, description="סה\"כ ברוטו")
    pension_amount: Optional[float] = Field(None, description="סכום ניכוי פנסיה")
    study_fund_amount: Optional[float] = Field(None, description="סכום ניכוי קרן השתלמות")
    unclear_fields: list[str] = Field(default_factory=list, description="שדות שהקריאה שלהם לא בטוחה")
    image_quality: Optional[str] = Field(None, description="good / fair / poor")


class PayslipError(Exception):
    """שגיאה שאפשר להציג לעובד כפי שהיא."""


def check_file(name: str, size: int) -> str:
    """בודק סוג וגודל; מחזיר את סוג ה-MIME או זורק PayslipError."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ALLOWED_TYPES:
        raise PayslipError("אפשר להעלות תלוש כ-PDF או כתמונה (JPG / PNG) בלבד.")
    if size > MAX_BYTES:
        raise PayslipError("הקובץ גדול מדי (עד 5MB).")
    if size == 0:
        raise PayslipError("הקובץ ריק.")
    return ALLOWED_TYPES[ext]


def clean_fields(raw: dict) -> dict:
    """משאיר רק ערכים הגיוניים (אחרת השדה נשאר ריק והבוט ישאל עליו). אותם גבולות כמו בהקלדה ידנית."""
    out = {}
    for key in FIELD_LABELS:
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        value = float(value)
        if key == "base_salary":
            ok = 0 < value <= MAX_MONTHLY_AMOUNT
        elif key in ("commissions", "bonus"):
            ok = 0 <= value <= MAX_MONTHLY_AMOUNT
        elif key == "credit_points":
            ok = 0 <= value <= MAX_CREDIT_POINTS
        else:
            ok = 0 <= value <= 100
        if ok:
            out[key] = value
    return out


class PayslipResult(BaseModel):
    fields: dict                 # שדות מספריים תקינים + occupation (שם עיסוק מהטבלה) אם זוהה בבירור
    certain: bool                # הכול ברור ועקבי - אפשר לשמור בלי לבקש אישור
    reasons: list[str] = []
    job_title: Optional[str] = None   # התפקיד כפי שנקרא, כשלא זוהה עיסוק מהטבלה
    occupation_from_title: Optional[str] = None   # התפקיד בתלוש, כשהעיסוק נבחר לפי דמיון ולא בהתאמה מדויקת


def closest_occupation(title: str) -> str | None:
    """העיסוק הדומה ביותר לתפקיד שנכתב בתלוש - רק אם הוא דומה מאוד וברור (ואין עיסוק שני כמעט באותו דמיון)."""
    norm = lambda text: re.sub(r"/(?:ת|ית)\b", "", text).strip()
    scored = sorted(((difflib.SequenceMatcher(None, norm(title), norm(o["occupation"])).ratio(), o["occupation"])
                     for o in get_occupations()), reverse=True)
    if scored and scored[0][0] >= 0.75 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.08):
        return scored[0][1]
    return None


def assess(raw: dict) -> PayslipResult:
    """מפיק את הנתונים הנקיים ומחליט אם הקריאה ודאית: בלי שדות לא ברורים, בלי ערכים לא הגיוניים שנפסלו,
    ועם סכום עקבי (שכר + עמלות + בונוס = סה"כ ברוטו, כשהסה"כ מופיע בתלוש)."""
    fields = clean_fields(raw)
    reasons = []
    if [f for f in (raw.get("unclear_fields") or []) if f]:
        reasons.append("חלק מהנתונים לא היו ברורים בתלוש")
    if str(raw.get("image_quality") or "good").lower() in ("fair", "poor"):
        reasons.append("איכות התמונה לא מספיק טובה")
    if [k for k in FIELD_LABELS if raw.get(k) is not None and k not in fields]:
        reasons.append("נמצאו ערכים לא הגיוניים")
    if "base_salary" not in fields:
        reasons.append("לא נמצא שכר בסיס")
    gross = raw.get("gross_total")
    if isinstance(gross, (int, float)) and not isinstance(gross, bool) and "base_salary" in fields:
        total = sum(fields.get(k, 0.0) for k in ("base_salary", "commissions", "bonus"))
        if abs(total - float(gross)) > 1:
            reasons.append("הסכומים בתלוש לא מסתכמים לברוטו שכתוב בו")
    base = sum(fields.get(k, 0.0) for k in ("base_salary", "commissions", "bonus"))
    for rate_key, amount_key in (("pension_rate", "pension_amount"), ("study_fund_rate", "study_fund_amount")):
        amount = raw.get(amount_key)
        if rate_key in fields and isinstance(amount, (int, float)) and not isinstance(amount, bool) and base > 0:
            if abs(base * fields[rate_key] / 100 - float(amount)) > 3:        # האחוז והסכום בתלוש לא תואמים
                reasons.append("אחוז ההפרשה לא תואם לסכום שבתלוש")
                break
    name = raw.get("occupation")
    occ = find_occupation(name) if isinstance(name, str) and name.strip() else None
    title = raw.get("job_title")
    title = title.strip()[:80] if isinstance(title, str) and title.strip() else None
    from_title = None
    if occ:
        fields["occupation"] = occ["occupation"]
        title = None
    else:
        guess = closest_occupation(title) if title else None
        if guess:
            fields["occupation"] = guess
            from_title, title = title, None
            # עיסוק שנבחר לפי דמיון קובע קבוצת זכאות - תמיד מוצג לאישור העובד
            reasons.append(f"העיסוק נבחר לפי דמיון לתפקיד שכתוב בתלוש (\"{from_title}\") - נא לוודא שהוא נכון")
    return PayslipResult(fields=fields, certain=not reasons, reasons=reasons, job_title=title,
                         occupation_from_title=from_title)


def _generate(model: str, api_key: str, data: bytes, mime: str) -> dict:
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=TIMEOUT_MS))
    response = client.models.generate_content(
        model=model,
        contents=[types.Part.from_bytes(data=data, mime_type=mime), build_prompt()],
        config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=PayslipFields),
    )
    parsed = response.parsed
    if parsed is None:
        return PayslipFields.model_validate_json(response.text).model_dump()
    return parsed.model_dump() if isinstance(parsed, PayslipFields) else dict(parsed)


def extract_payslip(data: bytes, mime: str, generate=_generate) -> PayslipResult:
    """קורא את התלוש. מנסה את המודלים והמפתחות לפי הסדר (כמו הסוכן). מחזיר שדות נקיים והערכת ודאות."""
    from agent import _gemini_keys, _provider, model_names
    _provider()                                           # טעינת .env
    keys = _gemini_keys()
    if not keys:
        raise PayslipError("קריאת תלוש לא זמינה כרגע. אפשר להקליד את הנתונים בשיחה.")
    last_error = None
    deadline = time.time() + TOTAL_BUDGET_SECONDS
    for model in model_names():
        for api_key in keys:
            if time.time() > deadline:
                break
            try:
                return assess(generate(model, api_key, data, mime))
            except Exception as e:                       # מכסה / עומס / פלט לא תקין: עוברים לניסיון הבא
                last_error = e
                logger.warning("קריאת תלוש נכשלה (%s): %s", model, type(e).__name__)
                time.sleep(0.2)
    logger.error("קריאת תלוש נכשלה בכל המודלים: %s", last_error)
    raise PayslipError("לא הצלחתי לקרוא את התלוש כרגע. אפשר לנסות שוב, או להקליד את הנתונים בשיחה.")


def apply_payslip(session_id: str, fields: dict) -> dict:
    """שומר את הערכים שהעובד אישר, דרך כלי העדכון הרגיל (כולל בדיקות תקינות וחישוב מחדש)."""
    from agent_tools import update_employee_data
    values = clean_fields(fields)
    invalid = [FIELD_LABELS[k].replace(" (₪)", "").replace(" (%)", "") for k in FIELD_LABELS if k in fields and k not in values]
    if invalid:                                      # ערך לא תקין (למשל שכר 0) לא נזרק בשקט
        return {"ok": False, "error": "ערך לא תקין: " + ", ".join(invalid)}
    occupation = fields.get("occupation")
    if isinstance(occupation, str) and find_occupation(occupation):
        values["occupation"] = occupation
    outcome = update_employee_data(session_id, **values)
    if outcome.get("ok"):
        import db
        state = db.load_state(session_id)
        for key in values:                               # מקור הנתונים שנשמרו עכשיו: התלוש
            state.sources[key] = "payslip"
        db.save_state(state)
    return outcome


def summary_text(fields: dict) -> str:
    """משפט קצר עם מה שנקרא (להודעה בשיחה)."""
    parts = [f"{FIELD_LABELS[k].replace(' (₪)', '').replace(' (%)', '')} {fields[k]:g}" for k in FIELD_LABELS if k in fields]
    if "occupation" in fields:
        parts.insert(0, f"{OCCUPATION_LABEL} {fields['occupation']}")
    return ", ".join(parts)
