"""האזור האישי: החלפת שם משתמש וסיסמה ויציאה. היסטוריית השיחות נמצאת בסרגל הצד."""
import html

import streamlit as st

import auth
import db
from ui import COOKIE_HOURS, badge, card_title, logout, page_header, render_history_sidebar, require_role

ROLE_BADGE = {"employee": "purple", "rep": "pink", "admin": "amber"}

user = require_role("employee", "rep", "admin")
page_header("האזור האישי", "הפרטים והאבטחה של החשבון שלך", "👤")

role_label = auth.ROLE_LABELS.get(user["role"], "")
name = (user.get("display_name") or user["username"]).strip()
with st.container(key="card-profile"):
    st.markdown(
        f"<div class='profile'><div class='avatar'>{html.escape(name[0])}</div><div>"
        f"<div class='pname'>{html.escape(name)}</div>"
        f"<div class='pmeta'>{badge(role_label, ROLE_BADGE.get(user['role'], 'gray'))}"
        f"<span>שם משתמש: <b>{html.escape(user['username'])}</b></span></div></div></div>",
        unsafe_allow_html=True)

if user["role"] == "employee":
    render_history_sidebar(st.session_state.get("sid"))     # היסטוריית השיחות (כולל שינוי שם ומחיקה) בסרגל הצד

st.session_state.setdefault("account_form", None)       # None / "username" / "password": רק טופס אחד פתוח, ורק אחרי לחיצה
notice = st.session_state.pop("account_notice", None)

with st.container(key="card-security"):
    card_title("פרטי התחברות", "🔐")
    st.caption("כל פעולה דורשת את הסיסמה הנוכחית. שני הכפתורים פותחים טופס קצר.")
    username_col, password_col, _ = st.columns([1, 1, 2])
    if username_col.button("החלפת שם משתמש", key="open_username_form", icon=":material/badge:",
                           use_container_width=True):
        st.session_state["account_form"] = None if st.session_state["account_form"] == "username" else "username"
        st.rerun()
    if password_col.button("החלפת סיסמה", key="open_password_form", icon=":material/lock:", use_container_width=True):
        st.session_state["account_form"] = None if st.session_state["account_form"] == "password" else "password"
        st.rerun()

    if notice:
        st.success(notice)

    if st.session_state["account_form"] == "username":
        with st.form("change_username", border=True, clear_on_submit=False):
            st.caption("שם המשתמש משמש לכניסה. השיחות וההיסטוריה שלך נשמרות. נדרשת הסיסמה הנוכחית.")
            new_username = st.text_input("שם משתמש חדש")
            password = st.text_input("סיסמה נוכחית", type="password")
            submit_col, _, cancel_col = st.columns([1, 2, 1])
            submitted = submit_col.form_submit_button("עדכון שם משתמש", type="primary")
            cancelled = cancel_col.form_submit_button("ביטול")
        if cancelled:
            st.session_state["account_form"] = None
            st.rerun()
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
            submit_col, _, cancel_col = st.columns([1, 2, 1])
            submitted = submit_col.form_submit_button("עדכון סיסמה", type="primary")
            cancelled = cancel_col.form_submit_button("ביטול")
        if cancelled:
            st.session_state["account_form"] = None
            st.rerun()
        if submitted:
            if new != again:
                st.error("הסיסמה החדשה והאימות אינם זהים.")
            else:
                try:
                    auth.change_password(user["username"], old, new)
                except auth.LoginError as error:
                    st.error(str(error))
                else:
                    # מנתקים את כל שאר המכשירים, ומנפיקים אסימון חדש להתחברות הנוכחית
                    db.clear_tokens(user["username"])
                    token = db.create_token(user["username"], COOKIE_HOURS)
                    st.session_state["auth_token"] = token
                    st.session_state["set_cookie"] = token
                    st.session_state["account_form"] = None
                    st.session_state["account_notice"] = "הסיסמה עודכנה. שאר המכשירים נותקו."
                    st.rerun()

if st.button("יציאה מהמערכת", icon=":material/logout:"):
    logout()
