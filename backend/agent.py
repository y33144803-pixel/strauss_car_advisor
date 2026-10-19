"""הסוכן: לולאת שיחה עם מודל השפה דרך LlamaIndex, שמפעילה את הכלים עד שיש תשובה לעובד.

בכל תור הסוכן מקבל: פרומפט מערכת (prompts/system_prompt.md) + תמונת מצב עדכנית מה-State
+ היסטוריית ההודעות. ההיסטוריה וה-State נשמרים ב-SQLite, כך שהשיחה שורדת רענון.
"""
import json
import logging
import os
import re
import time
from functools import lru_cache

from dotenv import load_dotenv

import db
from actions import format_result_message
from agent_tools import REP_FIELDS, apply_representative_answers
from agent_tools_registry import build_tools, load_specs

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts", "system_prompt.md")
MAX_TOOL_ROUNDS = 10      # הגנה מלולאה אינסופית של קריאות כלים
HISTORY_LIMIT = 40        # כמה הודעות אחרונות נשלחות למודל

GREETING = ("שלום! אני יועץ הרכב של שטראוס 🚗\n"
            "אפשר לבדוק איך ישתנה הנטו שלך אם תחליף/י רכב, תעבור/י לרכב חשמלי או תוותר/י על הרכב.\n"
            "מה תרצה/י לעשות?")


logger = logging.getLogger("strauss.agent")

DEFAULT_MODELS = {"gemini": "gemini-3.1-flash-lite", "openai": "gpt-4o"}
# מודלי גיבוי כשאין LLM_FALLBACK_MODELS ב-.env (הגדרה ריקה = בלי גיבוי)
DEFAULT_FALLBACKS = {"gemini": ["gemini-3.5-flash-lite", "gemini-3.5-flash"], "openai": []}


class LLMNotConfigured(RuntimeError):
    """חסר מפתח API או ספק לא מוכר - ניסיון חוזר לא יעזור."""


class LLMUnavailable(RuntimeError):
    """כל המודלים נכשלו (עומס, מכסה או בעיית רשת) - שווה לנסות שוב בעוד רגע."""


def friendly_error(error: Exception) -> str:
    """הודעה למשתמש (בלי פרטים טכניים; הפרטים נשמרים ביומן)."""
    if isinstance(error, LLMNotConfigured):
        return "הבוט עדיין לא מוגדר (חסר מפתח לשירות הבינה המלאכותית). יש לפנות למי שהתקין את המערכת."
    if isinstance(error, LLMUnavailable):
        return "שירות הבינה המלאכותית עמוס או לא זמין כרגע. אפשר ללחוץ על \"נסי שוב\" בעוד רגע."
    return "אירעה תקלה בלתי צפויה. אפשר ללחוץ על \"נסי שוב\"."


def _provider() -> str:
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    return os.environ.get("LLM_PROVIDER", "gemini").lower()


def model_names() -> list[str]:
    """המודל הראשי ואחריו מודלי הגיבוי (LLM_FALLBACK_MODELS, מופרדים בפסיק)."""
    provider = _provider()
    main = os.environ.get("LLM_MODEL") or DEFAULT_MODELS.get(provider)
    raw = os.environ.get("LLM_FALLBACK_MODELS")
    fallbacks = (DEFAULT_FALLBACKS.get(provider, []) if raw is None
                 else [m.strip() for m in raw.split(",") if m.strip()])
    return [main] + [m for m in fallbacks if m != main]


GEMINI_KEY_NAMES = ("GOOGLE_API_KEY", "GOOGLE_API_KEY_2", "GOOGLE_API_KEY_3")


def api_key_count() -> int:
    """כמה מפתחות Gemini מוגדרים ב-.env (GOOGLE_API_KEY, GOOGLE_API_KEY_2, GOOGLE_API_KEY_3). לפחות 1.
    מכסה נספרת לכל פרויקט Google, ולכן מפתח מפרויקט נפרד נותן גיבוי כשהמכסה של הראשון נגמרת."""
    _provider()                                   # טעינת .env
    if _provider() != "gemini":
        return 1
    return max(1, sum(1 for name in GEMINI_KEY_NAMES if os.environ.get(name, "").strip()))


def _gemini_keys() -> list[str]:
    return [os.environ[name].strip() for name in GEMINI_KEY_NAMES if os.environ.get(name, "").strip()]


