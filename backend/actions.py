"""פעולות שהמסך מפעיל ישירות (בלי מודל שפה): החלפת חלופה וחישוב מחדש.

משתמשות באותם כלים של הסוכן, ולכן אותם כללים ואותן בדיקות. ההודעה שנוספת לשיחה נבנית בקוד, מתוצאת המנוע.
"""
import db
from agent_tools import (WAIVER_CHOICE, allowed_catalog, calculate_net_salary, cancel_pending, current_situation_known,
                         request_representative_info, update_employee_data, verify_budget_and_eligibility)
from state import ConversationState

PICKER_BUTTONS_MAX = 5      # עד כמה רכבים - כפתורים; מעל זה - טבלה לבחירה


def alternative_options(session_id: str) -> list[dict]:
    """החלופות שהעובד יכול לבחור במסך: רכבי הקטלוג המותרים לו, ואפשרות ויתור אם העיסוק מאפשר."""
    catalog = allowed_catalog(db.load_state(session_id))
    if catalog is None:
        return []
    options = [{"choice": c["id"], "label": f"{c['name']} ({c['registration_year']}) · {c['drive']} · קבוצה {c['group']}"}
               for c in catalog["cars"]]
    if catalog["waiver_allowed"]:
        options.append({"choice": WAIVER_CHOICE, "label": "ויתור על הרכב (אחזקת רכב)"})
    return options


def picker_state(session_id: str) -> dict | None:
    """מה להציג בבורר הרכבים שמעל תיבת השיחה, או None אם אין מה להציג (הקבוצה עוד לא ידועה).
    visible: הבורר פתוח - כשעוד לא נבחרה חלופה, או כשהעובד/הסוכן ביקשו לבחור רכב אחר.
    as_table: מעל PICKER_BUTTONS_MAX רכבים מציגים טבלה לבחירה במקום כפתורים."""
    state = db.load_state(session_id)
    catalog = allowed_catalog(state)
    if catalog is None or not current_situation_known(state):     # קודם המצב היום, רק אחר כך חלופות
        return None
    return {
        "cars": catalog["cars"],
        "waiver_allowed": catalog["waiver_allowed"],
        "visible": (state.target.kind is None and not state.picker_dismissed) or state.picker_open,
        "closable": state.target.kind is not None,
        "can_reopen": state.target.kind != "waiver",     # בוויתור אין כפתור "בחירת רכב אחר" - הממשק נקי
        "as_table": len(catalog["cars"]) > PICKER_BUTTONS_MAX,
    }


def set_picker(session_id: str, open_: bool) -> None:
    """פתיחה/סגירה של בורר הרכבים (למשל כפתור "בחירת רכב אחר")."""
    state = db.load_state(session_id)
    state.picker_open = open_
    db.save_state(state)


RESET_MESSAGE = "איפסתי את הנתונים הקודמים. נתחיל מחדש - מה תרצה/י לבדוק?"


def reset_data(session_id: str) -> None:
    """איפוס נתוני העובד (העובד אישר): מצב ריק, שאלות פתוחות לנציג מבוטלות. ההודעות נשארות על המסך, אבל הבוט לא רואה
    את מה שלפני הודעת האיפוס."""
    state = db.load_state(session_id)
    for field in list(state.pending_questions):
        cancel_pending(state, field)
    db.save_state(ConversationState(session_id=session_id))          # בלי טעינה קודמת: דורס את המצב כולו
    messages = db.load_messages(session_id)
    messages.append({"role": "assistant", "content": RESET_MESSAGE, "source": "screen", "reset": True})
    db.save_messages(session_id, messages)


def pick_message(choice: str, cars: list[dict]) -> str:
    """ההודעה שנשלחת לשיחה בשם העובד כשהוא בוחר רכב (או ויתור) בבורר."""
    if choice == WAIVER_CHOICE:
        return "אני רוצה לוותר על הרכב"
    car = next(c for c in cars if c["id"] == choice)
    return f"אני רוצה לבדוק את {car['display_name']} (מזהה {car['id']})"


def format_result_message(result: dict) -> str:
    """הודעת השוואה לשיחה: רשימה קצרה (קריאה בעברית עם מספרים) + ההסבר."""
    diff = result["diff"]
    diff_text = (f"עלייה של {diff:,} ₪ בחודש" if diff > 0
                 else f"ירידה של {-diff:,} ₪ בחודש" if diff < 0 else "ללא שינוי")
    text = (f"**השוואה: {result['alternative']['label']}**  \n"
            f"נטו היום: **{result['net_today']:,} ₪**  \n"
            f"נטו בחלופה: **{result['net_alternative']:,} ₪**  \n"
            f"הפרש: **{diff_text}**\n\n"
            f"{result['explanation']}")
    if result["status"] == "provisional":
        text += "\n\n(השוואת ביניים - ההשתתפות ברכב החדש ממתינה לאישור הנציג.)"
    return text


def _append_assistant(session_id: str, text: str) -> None:
    messages = db.load_messages(session_id)
    messages.append({"role": "assistant", "content": text, "source": "screen"})
    db.save_messages(session_id, messages)


def switch_alternative(session_id: str, choice: str, new_participation: float | None = None) -> dict:
    """החלפת החלופה וחישוב מחדש. מחזיר {'ok': bool, 'message': str} ומוסיף את ההודעה לשיחה."""
    verdict = verify_budget_and_eligibility(session_id, choice)
    if not verdict["ok"]:
        missing = ", ".join(verdict["missing"]) or verdict["error"]
        message = f"כדי לבדוק את החלופה חסר: {missing}. אפשר להשלים אותו בשיחה."
        _append_assistant(session_id, message)
        return {"ok": False, "message": message}
    if verdict["data"]["eligible"] is False:
        message = f"החלופה לא זמינה: {verdict['data']['reason']}."
        _append_assistant(session_id, message)
        return {"ok": False, "message": message}

    if new_participation is not None and choice != WAIVER_CHOICE:
        update = update_employee_data(session_id, new_participation=new_participation)
        if not update["ok"]:
            _append_assistant(session_id, update["error"])
            return {"ok": False, "message": update["error"]}

    calc = calculate_net_salary(session_id)
    if not calc["ok"]:
        missing = ", ".join(calc["missing"]) or calc["error"]
        message = f"בחרתי את החלופה, אבל כדי לחשב עדיין חסר: {missing}."
        _append_assistant(session_id, message)
        return {"ok": False, "message": message}
    message = format_result_message(calc["data"])
    _append_assistant(session_id, message)
    return {"ok": True, "message": message}


def recalculate(session_id: str) -> dict:
    """חישוב מחדש על הנתונים השמורים (למשל אחרי תיקון נתון)."""
    calc = calculate_net_salary(session_id)
    if not calc["ok"]:
        message = calc["error"] + (f" ({', '.join(calc['missing'])})" if calc["missing"] else "")
        return {"ok": False, "message": message}
    message = format_result_message(calc["data"])
    _append_assistant(session_id, message)
    return {"ok": True, "message": message}
