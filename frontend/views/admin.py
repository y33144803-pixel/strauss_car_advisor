"""מסך ניהול: צפייה בכל השיחות (לקריאה בלבד, עם סינון ומיון) וניהול משתמשים - הוספה, איפוס סיסמה ומחיקה."""
from datetime import date, datetime

import streamlit as st

import auth
import db
from ui import avatar_for, fix_bidi, require_role, user_avatar_css

SORTS = {"עודכנו לאחרונה (מהחדש לישן)": ("updated_at", True), "עודכנו לאחרונה (מהישן לחדש)": ("updated_at", False),
         "נפתחו (מהחדש לישן)": ("created_at", True), "מספר הודעות (מהרב למעט)": ("messages", True),
         "משתמש (א-ת)": ("username", False)}
REP_FILTERS = ["הכול", "היתה שיחה עם נציג", "לא היתה שיחה עם נציג", "ממתינה לתשובת נציג"]


def rep_label(item: dict) -> str:
    if item["rep_open"]:
        return "ממתינה לתשובה"
    return f"היתה ({item['rep_total']})" if item["rep_total"] else "לא היתה"


def when(value: str) -> str:
    return value[:16].replace("T", " ")


user = require_role("admin")
st.subheader("מסך ניהול")

tab_sessions, tab_users = st.tabs(["שיחות", "משתמשים"])

with tab_sessions:
    sessions = db.list_all_sessions()
    if not sessions:
        st.info("עוד אין שיחות במערכת.")
    else:
        first_day = min(item["updated_at"][:10] for item in sessions)
        owners = sorted({item["username"] for item in sessions})
        top = st.columns(4)
        picked_owners = top[0].multiselect("משתמשים", owners, placeholder="כל המשתמשים", key="admin_f_owners")
        rep_filter = top[1].selectbox("שיחה עם נציג", REP_FILTERS, key="admin_f_rep")
        period = top[2].date_input("טווח תאריכים (עדכון אחרון)",
                                   value=(date.fromisoformat(first_day), datetime.now().date()), key="admin_f_dates")
        sort_by = top[3].selectbox("מיון", list(SORTS), key="admin_f_sort")

        shown = sessions
        if picked_owners:
            shown = [x for x in shown if x["username"] in picked_owners]
        if rep_filter == REP_FILTERS[1]:
            shown = [x for x in shown if x["rep_total"] > 0]
        elif rep_filter == REP_FILTERS[2]:
            shown = [x for x in shown if x["rep_total"] == 0]
        elif rep_filter == REP_FILTERS[3]:
            shown = [x for x in shown if x["rep_open"] > 0]
        if isinstance(period, (tuple, list)) and len(period) == 2:
            start, end = period[0].isoformat(), period[1].isoformat()
            shown = [x for x in shown if start <= x["updated_at"][:10] <= end]
        sort_key, descending = SORTS[sort_by]
        shown = sorted(shown, key=lambda x: x[sort_key], reverse=descending)

        st.caption(f"{len(shown)} שיחות מתוך {len(sessions)}")
        if not shown:
            st.info("אין שיחות שמתאימות לסינון.")
        else:
            st.dataframe([{"משתמש": x["username"], "שיחה": x["title"], "עודכנה": when(x["updated_at"]),
                           "הודעות": x["messages"], "נציג": rep_label(x)} for x in shown],
                         hide_index=True, use_container_width=True)
            by_id = {x["session_id"]: x for x in shown}
            chosen = st.selectbox("צפייה בשיחה", list(by_id), index=None, placeholder="בחרי שיחה לצפייה",
                                  key="admin_session_pick",
                                  format_func=lambda sid: f"{by_id[sid]['username']} · {by_id[sid]['title']} · "
                                                          f"{when(by_id[sid]['updated_at'])}")
            if chosen:
                head, close = st.columns([5, 1], vertical_alignment="center")
                head.caption("צפייה בלבד - אי אפשר לכתוב או לשנות שיחה של משתמש.")
                # הניקוי ב-callback: אי אפשר לשנות מפתח של ווידג'ט אחרי שהוא כבר צויר בהרצה הנוכחית
                close.button("סגירת השיחה", key="admin_close_session", use_container_width=True,
                             on_click=lambda: st.session_state.update(admin_session_pick=None))
                st.markdown(user_avatar_css(by_id[chosen]["display_name"]), unsafe_allow_html=True)  # האות של הבעלים
                for message in db.load_messages(chosen):
                    with st.chat_message(message["role"], avatar=avatar_for(message["role"])):
                        st.markdown(fix_bidi(message["content"]))

