"""האזור האישי: החלפת שם משתמש וסיסמה ויציאה. היסטוריית השיחות נמצאת בסרגל הצד."""
import streamlit as st

import auth
import db
from ui import logout, render_history_sidebar, require_role

user = require_role("employee", "rep")
st.subheader(f"שלום {user['display_name']}")
st.caption(f"שם משתמש: {user['username']} · תפקיד: {'עובד' if user['role'] == 'employee' else 'נציג תחבורה'}")

if user["role"] == "employee":
    render_history_sidebar(st.session_state.get("sid"))     # היסטוריית השיחות (כולל שינוי שם ומחיקה) בסרגל הצד

st.markdown("#### פרטי התחברות")
st.session_state.setdefault("account_form", None)       # None / "username" / "password": רק טופס אחד פתוח, ורק אחרי לחיצה
username_col, password_col, _ = st.columns([1, 1, 2])
if username_col.button("החלפת שם משתמש", key="open_username_form", use_container_width=True):
    st.session_state["account_form"] = None if st.session_state["account_form"] == "username" else "username"
    st.rerun()
if password_col.button("החלפת סיסמה", key="open_password_form", use_container_width=True):
    st.session_state["account_form"] = None if st.session_state["account_form"] == "password" else "password"
    st.rerun()

notice = st.session_state.pop("account_notice", None)
if notice:
    st.success(notice)

if st.session_state["account_form"] == "username":
    with st.form("change_username", border=True, clear_on_submit=False):
        st.caption("שם המשתמש משמש לכניסה. השיחות וההיסטוריה שלך נשמרות. נדרשת הסיסמה הנוכחית.")
        new_username = st.text_input("שם משתמש חדש")
        password = st.text_input("סיסמה נוכחית", type="password")
        submitted = st.form_submit_button("עדכון שם משתמש", type="primary")
    if submitted:
        try:
            updated = auth.change_username(user["username"], password, new_username)
        except auth.LoginError as error:
            st.error(str(error))
        else:
            st.session_state["user"] = updated
            st.session_state["account_form"] = None
            st.session_state["account_notice"] = f"שם המשתמש הוחלף ל-{updated['username']}. בכניסה הבאה יש להשתמש בשם החדש."
            st.rerun()

if st.session_state["account_form"] == "password":
    with st.form("change_password", border=True, clear_on_submit=True):
        old = st.text_input("סיסמה נוכחית", type="password")
        new = st.text_input("סיסמה חדשה", type="password")
        again = st.text_input("אימות סיסמה חדשה", type="password")
        submitted = st.form_submit_button("עדכון סיסמה", type="primary")
    if submitted:
        if new != again:
            st.error("הסיסמה החדשה והאימות אינם זהים.")
        else:
            try:
                auth.change_password(user["username"], old, new)
            except auth.LoginError as error:
                st.error(str(error))
            else:
                st.session_state["account_form"] = None
                st.session_state["account_notice"] = "הסיסמה עודכנה."
                st.rerun()

st.divider()
if st.button("יציאה מהמערכת"):
    logout()
