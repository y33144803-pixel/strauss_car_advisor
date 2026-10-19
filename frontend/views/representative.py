"""מסך נציג: שאלות פתוחות מהבוט, והזנת התשובה. השיחה של העובד ממשיכה אוטומטית."""
from datetime import datetime

import streamlit as st

import db
from agent_tools import REP_FIELDS
from ui import require_role

rep_user = require_role("rep")          # רק נציגים (התחברות עם שם משתמש וסיסמה)
st.subheader("מסך נציג תחבורה")


@st.fragment(run_every=5)
def open_questions() -> None:
    questions = db.list_questions("open")
    st.subheader(f"שאלות פתוחות ({len(questions)})")
    if not questions:
        st.caption("אין שאלות פתוחות כרגע. המסך מתרענן אוטומטית.")
    for q in questions:
        title = f"#{q['id']} · {REP_FIELDS.get(q['missing_field'], q['missing_field'])} · שיחה {q['session_id']}"
        with st.expander(title, expanded=True):
            st.markdown(f"**פנייה:** {q['context_message']}")
            if q["employee_summary"]:
                st.caption(q["employee_summary"])
            st.caption(f"נשלח: {datetime.fromisoformat(q['created_at']).strftime('%d/%m/%Y %H:%M')}")
            free_text = q["missing_field"] == "other"      # תשובה חופשית - אין סכום חובה
            with st.form(f"answer_{q['id']}"):
                # value=None: שדה ריק. בלי זה ברירת המחדל 0 הייתה נשמרת בטעות כהשתתפות של 0 ₪
                value = st.number_input("הסכום (₪)" + ("" if free_text else " - חובה"), min_value=0.0, max_value=50000.0, step=50.0,
                                        value=None, placeholder="הקלד/י סכום", key=f"val_{q['id']}")
                # תאריך אופציונלי: שדה ריק = ללא תאריך. (תיבת סימון בתוך טופס לא מעדכנת את השדה בזמן אמת, ולכן הוסרה)
                effective = st.date_input("בתוקף מתאריך (אופציונלי)", value=None, key=f"date_{q['id']}",
                                          format="DD/MM/YYYY")
                note = st.text_input("הערה (אופציונלי)", key=f"note_{q['id']}")
                if st.form_submit_button("שלח תשובה", type="primary"):
                    if value is None and not (free_text and note):
                        st.error("יש להזין סכום לפני השליחה (אפשר גם 0, אם זה הסכום הנכון)."
                                 if not free_text else "יש להזין סכום או הערה.")
                    else:
                        db.answer_question(q["id"], value, effective.strftime("%d/%m/%Y") if effective else None,
                                           note or None, answered_by=rep_user["username"])      # נשמר מי ענה
                        st.rerun(scope="app")


open_questions()

with st.expander("שאלות שנענו"):
    answered = [q for q in db.list_questions(None) if q["status"] in ("answered", "applied")]
    if not answered:
        st.caption("עדיין אין.")
    for q in answered:
        when = f", בתוקף מ-{q['effective_from']}" if q["effective_from"] else ""
        state = "נקלט בשיחה" if q["status"] == "applied" else "ממתין לקליטה בשיחה"
        answer = f"{q['answer_value']:,.0f} ₪" if q["answer_value"] is not None else (q["answer_note"] or "-")
        st.markdown(f"- #{q['id']} · {REP_FIELDS.get(q['missing_field'], q['missing_field'])} · "
                    f"{answer}{when} · שיחה {q['session_id']} · {state}"
                    + (f" · ענה/תה: {q['answered_by']}" if q.get("answered_by") else ""))
