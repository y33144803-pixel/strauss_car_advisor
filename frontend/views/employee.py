"""מסך עובד: שיחה עם הבוט (עם בורר רכבים מעל תיבת הקלט). הנתונים שנקלטו וההשוואה - בצד, לפי בקשה.
בסרגל הצד: שיחה חדשה והיסטוריית השיחות (שינוי שם ומחיקה)."""
import logging
import time

import streamlit as st

import db
import turns
from actions import pick_message, picker_state, recalculate, set_picker
from agent import STATUS_ANALYZING, chat, check_representative_updates, friendly_error, pending_user_message, start_session
from agent_tools import REP_FIELDS
from data_store import find_occupation
from payslip import (ALLOWED_TYPES, FIELD_LABELS, OCCUPATION_LABEL, PayslipError, apply_payslip, check_file,
                     extract_payslip, summary_text)
from ui import (CLOSE_PICKER, avatar_for, get_session_id, render_car_picker, render_comparison,
                render_history_sidebar, render_state_card, require_consent, require_role, show_markdown, typing_status_html)

require_role("employee")                # רק עובדים (או התחברות כבויה בפיתוח מקומי)
logger = logging.getLogger("strauss.employee")
sid = get_session_id()
CHAT_HEIGHT = 480
MAX_AUTO_RESUMES = 3        # המשך אוטומטי של תור שנקטע, לכל הודעה
MAX_MESSAGE_CHARS = 2000      # תקרת אורך להודעת עובד (עלות מודל ונפח מסד)
CHAT_HEIGHT_WITH_PICKER = 250

st.session_state.setdefault("picker_nonce", 0)

st.session_state.setdefault("show_comparison", False)
st.session_state.setdefault("show_data", False)
render_history_sidebar(sid, show_data_toggle=True)    # שיחה חדשה + מתג הנתונים + היסטוריית שיחות בסרגל הצד
# האישור אחרי הסרגל: בזמן שמוצג כרטיס האישור הסרגל (שיחה חדשה, היסטוריה) נשאר במקומו ולא נעלם
require_consent(sid)                    # אישור שמירת השיחות: בכל שיחה חדשה, אלא אם סימנו "אל תזכירו שוב"

# בצד (עמודה שמאלית), לפי בקשת העובד: "הנתונים שנקלטו" ו/או ההשוואה המפורטת. אחרת כל המסך הוא שיחה.
# שני המתגים בסרגל הצד, מתחת לכפתור "שיחה חדשה"
initial_state = db.load_state(sid)
show_comparison = bool(initial_state.last_result and st.session_state["show_comparison"])
show_data = bool(st.session_state["show_data"])
side_open = show_comparison or show_data
data_slot = None                         # נוצר בעמודת הצד; מצויר מחדש אחרי כל כלי כך שהוא מתמלא מול עיני העובד


def draw_state_card() -> None:
    if data_slot is not None:
        with data_slot.container():
            render_state_card(db.load_state(sid))


def watch_representative() -> None:
    """בודק מדי כמה שניות אם הנציג ענה; אם כן - השיחה ממשיכה מאותה נקודה.
    הבדיקה המחזורית פעילה רק כשיש שאלה פתוחה לנציג: ריצה מחזורית בזמן תור של הבוט יכולה להפריע לציור המסך
    (השיחה "נעלמת" עד רענון), ולכן בשיחה רגילה, בלי שאלה פתוחה, אין ריצה כזו."""
    def check() -> None:
        if check_representative_updates(sid):
            st.rerun()
    st.fragment(run_every=5 if initial_state.waiting_for_representative else None)(check)()


def follow_turn(turn) -> None:
    """מציג את התור שרץ ברקע (סטטוס חי) עד שיסתיים, ואז טוען את המסך מחדש מהמצב השמור - כך הפאנלים, הבורר וההשוואה
    תמיד מתואמים. אם המסך נקטע (לחיצה על מתג) והורץ מחדש - הריצה החדשה מתחברת לאותו תור ממשיכה לעקוב."""
    with st.chat_message("assistant", avatar=avatar_for("assistant")):
        # סטטוס אמיתי: הטקסט מתחלף לפי מה שהסוכן עושה באותו רגע (בוחן / מחפש רכב / מחשב / מנסח), ונכתב אות אחרי אות
        status_slot = st.empty()
        shown, steps = None, turn.steps
        while not turn.done:
            label = turn.status or STATUS_ANALYZING
            if label != shown:
                shown = label
                status_slot.markdown(typing_status_html(label), unsafe_allow_html=True)
            if turn.steps != steps:
                steps = turn.steps
                draw_state_card()
            time.sleep(0.2)
    finished = turns.finish(sid)
    if finished is not None and finished.error is not None:   # תקלה בשירות המודל לא אמורה להפיל את המסך
        logger.error("תור השיחה נכשל (שיחה %s)", sid, exc_info=finished.error)
        st.session_state["chat_error"] = friendly_error(finished.error)
    else:
        st.session_state.pop("chat_error", None)
    st.rerun()