@lru_cache(maxsize=16)
def get_llm(model: str, key_index: int = 0):
    """מודל שפה אחד (נוצר פעם אחת לכל מודל ומפתח) לפי LLM_PROVIDER והמפתח המתאים ב-.env."""
    provider = _provider()
    if provider == "gemini":
        keys = _gemini_keys()
        if not keys or key_index >= len(keys):
            raise LLMNotConfigured("חסר GOOGLE_API_KEY בקובץ .env")
        api_key = keys[key_index]
        from llama_index.llms.google_genai import GoogleGenAI
        # לא קובעים טמפרטורה: מודלי Gemini 3 ומעלה עלולים להיכשל אם היא מוגדרת
        from google.genai import types
        # מגבלת זמן קצרה (באלפיות שנייה) וללא ניסיונות חוזרים פנימיים - מודל גיבוי הוא הניסיון החוזר.
        # כך גם במקרה הגרוע (אין חיבור) ההמתנה מוגבלת לכ-20 שניות לכל מודל ולא לדקות.
        return GoogleGenAI(model=model, api_key=api_key, max_retries=0,
                           http_options=types.HttpOptions(timeout=20_000))

    if provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise LLMNotConfigured("חסר OPENAI_API_KEY בקובץ .env")
        from llama_index.llms.openai import OpenAI
        return OpenAI(model=model, api_key=api_key, temperature=0)

    raise LLMNotConfigured(f"LLM_PROVIDER לא מוכר: '{provider}' (אפשרויות: gemini / openai)")


# שם מודל -> עד מתי לא מנסים אותו. מכסה שנגמרה (429) נמשכת זמן רב; עומס רגעי (503) קצר.
_COOLDOWN: dict[str, float] = {}
COOLDOWN_SECONDS = {"quota": 600, "overload": 45}


def _cooldown_key(model: str, key_index: int) -> str:
    return model if key_index == 0 else f"{model}#מפתח{key_index + 1}"


def _start_cooldown(model: str, error_message: str) -> None:
    upper = error_message.upper()
    if "429" in upper or "RESOURCE_EXHAUSTED" in upper:
        _COOLDOWN[model] = time.time() + COOLDOWN_SECONDS["quota"]
    elif any(code in upper for code in ("503", "504", "UNAVAILABLE", "DEADLINE")):
        _COOLDOWN[model] = time.time() + COOLDOWN_SECONDS["overload"]


class FallbackLLM:
    """מנסה את המודל הראשי, ואם הוא לא זמין (עומס / מכסה) עובר למודל הגיבוי הבא."""

    def __init__(self, models: list[str], keys: int = 1):
        self.models = models
        self.keys = keys            # כמה מפתחות זמינים: על כל מודל מנסים אותם לפי הסדר, לפני המעבר למודל הבא
        self._last = None

    def chat_with_tools(self, tools, **kwargs):
        errors = []
        # מודל שנכשל בגלל מכסה/עומס נחסך מניסיונות בהמשך (כמה דקות) - אחרת כל תור מבזבז שניות על מודל חסום.
        # אם כל המודלים בהפסקה, מנסים את כולם בכל זאת.
        now = time.time()
        everything = [(m, k) for m in self.models for k in range(self.keys)]
        candidates = [c for c in everything if _COOLDOWN.get(_cooldown_key(*c), 0) <= now] or everything
        for name, key_index in candidates:
            # תשובה פגומה (MALFORMED_RESPONSE) היא תקלה אקראית של המודל - שווה ניסיון נוסף על אותו מודל.
            # עומס או מכסה (503/429) - עוברים מיד למודל הבא.
            attempts_left = 2
            while attempts_left:
                attempts_left -= 1
                try:
                    llm = get_llm(name) if key_index == 0 else get_llm(name, key_index)
                    response = llm.chat_with_tools(tools, **kwargs)
                    self._last = llm
                    return response
                except LLMNotConfigured:
                    raise                  # חסר מפתח / ספק לא מוכר - אין טעם לנסות מודל אחר
                except Exception as e:
                    message = str(e)[:200]
                    retry_same = attempts_left and "MALFORMED" in message.upper()
                    label = _cooldown_key(name, key_index)
                    logger.warning("המודל %s נכשל (%s): %s", label, "ניסיון חוזר" if retry_same else "עוברים לבא",
                                   message)
                    if not retry_same:
                        errors.append(f"{label}: {message[:120]}")
                        _start_cooldown(label, message)
                        break
        logger.error("כל המודלים נכשלו: %s", " | ".join(errors))
        raise LLMUnavailable("אף מודל לא זמין כרגע: " + " | ".join(errors))

    def get_tool_calls_from_response(self, response, **kwargs):
        return self._last.get_tool_calls_from_response(response, **kwargs)


