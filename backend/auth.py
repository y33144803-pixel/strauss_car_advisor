"""התחברות: משתמשים (עובד / נציג), סיסמאות מגובבות, אסימוני התחברות והגנה בסיסית מפני ניחוש סיסמאות.

המשתמשים מוגדרים מראש במשתנה הסביבה AUTH_USERS (בקובץ .env או בהגדרות הפלטפורמה), ונטענים למסד בעליית המערכת:

    AUTH_USERS="dana:סיסמה1:employee:דנה כהן;moshe:סיסמה2:rep:משה לוי"

כל רשומה: שם_משתמש:סיסמה:תפקיד:שם_תצוגה (מופרדות ב-; או בשורה חדשה). תפקיד: employee / rep / admin.
בסיסמה ובשם המשתמש אסור להשתמש ב-: וב-;.
משתמש שכבר קיים במסד לא נדרס - כך שינוי סיסמה שבוצע באזור האישי נשמר (כל עוד המסד נשמר).
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import time

from dotenv import load_dotenv

import db

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROLES = ("employee", "rep", "admin")
ROLE_LABELS = {"employee": "עובד", "rep": "נציג תחבורה", "admin": "מנהל"}
MIN_PASSWORD_LENGTH = 6
USERNAME_RE = re.compile(r"^[0-9A-Za-zא-ת._-]{3,30}$")      # אותיות (גם עבריות), ספרות, נקודה, קו תחתון ומקף
MAX_FAILED_ATTEMPTS = 5
LOCK_SECONDS = 60
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}

_failures: dict[str, list[float]] = {}        # שם משתמש -> זמני כישלון אחרונים


def auth_enabled() -> bool:
    """ההתחברות פעילה תמיד, אלא אם הוגדר במפורש AUTH_DISABLED=1 (לפיתוח מקומי ובדיקות)."""
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    return os.environ.get("AUTH_DISABLED", "").strip().lower() not in ("1", "true", "yes")


# ---------------------------------------------------------------- סיסמאות

def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


# ---------------------------------------------------------------- טעינת משתמשים

def parse_users(text: str) -> list[dict]:
    """מפרש את AUTH_USERS. רשומה שגויה נזרקת עם הודעה ברורה (עדיף להיכשל בעליה מאשר להשאיר משתמש חצי-מוגדר)."""
    users = []
    for entry in text.replace("\n", ";").split(";"):
        entry = entry.strip()
        if not entry:
            continue
        parts = [part.strip() for part in entry.split(":")]
        if len(parts) != 4 or not all(parts):
            raise ValueError(f"רשומת משתמש שגויה ב-AUTH_USERS (נדרש שם:סיסמה:תפקיד:שם תצוגה): '{entry[:20]}...'")
        username, password, role, display_name = parts
        if role not in ROLES:
            raise ValueError(f"תפקיד לא מוכר '{role}' למשתמש {username} (אפשרויות: {', '.join(ROLES)})")
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"הסיסמה של {username} קצרה מדי (לפחות {MIN_PASSWORD_LENGTH} תווים)")
        users.append({"username": username, "password": password, "role": role, "display_name": display_name})
    return users


def seed_users() -> int:
    """טוען את המשתמשים מ-AUTH_USERS למסד (רק מי שעוד לא קיים). מחזיר כמה נוצרו."""
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    created = load_backup()                      # קודם הגיבוי (כולל שינויי סיסמה), ואז AUTH_USERS למי שחסר
    for user in parse_users(os.environ.get("AUTH_USERS", "")):
        if db.create_user_if_missing(user["username"], user["display_name"], user["role"],
                                     hash_password(user["password"])):
            created += 1
    return created


# ---------------------------------------------------------------- כניסה

class LoginError(Exception):
    """כניסה נכשלה (הודעה בעברית שמוצגת למשתמש)."""


def _recent_failures(username: str) -> list[float]:
    cutoff = time.time() - LOCK_SECONDS
    recent = [t for t in _failures.get(username, []) if t >= cutoff]
    _failures[username] = recent
    return recent


def authenticate(username: str, password: str) -> dict:
    """בודק שם משתמש וסיסמה. מחזיר {username, display_name, role} או זורק LoginError.
    אחרי כמה כישלונות רצופים החשבון ננעל לדקה (הגנה מפני ניחוש סיסמאות)."""
    username = (username or "").strip()
    if len(_recent_failures(username)) >= MAX_FAILED_ATTEMPTS:
        raise LoginError("יותר מדי ניסיונות כושלים. אפשר לנסות שוב בעוד דקה.")
    user = db.get_user(username)
    # מבצעים גיבוב גם כשהמשתמש לא קיים, כדי שזמן התשובה לא יסגיר אם שם המשתמש קיים
    stored = user["password_hash"] if user else hash_password("dummy-password")
    ok = verify_password(password or "", stored)
    if not user or not ok:
        _failures.setdefault(username, []).append(time.time())
        raise LoginError("שם משתמש או סיסמה שגויים.")
    _failures.pop(username, None)
    return {"username": user["username"], "display_name": user["display_name"], "role": user["role"]}


def change_password(username: str, old_password: str, new_password: str) -> None:
    user = db.get_user(username)
    if not user or not verify_password(old_password or "", user["password_hash"]):
        raise LoginError("הסיסמה הנוכחית שגויה.")
    if len(new_password or "") < MIN_PASSWORD_LENGTH:
        raise LoginError(f"הסיסמה החדשה קצרה מדי (לפחות {MIN_PASSWORD_LENGTH} תווים).")
    if new_password == old_password:
        raise LoginError("הסיסמה החדשה זהה לנוכחית.")
    db.set_password_hash(username, hash_password(new_password))


def change_username(username: str, password: str, new_username: str) -> dict:
    """מחליף שם משתמש (נדרשת הסיסמה הנוכחית). מחזיר את המשתמש המעודכן. השיחות וההתחברות נשמרות."""
    new_username = (new_username or "").strip()
    user = db.get_user(username)
    if not user or not verify_password(password or "", user["password_hash"]):
        raise LoginError("הסיסמה שגויה.")
    if not USERNAME_RE.match(new_username):
        raise LoginError("שם משתמש חייב להכיל 3 עד 30 תווים: אותיות, ספרות, נקודה, קו תחתון או מקף (בלי רווחים).")
    if new_username == username:
        raise LoginError("שם המשתמש החדש זהה לנוכחי.")
    if not db.rename_user(username, new_username):
        raise LoginError("שם המשתמש הזה כבר תפוס.")
    return {"username": new_username, "display_name": user["display_name"], "role": user["role"]}


# ---------------------------------------------------------------- ניהול משתמשים (מנהל)

def create_user(username: str, password: str, role: str, display_name: str) -> None:
    """מוסיף משתמש חדש (עובד / נציג / מנהל). זורק LoginError עם הודעה בעברית."""
    username = (username or "").strip()
    display_name = (display_name or "").strip()
    if role not in ROLES:
        raise LoginError("תפקיד לא מוכר.")
    if not USERNAME_RE.match(username):
        raise LoginError("שם משתמש חייב להכיל 3 עד 30 תווים: אותיות, ספרות, נקודה, קו תחתון או מקף (בלי רווחים).")
    if not display_name:
        raise LoginError("יש להזין שם תצוגה.")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise LoginError(f"הסיסמה קצרה מדי (לפחות {MIN_PASSWORD_LENGTH} תווים).")
    if not db.create_user(username, display_name, role, hash_password(password)):
        raise LoginError("שם המשתמש הזה כבר תפוס.")


def reset_password(username: str, new_password: str) -> None:
    """איפוס סיסמה על ידי מנהל (בלי הסיסמה הנוכחית). המשתמש מנותק מכל המכשירים."""
    if not db.get_user(username):
        raise LoginError("המשתמש לא נמצא.")
    if len(new_password or "") < MIN_PASSWORD_LENGTH:
        raise LoginError(f"הסיסמה קצרה מדי (לפחות {MIN_PASSWORD_LENGTH} תווים).")
    db.set_password_hash(username, hash_password(new_password))
    db.clear_tokens(username)


def delete_user(acting_admin: str, username: str) -> None:
    """מחיקת משתמש. אי אפשר למחוק את עצמך או את המנהל האחרון."""
    if username == acting_admin:
        raise LoginError("אי אפשר למחוק את המשתמש שלך.")
    target = db.get_user(username)
    if not target:
        raise LoginError("המשתמש לא נמצא.")
    if target["role"] == "admin" and sum(1 for u in db.list_users() if u["role"] == "admin") <= 1:
        raise LoginError("אי אפשר למחוק את המנהל האחרון.")
    db.delete_user(username)


# ---------------------------------------------------------------- גיבוי ושחזור משתמשים

BACKUP_FILE = os.path.join(BASE_DIR, "users_backup.json")
_BACKUP_FIELDS = ("username", "display_name", "role", "password_hash")


def export_users_json() -> str:
    """כל המשתמשים כ-JSON (עם גיבוב הסיסמה, לא סיסמה גלויה). שיחות לא נכללות."""
    return json.dumps({"users": db.export_users()}, ensure_ascii=False, indent=2)


def import_users_json(text: str) -> int:
    """טוען משתמשים מגיבוי. זורק LoginError אם הקובץ לא תקין. משתמש קיים לא נדרס. מחזיר כמה נוספו."""
    try:
        users = json.loads(text)["users"]
        for u in users:
            if not all(isinstance(u.get(f), str) and u[f] for f in _BACKUP_FIELDS) or u["role"] not in ROLES \
                    or u["password_hash"].count("$") != 2:
                raise ValueError
    except (ValueError, KeyError, TypeError, AttributeError):
        raise LoginError("קובץ הגיבוי לא תקין.") from None
    return db.import_users(users)


def load_backup() -> int:
    """בעליית השרת: טוען משתמשים מ-users_backup.json (אם קיים) ומהמשתנה USERS_BACKUP_JSON (אם הוגדר).
    כך משתמשים שנוספו במסך הניהול חוזרים אחרי פריסה מחודשת בענן. קובץ פגום מדלגים עליו ולא מפיל את השרת."""
    added = 0
    sources = []
    if os.path.exists(BACKUP_FILE):
        with open(BACKUP_FILE, encoding="utf-8") as handle:
            sources.append(handle.read())
    if os.environ.get("USERS_BACKUP_JSON", "").strip():
        sources.append(os.environ["USERS_BACKUP_JSON"])
    for text in sources:
        try:
            added += import_users_json(text)
        except LoginError:
            pass
    return added