def run_turn(text: str | None) -> None:
    """תור בשיחה (text=None: ניסיון חוזר להודעה שכבר נשמרה). רץ ברקע (turns.py) והמסך עוקב אחריו."""
    turn = turns.start(sid, lambda t: chat(sid, text, on_step=t.on_step, on_status=t.on_status))
    follow_turn(turn)


def confirm_payslip() -> None:
    """העובד אישר (או תיקן) את הערכים שנקראו מהתלוש: שומרים אותם ושולחים לבוט הודעה שממשיכה את השיחה."""
    proposal = st.session_state.pop("payslip_proposal", None)
    if not proposal or proposal["sid"] != sid:
        return
    values = {key: float(st.session_state[f"payslip_{proposal['nonce']}_{key}"])
              for key in proposal["fields"] if key != "occupation"}
    # התפקיד הוא תיבת טקסט חופשית. אם הוא תואם עיסוק בטבלה - נשמר; אחרת הבוט מקבל אותו בהודעה ומטפל בו בשיחה
    typed = (st.session_state.get(f"payslip_{proposal['nonce']}_occupation") or "").strip()
    unmatched_job = None
    if typed:
        record = find_occupation(typed)
        if record:
            values["occupation"] = record["occupation"]
        else:
            unmatched_job = typed
    outcome = apply_payslip(sid, values)
    if not outcome["ok"]:
        proposal["error"] = outcome["error"]               # הכרטיס נשאר עם מה שהעובד הקליד, והשגיאה מוצגת בו
        st.session_state["payslip_proposal"] = proposal
        return
    st.session_state["payslip_text"] = ("העליתי תלוש שכר ואישרתי את הנתונים שנקראו ממנו: " + summary_text(values) + "."
                                        + (f" התפקיד שלי: {unmatched_job}." if unmatched_job else ""))


def cancel_payslip() -> None:
    st.session_state.pop("payslip_proposal", None)


def read_uploaded_payslip(uploaded, show_status) -> None:
    """קורא קובץ שהועלה בשורת הצ'אט. נתונים ברורים ועקביים נשמרים מיד (העובד רואה מה נקלט בשיחה ויכול לתקן);
    אם משהו לא ברור מוצג כרטיס אישור. הקובץ לא נשמר בשום מקום."""
    try:
        mime = check_file(uploaded.name, uploaded.size)
        show_status("קורא את התלוש...")
        result = extract_payslip(uploaded.getvalue(), mime)
    except PayslipError as e:
        st.session_state["payslip_notice"] = str(e)
        return
    st.session_state.pop("payslip_notice", None)
    if not result.fields:
        st.session_state["payslip_notice"] = "לא מצאתי בקובץ נתוני שכר. אפשר לנסות תלוש אחר או להקליד את הנתונים בשיחה."
        return
    if result.certain:
        outcome = apply_payslip(sid, result.fields)
        if outcome["ok"]:
            note = (f" בתלוש מופיע התפקיד \"{result.job_title}\", שלא זוהה בבירור באחד העיסוקים בטבלה - נא לשאול אותי מה העיסוק."
                    if result.job_title else
                    f" (התפקיד שכתוב בתלוש: \"{result.occupation_from_title}\")" if result.occupation_from_title else "")
            st.session_state["payslip_text"] = ("העליתי תלוש שכר. הנתונים שנקראו ממנו: "
                                                + summary_text(result.fields) + "." + note)
            return
        result.reasons.append(outcome["error"])
    nonce = st.session_state.get("payslip_nonce", 0) + 1
    st.session_state["payslip_nonce"] = nonce
    st.session_state["payslip_proposal"] = {"sid": sid, "fields": result.fields, "nonce": nonce,
                                            "reasons": result.reasons, "job_title": result.job_title}


