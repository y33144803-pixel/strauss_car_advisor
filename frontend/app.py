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
import db

VIEWS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "views")
employee_page = st.Page(os.path.join(VIEWS, "employee.py"), title="מסך עובד", icon="🚗", default=True)
representative_page = st.Page(os.path.join(VIEWS, "representative.py"), title="מסך נציג", icon="🧑‍💼")
admin_page = st.Page(os.path.join(VIEWS, "admin.py"), title="מסך ניהול", icon="🛠️")
account_page = st.Page(os.path.join(VIEWS, "account.py"), title="האזור שלי", icon="👤")

# המסכים המותרים לכל תפקיד (עובד לא רואה את מסך הנציג ולהיפך). "both" = התחברות כבויה (פיתוח מקומי)
PAGES_BY_ROLE = {
    "employee": [employee_page, account_page],
    "rep": [representative_page, account_page],
    "admin": [admin_page, account_page],
    "both": [employee_page, representative_page, admin_page, account_page],
}


@st.cache_resource
def load_users() -> int:
    """טעינת המשתמשים מ-AUTH_USERS למסד, פעם אחת בכל הפעלה של השרת."""
    return auth.seed_users()


@st.cache_resource
def warm_up() -> bool:
    """טוען ברקע את המודולים הכבדים של הסוכן (llama-index, Gemini) כבר בעליית השרת, כשהמשתמש עוד על מסך
    הכניסה. בלי זה הכניסה נתקעת כמה שניות (בשרת איטי - הרבה יותר) על ייבוא הספריות, ומסך הכניסה נשאר מתחת."""
    import threading

    def work() -> None:
        try:
            import actions  # noqa: F401
            import agent  # noqa: F401
            import agent_tools  # noqa: F401
            from llama_index.core.llms import ChatMessage  # noqa: F401
            from llama_index.llms.google_genai import GoogleGenAI  # noqa: F401
        except Exception:
            logging.getLogger("strauss.app").exception("החימום של המודולים נכשל (לא קריטי)")

    threading.Thread(target=work, daemon=True).start()
    return True


warm_up()
if os.environ.get("DEV_RELOAD", "1") != "0" and not os.environ.get("RENDER"):
    ui.reload_changed_modules()  # רק בפיתוח (בענן הקוד לא משתנה בזמן ריצה). שינוי בקוד ה-backend בזמן שהשרת רץ נטען מחדש בלי להפעיל את השרת
load_users()
user = ui.current_user()
if user is None:                 # לא מחוברים: רק מסך כניסה
    ui.setup_page()
    ui.topbar([], None)          # אותו באנר כמו באתר, בלי כפתורי ניווט
    if not db.list_users():       # לא מ-AUTH_USERS ולא מקובץ הגיבוי
        st.error("לא הוגדרו משתמשים (AUTH_USERS או users_backup.json), ולכן אי אפשר להיכנס. יש לפנות למי שהתקין את המערכת.")
    ui.render_login()
    st.stop()

pages = PAGES_BY_ROLE.get(user["role"], [account_page])
# הניווט של Streamlit מוסתר. בבאנר אין כפתורי ניווט למשתמשים מחוברים: לחיצה על השם פותחת תפריט (אזור אישי ויציאה);
# רק בפיתוח מקומי בלי התחברות ("both") יש בבאנר כפתורים למעבר בין מסך עובד למסך נציג
current_page = st.navigation(pages, position="hidden")
st.session_state["_page"] = "employee" if current_page.title == employee_page.title else "other"
ui.setup_page()
ui.topbar([employee_page, representative_page] if user["role"] == "both" else [], current_page, user)
if user["role"] in ("rep", "admin"):         # נציג ומנהל: בלי סרגל צד (התפריט בבאנר, לחיצה על השם)
    st.markdown("<style>[data-testid='stSidebar'], [data-testid='stSidebarCollapsedControl'], "
                "[data-testid='collapsedControl'] { display: none !important; }</style>", unsafe_allow_html=True)
current_page.run()