def load_system_prompt() -> str:
    with open(PROMPT_PATH, encoding="utf-8") as f:
        return f.read()


EMPLOYEE_FIELD_LABELS = {
    "eligibility_group": "קבוצת זכאות", "base_salary": "שכר בסיס", "commissions": "עמלות", "bonus": "בונוס",
    "pension_rate": "אחוז פנסיה", "study_fund_rate": "אחוז קרן השתלמות", "credit_points": "נקודות זיכוי",
    "occupation": "עיסוק", "children_under_23": "ילדים עד גיל 23", "children_ages": "גילאי הילדים",
}


def state_snapshot(session_id: str) -> str:
    """תמונת מצב קצרה של מה שכבר נאסף ומה חסר - מצורפת לפרומפט בכל תור."""
    state = db.load_state(session_id)
    snapshot = {
        "employee": state.employee.model_dump(exclude_none=True),
        "current_situation": state.current.model_dump(
            include={"kind", "label", "usage_value", "participation", "maintenance", "is_electric"},
            exclude_none=True),
        "target": state.target.model_dump(
            include={"kind", "label", "participation", "participation_status", "eligibility_reason"},
            exclude_none=True),
        # מה שכבר נקבע ואומת: הסוכן לא שואל עליו שוב ולא מטיל בו ספק (למשל קבוצה שנשלפה מהעיסוק)
        "already_known_do_not_ask_again": (
            [label for field, label in EMPLOYEE_FIELD_LABELS.items() if getattr(state.employee, field) is not None]
            + (["הרכב הנוכחי / המצב היום"] if state.current.kind else [])
            + (["החלופה המבוקשת"] if state.target.kind else [])),
        "missing_required_fields": state.missing_required_fields(),
        "waiting_for_representative": state.waiting_for_representative,
        "pending_representative_questions": [REP_FIELDS.get(f, f) for f in state.pending_questions],
        "calculation_ready": state.calculation_ready,
        "has_result": state.last_result is not None,
        "result_is_stale": state.result_is_stale,
    }
    if state.last_result is not None:
        # תוצאת החישוב האחרונה - כדי שהסוכן יוכל לענות על שאלות המשך בלי להמציא מספרים
        snapshot["last_result"] = {k: state.last_result[k] for k in
                                   ("status", "net_today", "net_alternative", "diff", "changes", "explanation")}
    return json.dumps(snapshot, ensure_ascii=False, indent=1)


def _system_message(session_id: str):
    from llama_index.core.llms import ChatMessage, MessageRole
    content = (load_system_prompt() + "\n\n# מצב השיחה (נתונים שמורים - מקור האמת)\n```json\n"
               + state_snapshot(session_id) + "\n```")
    return ChatMessage(role=MessageRole.SYSTEM, content=content)


def _history_messages(messages: list[dict]) -> list:
    from llama_index.core.llms import ChatMessage, MessageRole
    roles = {"user": MessageRole.USER, "assistant": MessageRole.ASSISTANT}
    return [ChatMessage(role=roles[m["role"]], content=m["content"])
            for m in messages[-HISTORY_LIMIT:] if m["role"] in roles]


STATUS_ANALYZING = "בוחן את הנתונים שלך..."          # לפני הפנייה הראשונה למודל
STATUS_COMPOSING = "מנסח את התשובה..."                 # אחרי שכלי הסתיים והמודל כותב את התשובה
TOOL_STATUS = {spec["name"]: spec["status"] for spec in load_specs() if "status" in spec}   # מ-tool_specs.json


PICKER_MESSAGE = re.compile(r"^אני רוצה (לבדוק את .*\(מזהה CAR-\d+\)|לוותר על הרכב)$")