def render_payslip_card() -> None:
    notice = st.session_state.get("payslip_notice")
    if notice:
        st.warning(notice)
    proposal = st.session_state.get("payslip_proposal")
    if not proposal or proposal["sid"] != sid:
        return
    with st.container(key="payslip-card", border=True):
        st.markdown("**קראתי מהתלוש - נא לאשר או לתקן**")
        if proposal.get("error"):
            st.error(f"לא הצלחתי לשמור: {proposal['error']}")
        why = "; ".join(proposal.get("reasons") or [])
        if proposal.get("job_title"):
            why = (why + "; " if why else "") + f"התפקיד בתלוש (\"{proposal['job_title']}\") לא זוהה בבירור - אפשר לתקן אותו בשדה התפקיד"
        st.caption((f"{why}. " if why else "") + "רק השדות שנמצאו בתלוש; מה שלא מופיע כאן - אשאל עליו בשיחה. "
                   "הקובץ נשלח לקריאה אוטומטית ולא נשמר.")
        cols = st.columns(2)
        st.text_input(OCCUPATION_LABEL, value=proposal["fields"].get("occupation") or proposal.get("job_title") or "",
                      placeholder="למשל: מפתח/ת תוכנה (אפשר להשאיר ריק)", key=f"payslip_{proposal['nonce']}_occupation")
        for i, (key, value) in enumerate((k, v) for k, v in proposal["fields"].items() if k != "occupation"):
            cols[i % 2].number_input(FIELD_LABELS[key], value=value, min_value=0.0, step=1.0 if "(₪)" in FIELD_LABELS[key] else 0.25,
                                     format="%g", key=f"payslip_{proposal['nonce']}_{key}")
        ok_col, cancel_col, _ = st.columns([1, 1, 2])
        ok_col.button("אשר והמשך", type="primary", on_click=confirm_payslip, key="payslip_ok")
        cancel_col.button("ביטול", on_click=cancel_payslip, key="payslip_cancel")


watch_representative()

if side_open:
    chat_col, result_col = st.columns([3, 2], gap="medium")
    if show_data:
        with result_col:
            if show_comparison:
                # כשההשוואה מוצגת, הנתונים נאספים לתיבה נפתחת "הצג נתונים" (סגורה כברירת מחדל);
                # כשההשוואה נעלמת הם חוזרים להיראות כרגיל
                with st.expander("הצג נתונים", expanded=False):
                    data_slot = st.empty()
            else:
                data_slot = st.empty()
        draw_state_card()
else:
    chat_col, result_col = st.container(), None
    # בלי עמודת צד: תיבת הכתיבה ברוחב כל השיחה
    st.markdown("<style>[data-testid='stBottomBlockContainer'] [data-testid='stChatInput'] { width: 100% !important; }"
                "</style>", unsafe_allow_html=True)
picker = picker_state(sid)
picker_open = bool(picker and picker["visible"])

with chat_col:
    # ההודעות מוצגות בקונטיינר שנוצר כאן ונמלא בהמשך - אחרי שהתור הנוכחי טופל.
    # כשהבורר פתוח החלון נמוך יותר, כדי שתיבת הקלט תישאר גלויה בלי לגלול
    # (הגובה בפועל נקבע ב-CSS לפי גובה המסך; המפתח קובע איזה כלל חל)
    chat_box = st.container(height=CHAT_HEIGHT_WITH_PICKER if picker_open else CHAT_HEIGHT, border=False,
                            key="chatbox-picker" if picker_open else "chatbox-btn" if picker and picker["can_reopen"] else "chatbox")

    # בורר הרכבים מעל תיבת הקלט: מופיע כשקבוצת הזכאות ידועה ועוד לא נבחרה חלופה,
    # או כשהעובד (כפתור "בחירת רכב אחר") / הסוכן (הכלי show_catalog) ביקשו לבחור רכב
    picked_text = None
    if picker_open:
        picker_slot = st.empty()                  # בלחיצה על רכב הבורר נעלם מיד, לא רק אחרי שהבוט עונה
        with picker_slot.container():
            choice = render_car_picker(picker, st.session_state["picker_nonce"])
        if choice:
            st.session_state["picker_nonce"] += 1
            picker_slot.empty()
            if choice == CLOSE_PICKER:
                set_picker(sid, False)
                st.rerun()
            picked_text = pick_message(choice, picker["cars"])      # הבחירה נשלחת לשיחה בשם העובד
    elif picker and picker["can_reopen"]:
        if st.button("בחירת רכב אחר", key="open_picker"):
            set_picker(sid, True)
            st.rerun()


