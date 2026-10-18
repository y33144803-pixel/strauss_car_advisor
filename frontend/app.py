"""נקודת הכניסה של האפליקציה.

הרצה (מהתיקייה הראשית):  streamlit run frontend/app.py
"""
import logging
import os

import streamlit as st

import ui

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

st.set_page_config(page_title="יועץ הרכב של שטראוס", page_icon=ui.avatar_for("assistant") or "🚗", layout="wide",
                   initial_sidebar_state="expanded")

import auth

VIEWS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "views")
employee_page = st.Page(os.path.join(VIEWS, "employee.py"), title="מסך עובד", icon="🚗", default=True)
representative_page = st.Page(os.path.join(VIEWS, "representative.py"), title="מסך נציג", icon="🧑‍💼")
account_page = st.Page(os.path.join(VIEWS, "account.py"), title="האזור שלי", icon="👤")

# המסכים המותרים לכל תפקיד (עובד לא רואה את מסך הנציג ולהיפך). "both" = התחברות כבויה (פיתוח מקומי)
PAGES_BY_ROLE = {
    "employee": [employee_page, account_page],
    "rep": [representative_page, account_page],
    "both": [employee_page, representative_page, account_page],
}


@st.cache_resource
def load_users() -> int:
    """טעינת המשתמשים מ-AUTH_USERS למסד, פעם אחת בכל הפעלה של השרת."""
    return auth.seed_users()


ui.reload_changed_modules()      # שינוי בקוד ה-backend בזמן שהשרת רץ נטען מחדש בלי להפעיל את השרת
load_users()
user = ui.current_user()
if user is None:                 # לא מחוברים: רק מסך כניסה
    ui.setup_page()
    if not os.environ.get("AUTH_USERS"):
        st.error("לא הוגדרו משתמשים (AUTH_USERS), ולכן אי אפשר להיכנס. יש לפנות למי שהתקין את המערכת.")
    ui.render_login()
    st.stop()

pages = PAGES_BY_ROLE.get(user["role"], [account_page])
# הניווט של Streamlit מוסתר: הכפתורים נמצאים בבאנר הקבוע שלנו
current_page = st.navigation(pages, position="hidden")
ui.setup_page()
ui.topbar(pages, current_page, user)
current_page.run()