def picker_context_note(messages: list[dict]) -> str | None:
    """כשההודעה האחרונה נשלחה מלחיצה בבורר (ולא הוקלדה): הערת מערכת שמזכירה לסוכן את ההודעה המילולית שקדמה
    לה ואת תשובתו אליה, כדי שיסגור את המעגל ולא יתעלם מהשיחה. None אם אין הקשר כזה."""
    if len(messages) < 3 or messages[-1]["role"] != "user" or not PICKER_MESSAGE.match(messages[-1]["content"].strip()):
        return None
    earlier = messages[:-1]
    last_assistant = next((m for m in reversed(earlier) if m["role"] == "assistant"), None)
    last_typed = next((m for m in reversed(earlier) if m["role"] == "user"
                       and not PICKER_MESSAGE.match(m["content"].strip())), None)
    if not last_assistant or not last_typed or earlier.index(last_typed) < earlier.index(last_assistant) - 1:
        return None
    return (f"[הערת מערכת] ההודעה האחרונה נשלחה מלחיצה בבורר הרכבים, לא הוקלדה. ההודעה המילולית הקודמת של העובד: "
            f"\"{last_typed['content'][:200]}\". התשובה שלך אליה היתה: \"{last_assistant['content'][:300]}\". "
            "אם ההודעה הקודמת נדחתה או לא נענתה, פתח/י את התגובה בהתייחסות אליה (בקצרה), ורק אחר כך עבר/י לרכב "
            "שנבחר. אל תתעלם/י ממנה.")


def start_session(session_id: str) -> list[dict]:
    """מחזיר את הודעות השיחה; לשיחה חדשה - מוסיף הודעת פתיחה."""
    messages = db.load_messages(session_id)
    if not messages:
        messages = [{"role": "assistant", "content": GREETING}]
        db.save_messages(session_id, messages)
    return messages


def _continuation_line(session_id: str) -> str:
    """אחרי שתשובת נציג נקלטה ועוד אין חישוב: מה חסר כדי להמשיך (במקום "נמשיך" כללי שלא מוביל לשום מקום)."""
    state = db.load_state(session_id)
    if state.waiting_for_representative:
        waiting = ", ".join(REP_FIELDS.get(f, f) for f in state.pending_questions)
        return f"אני עדיין ממתין לתשובת הנציג בנושא: {waiting}."
    missing = state.missing_required_fields()
    if missing:
        return f"כדי שאוכל לחשב את הנטו חסר לי עוד: {', '.join(missing)}. אפשר לכתוב לי אותם כאן."
    return "כל הנתונים קיימים. כתוב/י לי אם להשוות לחלופה נוספת או לתקן נתון."


def check_representative_updates(session_id: str) -> str | None:
    """קליטת תשובות נציג (מופעל בכל טעינה של מסך העובד). אם יש חדש - מוסיף הודעה לשיחה ומחזיר אותה."""
    events = apply_representative_answers(session_id)
    if not events:
        return None
    lines = []
    for ev in events:
        if "field" in ev:
            text = f"הנציג ענה לגבי {ev['field']}" + (f" ({ev['car']})" if ev.get("car") else "")
            if ev["value"] is not None:
                text += f": {ev['value']:,.0f} ₪"
            if ev.get("effective_from"):
                text += f", בתוקף מ-{ev['effective_from']}"
            if ev.get("note"):
                text += f" ({ev['note']})"
            lines.append(text + ".")
    recalculated = next((ev["recalculated"] for ev in events if "recalculated" in ev), None)
    if recalculated:
        lines.append("\nהנה ההשוואה המעודכנת:\n\n" + format_result_message(recalculated))
    else:
        lines.append(_continuation_line(session_id))
    message = "\n".join(lines)
    db.append_messages(session_id, [{"role": "assistant", "content": message, "source": "representative"}])
    return message


SAFE_REPLY = ("כדי שלא אציג לך מספרים שלא אומתו, אני צריך להריץ את החישוב על הנתונים השמורים. "
              "אפשר לנסח שוב את הבקשה, או לתקן נתון שחסר?")
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set[float]:
    found = set()
    for token in _NUMBER_RE.findall(text):
        try:
            found.add(float(token.replace(",", "").rstrip(".")))
        except ValueError:
            pass
    return found


def _evidence(session_id: str, messages: list[dict], tool_log: list[dict]) -> set[float]:
    """כל המספרים שמותר לבוט להזכיר: מהעובד, מתוצאות כלים ומהמצב השמור."""
    text = " ".join(m["content"] for m in messages if m["role"] == "user")
    text += json.dumps([t["output"] for t in tool_log], ensure_ascii=False)
    text += state_snapshot(session_id)
    return _numbers(text)


def unsupported_amounts(reply: str, evidence: set[float]) -> list[str]:
    """סכומים (3 ספרות ומעלה) שמופיעים בתשובה ואינם מגובים בראיות. סטייה של 1 מותרת (עיגול)."""
    bad = []
    for token in _NUMBER_RE.findall(reply):
        value = float(token.replace(",", "").rstrip("."))
        if value < 100:
            continue                      # אחוזים, נקודות זיכוי, מספרי רשימה
        if not any(abs(value - e) <= 1 for e in evidence):
            bad.append(token)
    return sorted(set(bad))