# תיבת הקלט מחוץ לעמודות: Streamlit מצמיד אותה תמיד לתחתית המסך. חלון השיחה מתאים את גובהו למסך (CSS),
# ולכן הדף לא גולל בכל תור
submitted = st.chat_input("כתוב/י כאן... (+ לצירוף תלוש שכר)", accept_file=True, file_type=sorted(ALLOWED_TYPES),
                          max_chars=MAX_MESSAGE_CHARS)
prompt = submitted.text if submitted else None
upload = submitted.files[0] if submitted and submitted.files else None
live_turn = turns.get(sid)                                       # תור שרץ ברקע (או הסתיים ועוד לא טופל)
text = (prompt or picked_text or st.session_state.pop("payslip_text", None)         # (הטקסט האחרון: אישור כרטיס התלוש)
        or (None if live_turn else st.session_state.pop("queued_text", None)))
messages = start_session(sid)
with chat_box:
    for m in (m for m in messages if not m.get("hidden")):          # הודעות מערכת נסתרות לא מוצגות
        with st.chat_message(m["role"], avatar=avatar_for(m["role"])):
            show_markdown(m["content"])
    if live_turn:
        if text:                                # נשלחה הודעה בזמן שהבוט עוד חושב: ממתינה לסיום התור
            st.session_state["queued_text"] = " ".join(t for t in (st.session_state.get("queued_text"), text) if t)
        follow_turn(live_turn)
    if upload:
        # קריאת התלוש מוצגת כמו כל פעולה של הבוט: בועה עם סטטוס חי ("קורא את התלוש...")
        with st.chat_message("assistant", avatar=avatar_for("assistant")):
            payslip_status = st.empty()
            read_uploaded_payslip(upload, lambda label: payslip_status.markdown(typing_status_html(label),
                                                                                unsafe_allow_html=True))
            payslip_status.empty()
        read_text = st.session_state.pop("payslip_text", None)
        text = " ".join(t for t in (prompt, read_text) if t) or text
        if not text:
            st.rerun()                          # נקרא ונדרש אישור (או שהייתה שגיאה): מציירים מחדש את המסך
    if text:
        st.session_state.pop("payslip_notice", None)
        with st.chat_message("user", avatar=avatar_for("user")):
            show_markdown(text)                 # ההודעה מוצגת מיד, בלי לחכות לתשובה
        run_turn(text)
    elif pending_user_message(sid):
        unanswered = pending_user_message(sid)
        resumes = st.session_state.setdefault("auto_resumes", {})
        if not st.session_state.get("chat_error") and resumes.get((sid, unanswered), 0) < MAX_AUTO_RESUMES:
            # אין שגיאה: התור נקטע באמצע (למשל לחיצה על מתג או כפתור בזמן שהבוט חשב) - ממשיכים אוטומטית, עד
            # MAX_AUTO_RESUMES פעמים לכל הודעה (לחיצות חוזרות קוטעות שוב). אם זה לא מצליח - מוצג "נסיון חוזר"
            resumes[(sid, unanswered)] = resumes.get((sid, unanswered), 0) + 1
            run_turn(None)
        retry_slot = st.empty()                 # בלחיצה ההודעה והכפתור נעלמים מיד (לא רק אחרי שהבוט עונה)
        with retry_slot.container():
            st.warning(st.session_state.get("chat_error") or "ההודעה האחרונה שלך עדיין לא נענתה.")
            retry = st.button("נסיון חוזר", type="primary")
        if retry:
            retry_slot.empty()
            st.session_state.pop("chat_error", None)
            run_turn(None)

state = db.load_state(sid)
with chat_box:
    render_payslip_card()
    if state.waiting_for_representative:
        waiting = ", ".join(REP_FIELDS.get(f, f) for f in state.pending_questions)
        st.info(f"שלחתי שאלה לנציג התחבורה ואני ממתין לתשובה: {waiting}. הדף מתעדכן אוטומטית.")

if show_comparison:
    with result_col:
        st.subheader("השוואה")
        render_comparison(state.last_result, stale=state.result_is_stale)
        if state.result_is_stale and st.button("חשב מחדש", type="primary"):
            outcome = recalculate(sid)
            if not outcome["ok"]:
                st.warning(outcome["message"])
            st.rerun()