with tab_users:
    notice = st.session_state.pop("admin_notice", None)
    if notice:
        st.success(notice)
    users = db.list_users()
    st.dataframe(
        [{"שם משתמש": u["username"], "שם תצוגה": u["display_name"], "תפקיד": auth.ROLE_LABELS.get(u["role"], u["role"]),
          "שיחות": u["sessions"], "נוצר": u["created_at"][:10]} for u in users],
        hide_index=True, use_container_width=True)

    st.session_state.setdefault("admin_form", None)      # None / "add" / "reset" / "delete": טופס אחד פתוח, רק אחרי לחיצה
    add_col, reset_col, delete_col, _ = st.columns([1, 1, 1, 2])
    for column, form, label in ((add_col, "add", "הוספת משתמש"), (reset_col, "reset", "איפוס סיסמה"),
                                (delete_col, "delete", "מחיקת משתמש")):
        if column.button(label, key=f"admin_open_{form}", use_container_width=True,
                         type="primary" if st.session_state["admin_form"] == form else "secondary"):
            st.session_state["admin_form"] = None if st.session_state["admin_form"] == form else form
            st.rerun()

    def close_form() -> None:
        st.session_state["admin_form"] = None
        st.rerun()

    form_name = st.session_state["admin_form"]
    if form_name == "add":
        with st.form("admin_add_user", clear_on_submit=False):
            new_name = st.text_input("שם משתמש")
            new_display = st.text_input("שם תצוגה")
            new_role = st.selectbox("תפקיד", list(auth.ROLES), format_func=auth.ROLE_LABELS.get)
            new_password = st.text_input("סיסמה ראשונית", type="password")
            go_col, _, cancel_col = st.columns([1, 2, 1])
            add = go_col.form_submit_button("הוספה", type="primary")
            cancelled = cancel_col.form_submit_button("ביטול")
        if cancelled:
            close_form()
        if add:
            try:
                auth.create_user(new_name, new_password, new_role, new_display)
            except auth.LoginError as error:
                st.error(str(error))
            else:
                st.session_state["admin_notice"] = f"המשתמש {new_name.strip()} נוסף ({auth.ROLE_LABELS[new_role]})."
                st.session_state["admin_form"] = None
                st.rerun()
    elif form_name in ("reset", "delete"):
        with st.form(f"admin_{form_name}_user", clear_on_submit=False):
            target = st.selectbox("משתמש", [u["username"] for u in users])
            if form_name == "reset":
                temp = st.text_input("סיסמה חדשה", type="password")
                st.caption("המשתמש ינותק מכל המכשירים וייכנס עם הסיסמה החדשה.")
            else:
                st.warning("המשתמש יימחק. השיחות שלו יישארו במערכת.")
            go_col, _, cancel_col = st.columns([1, 2, 1])
            go = go_col.form_submit_button("איפוס" if form_name == "reset" else "מחיקה", type="primary")
            cancelled = cancel_col.form_submit_button("ביטול")
        if cancelled:
            close_form()
        if go:
            try:
                if form_name == "reset":
                    auth.reset_password(target, temp)
                    done = f"הסיסמה של {target} אופסה, והמשתמש נותק מכל המכשירים."
                else:
                    auth.delete_user(user["username"], target)
                    done = f"המשתמש {target} נמחק."
            except auth.LoginError as error:
                st.error(str(error))
            else:
                st.session_state["admin_notice"] = done
                st.session_state["admin_form"] = None
                st.rerun()

    with st.expander("גיבוי ושחזור משתמשים"):
        st.caption("הגיבוי כולל משתמשים וסיסמאות מוצפנות (בלי שיחות).")
        st.download_button("הורדת גיבוי משתמשים", data=auth.export_users_json(), file_name="users_backup.json",
                           mime="application/json", key="admin_backup_download")
        uploaded = st.file_uploader("שחזור מקובץ גיבוי", type="json", key="admin_backup_upload")
        if uploaded is not None and st.button("טעינת הגיבוי", key="admin_backup_load"):
            try:
                added = auth.import_users_json(uploaded.getvalue().decode("utf-8"))
            except (auth.LoginError, UnicodeDecodeError) as error:
                st.error(str(error) if isinstance(error, auth.LoginError) else "קובץ הגיבוי לא תקין.")
            else:
                st.session_state["admin_notice"] = f"נטענו {added} משתמשים חדשים מהגיבוי."
                st.rerun()
