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

VIEWS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "views")
employee_page = st.Page(os.path.join(VIEWS, "employee.py"), title="מסך עובד", icon="🚗", default=True)
representative_page = st.Page(os.path.join(VIEWS, "representative.py"), title="מסך נציג", icon="🧑‍💼")
# הניווט של Streamlit מוסתר: הכפתורים נמצאים בבאנר הקבוע שלנו
current_page = st.navigation([employee_page, representative_page], position="hidden")

ui.reload_changed_modules()      # שינוי בקוד ה-backend בזמן שהשרת רץ נטען מחדש בלי להפעיל את השרת
ui.setup_page()
ui.topbar([employee_page, representative_page], current_page)
current_page.run()
