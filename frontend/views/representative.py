"""מסך נציג: שאלות פתוחות מהבוט, והזנת התשובה. השיחה של העובד ממשיכה אוטומטית."""
import html
from datetime import datetime

import streamlit as st

import db
from agent_tools import REP_FIELDS
from data_store import get_rules
from ui import badge, rep_hero, require_role

rep_user = require_role("rep")          # רק נציגים (התחברות עם שם משתמש וסיסמה)


NO_VALUE = "אין ערך למסירה (למשל: הרכב או סוג העבודה לא קיימים, או שלא ניתן לקבוע) - חובה לכתוב הערה"


def answer_form(q: dict) -> None:
    """טופס התשובה לשאלה אחת. בכל שאלה אפשר לסמן "אין ערך למסירה" - ואז חובה הערה, שהבוט משתמש בה כדי להסביר לעובד."""
    free_text = q["missing_field"] == "other"      # תשובה חופשית - אין סכום חובה
    is_group = q["missing_field"] == "eligibility_group"
    with st.form(f"answer_{q['id']}"):
        no_value = False if free_text else st.checkbox(NO_VALUE, key=f"noamt_{q['id']}")
        group = value = effective = None
        if is_group:                                # הנציג בוחר קבוצת זכאות (אין סכום)
            group = st.selectbox("קבוצת הזכאות של העובד", get_rules()["groupOrder"], key=f"group_{q['id']}")
        else:
            # value=None: שדה ריק. בלי זה ברירת המחדל 0 הייתה נשמרת בטעות כהשתתפות של 0 ₪
            value = st.number_input("הסכום (₪)" + ("" if free_text or no_value else " - חובה"), min_value=0.0,
                                    max_value=50000.0, step=50.0, value=None, placeholder="הקלד/י סכום",
                                    key=f"val_{q['id']}")
            # תאריך אופציונלי: שדה ריק = ללא תאריך. (תיבת סימון בתוך טופס לא מעדכנת את השדה בזמן אמת, ולכן הוסרה)
            effective = st.date_input("בתוקף מתאריך (אופציונלי)", value=None, key=f"date_{q['id']}", format="DD/MM/YYYY")
        note = "" if is_group else st.text_input("הערה לבוט (לא תוצג לעובד; הבוט יחליט מה לומר לו)", key=f"note_{q['id']}")
        if is_group:
            note = st.text_input("הערה לבוט - נדרשת רק אם אין ערך למסירה", key=f"note_{q['id']}")
        if not st.form_submit_button("שלח תשובה", type="primary"):
            return
        if (free_text or no_value) and not note:
            st.error("יש לכתוב הערה: הבוט משתמש בה כדי להסביר לעובד.")
        elif is_group:
            # קבוצה: האות נשמרת בהערה. "אין ערך" שומר רק את ההערה, והבוט ממשיך איתה בלי לקבוע קבוצה
            db.answer_question(q["id"], None, None, note if no_value else group, answered_by=rep_user["username"])
            st.rerun(scope="app")
        elif value is None and not (free_text or no_value):
            st.error("יש להזין סכום לפני השליחה (אפשר גם 0, אם זה הסכום הנכון), או לסמן שאין ערך למסירה.")
        else:
            if no_value:
                value = None                                   # סימון "אין ערך": גם אם הוקלד מספר - לא נשמר
            db.answer_question(q["id"], value, effective.strftime("%d/%m/%Y") if effective else None,
                               note or None, answered_by=rep_user["username"])      # נשמר מי ענה
            st.rerun(scope="app")


def open_card(q: dict) -> None:
    field = REP_FIELDS.get(q["missing_field"], q["missing_field"])
    sent = datetime.fromisoformat(q["created_at"]).strftime("%d/%m/%Y %H:%M")
    with st.expander(f"#{q['id']} · {field} · נשלח {sent}", expanded=False, key=f"card-q-{q['id']}"):
        st.markdown(
            f"<div class='q-head'>{badge('#' + str(q['id']), 'purple')}<span class='q-title'>{html.escape(field)}</span>"
            f"<span class='q-session'>שיחה {html.escape(q['session_id'])}</span></div>"
            f"<div class='quote'><b>פנייה:</b> {html.escape(q['context_message'])}</div>"
            + (f"<div class='q-chips'>{html.escape(q['employee_summary'])}</div>" if q["employee_summary"] else ""),
            unsafe_allow_html=True)
        answer_form(q)


def answered_card(q: dict) -> None:
    when = f"בתוקף מ-{q['effective_from']}" if q["effective_from"] else ""
    applied = q["status"] == "applied"
    answer = f"{q['answer_value']:,.0f} ₪" if q["answer_value"] is not None else (q["answer_note"] or "-")
    by = f"ענה/תה: {q['answered_by']}" if q.get("answered_by") else ""
    meta = " · ".join(x for x in (when, f"שיחה {q['session_id']}", by) if x)
    field = REP_FIELDS.get(q["missing_field"], q["missing_field"])
    with st.expander(f"#{q['id']} · {field}: {answer}", expanded=False, key=f"card-a-{q['id']}"):
        st.markdown(
            f"<div class='ans-row'>{badge('#' + str(q['id']), 'gray')}"
            f"<span class='ans-main'>{html.escape(field)}: {html.escape(answer)}</span>"
            f"{badge('נקלט בשיחה' if applied else 'ממתין לקליטה', 'green' if applied else 'amber')}"
            f"<span class='ans-meta'>{html.escape(meta)}</span></div>"
            f"<div class='quote'><b>הפנייה:</b> {html.escape(q['context_message'])}</div>",
            unsafe_allow_html=True)


@st.fragment(run_every=5)
def questions_view() -> None:
    open_qs = db.list_questions("open")
    answered = [q for q in db.list_questions(None) if q["status"] in ("answered", "applied")]
    view = rep_hero(len(open_qs), len(answered))      # הריבועים למעלה: מונים ומתג בין פתוחות לשנענו
    if view == "open":
        if not open_qs:
            st.markdown("<div class='empty-card'><div class='big'>✅</div>אין שאלות פתוחות כרגע. המסך מתרענן אוטומטית.</div>",
                        unsafe_allow_html=True)
        for q in open_qs:
            open_card(q)
    else:
        if not answered:
            st.caption("עדיין אין.")
        for q in answered:
            answered_card(q)


questions_view()
