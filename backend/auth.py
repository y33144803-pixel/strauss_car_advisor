"""התחברות: משתמשים (עובד / נציג), סיסמאות מגובבות, אסימוני התחברות והגנה בסיסית מפני ניחוש סיסמאות.

המשתמשים מוגדרים מראש במשתנה הסביבה AUTH_USERS (בקובץ .env או בהגדרות הפלטפורמה), ונטענים למסד בעליית המערכת:

    AUTH_USERS="dana:סיסמה1:employee:דנה כהן;moshe:סיסמה2:rep:משה לוי"

כל רשומה: שם_משתמש:סיסמה:תפקיד:שם_תצוגה (מופרדות ב-; או בשורה חדשה). תפקיד: employee / rep.
בסיסמה ובשם המשתמש אסור להשתמש ב-: וב-;.
משתמש שכבר קיים במסד לא נדרס - כך שינוי סיסמה שבוצע באזור האישי נשמר (כל עוד המסד נשמר).
"""
import hashlib
import hmac
import os
import secrets
import time

from dotenv import load_dotenv

import db

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROLES = ("employee", "rep")
MIN_PASSWORD_LENGTH = 6
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
    created = 0
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
