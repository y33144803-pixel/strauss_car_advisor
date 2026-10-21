"""מסך נציג: שאלות פתוחות מהבוט, והזנת התשובה. השיחה של העובד ממשיכה אוטומטית."""
import html
from datetime import datetime

import streamlit as st

import db
from agent_tools import REP_FIELDS
from data_store import get_rules
from ui import badge, page_header, require_role

rep_user = require_role("rep")          # רק נציגים (התחברות עם שם משתמש וסיסמה)
page_header("מסך נציג תחבורה", "שאלות שהבוט לא יכול להשלים לבד. התשובה שלך חוזרת לשיחה של העובד מיד.", "🧑‍💼")


@st.fragment(run_every=5)
def open_questions() -> None:
    questions = db.list_questions("open")
    st.subheader(f"שאלות פתוחות ({len(questions)})")
    if not questions:
        st.markdown("<div class='empty-card'><div class='big'>✅</div>אין שאלות פתוחות כרגע. המסך מתרענן אוטומטית.</div>",
                    unsafe_allow_html=True)
    for q in questions:
        field = REP_FIELDS.get(q["missing_field"], q["missing_field"])
        sent = datetime.fromisoformat(q["created_at"]).strftime("%d/%m/%Y %H:%M")
        with st.container(key=f"card-q-{q['id']}"):
            st.markdown(
                f"<div class='q-head'>{badge('#' + str(q['id']), 'purple')}<span class='q-title'>{html.escape(field)}</span>"
                f"<span class='q-session'>שיחה {html.escape(q['session_id'])} · נשלח {sent}</span></div>"
                f"<div class='quote'><b>פנייה:</b> {html.escape(q['context_message'])}</div>"
                + (f"<div class='q-chips'>{html.escape(q['employee_summary'])}</div>" if q["employee_summary"] else ""),
                unsafe_allow_html=True)
            free_text = q["missing_field"] == "other"      # תשובה חופשית - אין סכום חובה
            if q["missing_field"] == "eligibility_group":
                with st.form(f"answer_{q['id']}"):         # הנציג בוחר קבוצת זכאות (אין סכום)
                    group = st.selectbox("קבוצת הזכאות של העובד", get_rules()["groupOrder"], key=f"group_{q['id']}")
                    if st.form_submit_button("שלח תשובה", type="primary"):
                        db.answer_question(q["id"], None, None, group, answered_by=rep_user["username"])
                        st.rerun(scope="app")
                continue
            with st.form(f"answer_{q['id']}"):
                # value=None: שדה ריק. בלי זה ברירת המחדל 0 הייתה נשמרת בטעות כהשתתפות של 0 ₪
                no_amount = False if free_text else st.checkbox(
                    "אין סכום למסור (למשל: הרכב לא קיים או לא ניתן לקבוע) - חובה לכתוב הערה", key=f"noamt_{q['id']}")
                value = st.number_input("הסכום (₪)" + ("" if free_text or no_amount else " - חובה"), min_value=0.0,
                                        max_value=50000.0, step=50.0, value=None, placeholder="הקלד/י סכום",
                                        key=f"val_{q['id']}")
                # תאריך אופציונלי: שדה ריק = ללא תאריך. (תיבת סימון בתוך טופס לא מעדכנת את השדה בזמן אמת, ולכן הוסרה)
                effective = st.date_input("בתוקף מתאריך (אופציונלי)", value=None, key=f"date_{q['id']}",
                                          format="DD/MM/YYYY")
                note = st.text_input("הערה לבוט (לא תוצג לעובד; הבוט יחליט מה לומר לו)", key=f"note_{q['id']}")
                if st.form_submit_button("שלח תשובה", type="primary"):
                    if (free_text or no_amount) and not note:
                        st.error("יש לכתוב הערה: הבוט משתמש בה כדי להסביר לעובד.")
                    elif value is None and not (free_text or no_amount):
                        st.error("יש להזין סכום לפני השליחה (אפשר גם 0, אם זה הסכום הנכון), או לסמן שאין סכום למסור.")
                    else:
                        if no_amount:
                            value = None                                   # סימון "אין סכום": גם אם הוקלד מספר - לא נשמר
                        db.answer_question(q["id"], value, effective.strftime("%d/%m/%Y") if effective else None,
                                           note or None, answered_by=rep_user["username"])      # נשמר מי ענה
                        st.rerun(scope="app")


open_questions()

with st.expander("שאלות שנענו"):
    answered = [q for q in db.list_questions(None) if q["status"] in ("answered", "applied")]
    if not answered:
        st.caption("עדיין אין.")
    for q in answered:
        when = f"בתוקף מ-{q['effective_from']}" if q["effective_from"] else ""
        applied = q["status"] == "applied"
        answer = f"{q['answer_value']:,.0f} ₪" if q["answer_value"] is not None else (q["answer_note"] or "-")
        by = f"ענה/תה: {q['answered_by']}" if q.get("answered_by") else ""
        meta = " · ".join(x for x in (when, f"שיחה {q['session_id']}", by) if x)
        st.markdown(
            f"<div class='ans-row'>{badge('#' + str(q['id']), 'gray')}"
            f"<span class='ans-main'>{html.escape(REP_FIELDS.get(q['missing_field'], q['missing_field']))}: {html.escape(answer)}</span>"
            f"{badge('נקלט בשיחה' if applied else 'ממתין לקליטה', 'green' if applied else 'amber')}"
            f"<span class='ans-meta'>{html.escape(meta)}</span></div>",
            unsafe_allow_html=True)