def pending_user_message(session_id: str) -> str | None:
    """ההודעה האחרונה של העובד אם עוד לא נענתה (למשל כי השירות לא היה זמין), אחרת None."""
    messages = db.load_messages(session_id)
    return messages[-1]["content"] if messages and messages[-1]["role"] == "user" else None


# הבוט לא יודע כמה זמן לוקח לנציג לענות (אין לו מידע כזה), ולכן משפטים שמעריכים זמן טיפול נמחקים מהתשובה
_TIME_CLAIM_RE = re.compile(
    r"ימי עסקים|ימי עבודה|שעות עבודה|זמן (?:ה)?(?:טיפול|המתנה|תגובה)|"
    r"(?:בדרך כלל|לרוב|בממוצע)[^.!?\n]*(?:יום|ימים|שעות|שעה|דקות)|"
    r"(?:תוך|בתוך|עד) (?:\S+ )?(?:יום|יומיים|ימים|שעות|שעה|דקות)\b")


def strip_time_claims(text: str) -> str:
    """מוחק משפטים שמבטיחים או מעריכים זמן תשובה של הנציג - מידע שאין לבוט ולכן מומצא."""
    parts = [part for part in re.split(r"(?<=[.!?])[ \t]+|\n", text) if part.strip()]
    kept = [part for part in parts if not _TIME_CLAIM_RE.search(part)]
    if len(kept) == len(parts):
        return text                                   # לא נמצא דבר: הטקסט המקורי נשאר בדיוק כמות שהוא
    return "\n\n".join(kept).strip()


# טענה "שלחתי / העברתי שאלה לנציג" חייבת להיות מגובה בפעולה אמיתית (הכלי request_representative_info הצליח, או שיש
# שאלה פתוחה לנציג בשיחה). אחרת הבוט הבטיח משהו שלא קרה, ובמסך הנציג אין שום שאלה
_REP_CLAIM_RE = re.compile(
    r"(?:שלחתי|שלחנו|פניתי|פנינו|העברתי|הפניתי|נשלחה|הועברה|הופנתה|פתחתי)[^.!?\n]*נציג|"
    r"נציג[^.!?\n]*(?:קיבל|יקבל|קיבלו|יענה|ישיב|יחזור)")


def claims_rep_contact(text: str) -> bool:
    return bool(_REP_CLAIM_RE.search(text))


def strip_rep_claims(text: str) -> str:
    parts = [part for part in re.split(r"(?<=[.!?])[ \t]+|\n", text) if part.strip()]
    return "\n\n".join(part for part in parts if not _REP_CLAIM_RE.search(part)).strip()


def rep_contact_is_backed(session_id: str, tool_log: list) -> bool:
    if any(t["tool"] == "request_representative_info" and (t["output"] or {}).get("ok") for t in tool_log):
        return True
    return db.has_open_questions(session_id)


