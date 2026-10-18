"""מסך עובד: שיחה עם הבוט (עם בורר רכבים מעל תיבת הקלט). הנתונים שנקלטו וההשוואה - בצד, לפי בקשה.
בסרגל הצד: שיחה חדשה והיסטוריית השיחות (שינוי שם ומחיקה)."""
import logging

import streamlit as st

import db
from actions import pick_message, picker_state, recalculate, set_picker
from agent import STATUS_ANALYZING, chat, check_representative_updates, friendly_error, pending_user_message, start_session
from agent_tools import REP_FIELDS
from ui import (CLOSE_PICKER, avatar_for, get_session_id, render_car_picker, render_comparison,
                render_history_sidebar, render_state_card, require_role, show_markdown, typing_status_html)

require_role("employee")                # רק עובדים (או התחברות כבויה בפיתוח מקומי)
logger = logging.getLogger("strauss.employee")
sid = get_session_id()
CHAT_HEIGHT = 480
CHAT_HEIGHT_WITH_PICKER = 250

st.session_state.setdefault("picker_nonce", 0)

render_history_sidebar(sid)             # שיחה חדשה + היסטוריית שיחות בסרגל הצד

# בצד (עמודה שמאלית), לפי בקשת העובד: "הנתונים שנקלטו" ו/או ההשוואה המפורטת. אחרת כל המסך הוא שיחה.
# המתגים עצמם מופיעים בשיחה (המתג של ההשוואה - מתחת להודעת ההשוואה האחרונה)
initial_state = db.load_state(sid)
st.session_state.setdefault("show_comparison", False)
st.session_state.setdefault("show_data", False)
show_comparison = bool(initial_state.last_result and st.session_state["show_comparison"])
show_data = bool(st.session_state["show_data"])
side_open = show_comparison or show_data
data_slot = None                         # נוצר בעמודת הצד; מצויר מחדש אחרי כל כלי כך שהוא מתמלא מול עיני העובד


def draw_state_card() -> None:
    if data_slot is not None:
        with data_slot.container():
            render_state_card(db.load_state(sid))


@st.fragment(run_every=5)
def watch_representative() -> None:
    """בודק מדי כמה שניות אם הנציג ענה; אם כן - השיחה ממשיכה מאותה נקודה."""
    if check_representative_updates(sid):
        st.rerun()


def run_turn(text: str | None) -> None:
    """תור בשיחה (text=None: ניסיון חוזר להודעה שכבר נשמרה). מוצג כבועה עם סטטוס חי, ובסיומו המסך נטען מחדש
    מהמצב השמור - כך הפאנלים, הבורר וההשוואה תמיד מתואמים."""
    with st.chat_message("assistant", avatar=avatar_for("assistant")):
        # סטטוס אמיתי: הטקסט מתחלף לפי מה שהסוכן עושה באותו רגע (בוחן / מחפש רכב / מחשב / מנסח), ונכתב אות אחרי אות
        status_slot = st.empty()

        def show_status(label: str) -> None:
            status_slot.markdown(typing_status_html(label), unsafe_allow_html=True)

        show_status(STATUS_ANALYZING)
        try:
            chat(sid, text, on_step=lambda step: draw_state_card(), on_status=show_status)
        except Exception as e:  # תקלה בשירות המודל לא אמורה להפיל את המסך
            logger.exception("תור השיחה נכשל (שיחה %s)", sid)
            st.session_state["chat_error"] = friendly_error(e)
            st.rerun()
    st.session_state.pop("chat_error", None)
    st.rerun()


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
        choice = render_car_picker(picker, st.session_state["picker_nonce"])
        if choice:
            st.session_state["picker_nonce"] += 1
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
prompt = st.chat_input("כתוב/י כאן...")

text = prompt or picked_text
messages = start_session(sid)
with chat_box:
    st.caption("🔒 השיחות נשמרות לצורך מעקב ושיפור השירות. בהמשך השיחה את/ה מאשר/ת את שמירתן.")
    last_comparison = max((i for i, m in enumerate(messages)
                           if m["role"] == "assistant"
                           and ("נטו היום" in m["content"] or m["content"].startswith("**השוואה"))),
                          default=len(messages) - 1)       # אין הודעת השוואה בשיחה: המתג בסוף השיחה
    for i, m in enumerate(messages):
        with st.chat_message(m["role"], avatar=avatar_for(m["role"])):
            show_markdown(m["content"])
        if i == last_comparison and initial_state.last_result:
            # מתג "הצג השוואה מפורטת בצד" - מתחת להודעת ההשוואה האחרונה בשיחה
            st.toggle("הצג השוואה מפורטת בצד", key="show_comparison")
    if text:
        with st.chat_message("user", avatar=avatar_for("user")):
            show_markdown(text)                 # ההודעה מוצגת מיד, בלי לחכות לתשובה
        run_turn(text)
    elif pending_user_message(sid):
        st.warning(st.session_state.get("chat_error") or "ההודעה האחרונה שלך עדיין לא נענתה.")
        if st.button("נסי שוב", type="primary"):
            run_turn(None)
    st.toggle("הצג את הנתונים שנקלטו בצד", key="show_data")

state = db.load_state(sid)
with chat_box:
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