def chat(session_id: str, user_text: str | None = None, llm=None, on_step=None, on_status=None) -> dict:
    """תור אחד בשיחה: הודעת העובד -> (קריאות כלים) -> תשובת הבוט.
    בלי user_text: ממשיכים הודעה שכבר נשמרה ולא נענתה (ניסיון חוזר, בלי לשלוח אותה פעמיים).
    on_step: פונקציה שנקראת אחרי כל כלי שהופעל (המסך משתמש בה לעדכון כרטיס הנתונים בזמן אמת).
    on_status: פונקציה שמקבלת טקסט סטטוס אמיתי לפי מה שקורה עכשיו (מנתח / מפעיל כלי / מנסח) - להצגה בזמן ההמתנה.
    מחזיר {'reply': str, 'tool_calls': [...]} ושומר את ההודעות ב-DB."""
    from llama_index.core.llms import ChatMessage, MessageRole

    llm = llm or FallbackLLM(model_names(), keys=api_key_count())
    tools = build_tools(session_id)
    tools_by_name = {t.metadata.name: t for t in tools}

    messages = start_session(session_id)
    if user_text is None:
        if messages[-1]["role"] != "user":
            raise ValueError("אין הודעת עובד שממתינה לתשובה")
    else:
        messages.append({"role": "user", "content": user_text})
        db.append_messages(session_id, [{"role": "user", "content": user_text}])

    history = _history_messages(messages)
    note = picker_context_note(messages)
    if note:
        history.append(ChatMessage(role=MessageRole.USER, content=note))
    tool_log = []
    reply = None

    def status(text: str) -> None:
        if on_status:
            try:
                on_status(text)
            except Exception:            # תקלה בהצגת סטטוס לא אמורה לעצור את השיחה
                logger.exception("on_status נכשל")

    corrections_left = 1      # ניסיון תיקון אחד אם הסוכן המציא מספרים
    for _ in range(MAX_TOOL_ROUNDS):
        # הפרומפט נבנה מחדש בכל סבב - כך הסוכן רואה את המצב אחרי כל כלי שהופעל
        chat_history = [_system_message(session_id)] + history
        status(STATUS_ANALYZING if not tool_log else STATUS_COMPOSING)
        # כלי אחד בכל סבב: כך הסוכן רואה את תוצאת כל כלי (ואת המצב המעודכן) לפני שהוא מחליט על הצעד הבא,
        # ולא מפעיל למשל פנייה לנציג באותו תור שבו הוא עוד אמור לשאול את העובד
        response = llm.chat_with_tools(tools, chat_history=chat_history, allow_parallel_tool_calls=False)
        tool_calls = llm.get_tool_calls_from_response(response, error_on_no_tool_call=False)
        if not tool_calls:
            candidate = re.sub(r"\s*\[[a-z_]{4,}\]", "", response.message.content or "")   # שם כלי שדלף לטקסט
            candidate = strip_time_claims(candidate)
            invented = unsupported_amounts(candidate, _evidence(session_id, messages, tool_log))
            rep_claim = claims_rep_contact(candidate) and not rep_contact_is_backed(session_id, tool_log)
            if not invented and not rep_claim:
                reply = candidate
                break
            if corrections_left > 0:
                corrections_left -= 1
                history.append(response.message)
                problems = []
                if invented:
                    problems.append(f"התשובה כללה מספרים שלא הגיעו מהעובד או מכלי: {', '.join(invented)}. "
                                    "אסור להמציא מספרים. הפעל את הכלי המתאים (למשל calculate_net_salary), "
                                    "או נסח מחדש בלי המספרים האלה.")
                if rep_claim:
                    problems.append("כתבת שפנית או שלחת שאלה לנציג התחבורה, אבל לא הפעלת את request_representative_info "
                                    "והצלחת, ואין שאלה פתוחה. אם העובד אמר שהוא לא יודע את הנתון - הפעל את הכלי עכשיו. "
                                    "אחרת נסח מחדש בלי לטעון שפנית לנציג.")
                history.append(ChatMessage(role=MessageRole.USER, content="[הודעת מערכת] " + " ".join(problems)))
                continue
            if rep_claim and not invented:
                reply = strip_rep_claims(candidate) or SAFE_REPLY      # לא מבטיחים פנייה שלא קרתה
                break
            reply = SAFE_REPLY
            break
        history.append(response.message)
        for call in tool_calls:
            tool = tools_by_name.get(call.tool_name)
            status(TOOL_STATUS.get(call.tool_name, STATUS_ANALYZING))
            if tool is None:
                output = {"ok": False, "error": f"כלי לא קיים: {call.tool_name}"}
            else:
                try:
                    output = tool.call(**call.tool_kwargs).raw_output
                except Exception as e:  # כלי לא אמור לקרוס, אבל השיחה לא תיפול בגללו
                    logger.exception("הכלי %s נכשל (שיחה %s)", call.tool_name, session_id)
                    output = {"ok": False, "error": f"שגיאה בהפעלת הכלי: {e}"}
            tool_log.append({"tool": call.tool_name, "args": call.tool_kwargs, "output": output})
            if on_step:
                try:
                    on_step(tool_log[-1])
                except Exception:       # תקלה בעדכון התצוגה לא אמורה לעצור את השיחה
                    logger.exception("on_step נכשל")
            history.append(ChatMessage(role=MessageRole.TOOL, content=json.dumps(output, ensure_ascii=False),
                                       additional_kwargs={"tool_call_id": call.tool_id}))
    if reply is None:
        reply = "מצטער, לא הצלחתי להשלים את הבקשה. אפשר לנסח אותה שוב?"

    messages.append({"role": "assistant", "content": reply,
                     "tools": [t["tool"] for t in tool_log]})
    db.append_messages(session_id, [messages[-1]])         # הוספה (ולא דריסה): הודעות שנוספו במקביל נשמרות
    return {"reply": reply, "tool_calls": tool_log}
