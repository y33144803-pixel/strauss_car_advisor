"""מסך ניהול: צפייה בכל השיחות (לקריאה בלבד, עם סינון ומיון) וניהול משתמשים - הוספה, איפוס סיסמה ומחיקה."""
from datetime import date, datetime

import streamlit as st

import auth
import db
from agent_tools import REP_FIELDS
from ui import avatar_for, card_title, fix_bidi, page_header, require_role, user_avatar_css

SORTS = {"עודכנו לאחרונה (מהחדש לישן)": ("updated_at", True), "עודכנו לאחרונה (מהישן לחדש)": ("updated_at", False),
         "נפתחו (מהחדש לישן)": ("created_at", True), "מספר הודעות (מהרב למעט)": ("messages", True),
         "משתמש (א-ת)": ("username", False)}
REP_FILTERS = ["הכול", "היתה שיחה עם נציג", "לא היתה שיחה עם נציג", "ממתינה לתשובת נציג"]


def rep_label(item: dict) -> str:
    """האם היתה שיחה עם נציג (בלי שמות; השמות בעמודה נפרדת)."""
    if item["rep_open"]:
        return "ממתינה לתשובה"
    return "היתה" if item["rep_total"] else "לא היתה"


def rep_names(item: dict, names: dict) -> str:
    return ", ".join(names.get(r, r) for r in item["reps"]) or "—"


def when(value: str) -> str:
    """תאריך ושעה בעברית (יום/חודש/שנה), כיחידה אחת כדי שלא יתהפך בתוך טקסט מימין לשמאל."""
    return "⁦" + datetime.fromisoformat(value).strftime("%d/%m/%Y %H:%M") + "⁩"


user = require_role("admin")
page_header("מסך ניהול", "כל השיחות, הנציגים והמשתמשים במקום אחד", "🛠️")
st.markdown("<style>[class*='st-key-sessrow_new_'] { background: #F4E9FB; border-radius: 12px; padding: 6px 12px;"
            " border-inline-start: 5px solid var(--primary); box-shadow: 0 2px 8px rgba(124,58,168,.10); }"
            "[class*='st-key-sessrow_seen_'] { background: #fff; border: 1px solid var(--line); border-radius: 12px;"
            " padding: 6px 12px; }</style>", unsafe_allow_html=True)

tab_sessions, tab_users = st.tabs(["שיחות", "משתמשים"], key="admin_tab", on_change="rerun")   # הלשונית נשמרת אחרי פעולה

def open_session(session_id: str) -> None:
    """callback של כפתור "צפייה": פותח את השיחה ורושם שהמנהל צפה בה."""
    st.session_state["admin_open"] = session_id
    db.mark_session_viewed(session_id, user["username"])


def close_session() -> None:
    st.session_state["admin_open"] = None


def unseen(item: dict) -> bool:
    """שיחה שלא נצפתה, או שהתעדכנה מאז הצפייה האחרונה."""
    return not item["viewed_at"] or item["updated_at"] > item["viewed_at"]


with tab_sessions:
    names = {u["username"]: u["display_name"] for u in db.list_users()}
    sessions = db.list_all_sessions()
    if not sessions:
        st.info("עוד אין שיחות במערכת.")
    else:
        first_day = min(item["updated_at"][:10] for item in sessions)
        owners = sorted({item["username"] for item in sessions})
        reps = sorted({r for item in sessions for r in item["reps"]})
        with st.container(key="card-filters"):
            card_title("סינון ומיון", "🔎")
            top = st.columns(3)
            picked_owners = top[0].multiselect("משתמשים", owners, placeholder="כל המשתמשים", key="admin_f_owners")
            rep_filter = top[1].selectbox("שיחה עם נציג", REP_FILTERS, key="admin_f_rep")
            picked_reps = top[2].multiselect("נציג שענה", reps, format_func=lambda r: names.get(r, r),
                                             placeholder="כל הנציגים", key="admin_f_reps")
            bottom = st.columns(3)
            period = bottom[0].date_input("טווח תאריכים (עדכון אחרון)",
                                          value=(date.fromisoformat(first_day), datetime.now().date()), key="admin_f_dates")
            view_filter = bottom[1].selectbox("מצב צפייה", ["הכול", "טרם נצפו", "נצפו"], key="admin_f_viewed")
            sort_by = bottom[2].selectbox("מיון", list(SORTS), key="admin_f_sort")

        shown = sessions
        if picked_owners:
            shown = [x for x in shown if x["username"] in picked_owners]
        if rep_filter == REP_FILTERS[1]:
            shown = [x for x in shown if x["rep_total"] > 0]
        elif rep_filter == REP_FILTERS[2]:
            shown = [x for x in shown if x["rep_total"] == 0]
        elif rep_filter == REP_FILTERS[3]:
            shown = [x for x in shown if x["rep_open"] > 0]
        if picked_reps:
            shown = [x for x in shown if set(picked_reps) & set(x["reps"])]
        if view_filter == "טרם נצפו":
            shown = [x for x in shown if unseen(x)]
        elif view_filter == "נצפו":
            shown = [x for x in shown if not unseen(x)]
        if isinstance(period, (tuple, list)) and len(period) == 2:
            start, end = period[0].isoformat(), period[1].isoformat()
            shown = [x for x in shown if start <= x["updated_at"][:10] <= end]
        sort_key, descending = SORTS[sort_by]
        shown = sorted(shown, key=lambda x: x[sort_key], reverse=descending)

        opened = st.session_state.get("admin_open")
        by_id = {x["session_id"]: x for x in sessions}
        if opened and opened in by_id:
            item = by_id[opened]
            head, close = st.columns([5, 1], vertical_alignment="center")
            head.markdown(f"**{item['title']}** · {item['username']} · {when(item['updated_at'])}")
            close.button("סגירת השיחה", key="admin_close_session", use_container_width=True, on_click=close_session)
            st.caption("צפייה בלבד - אי אפשר לכתוב או לשנות שיחה של משתמש.")
            st.markdown(user_avatar_css(item["display_name"]), unsafe_allow_html=True)    # האות של בעל השיחה
            for message in (m for m in db.load_messages(opened) if not m.get("hidden")):
                with st.chat_message(message["role"], avatar=avatar_for(message["role"])):
                    st.markdown(fix_bidi(message["content"]))
            questions = db.list_session_questions(opened)
            if questions:
                st.markdown("##### שאלות לנציג בשיחה")
                for q in questions:
                    who = names.get(q["answered_by"], q["answered_by"]) if q["answered_by"] else None
                    answer = (f"{q['answer_value']:,.0f} ₪" if q["answer_value"] is not None else (q["answer_note"] or "-")) \
                        if q["status"] in ("answered", "applied") else "ממתינה לתשובה"
                    st.markdown(f"- {REP_FIELDS.get(q['missing_field'], q['missing_field'])} · {answer}"
                                + (f" · ענה/תה: {who}" if who else ""))
        else:
            st.caption(f"{len(shown)} שיחות מתוך {len(sessions)}. שורה מסומנת = טרם נצפתה או עודכנה מאז הצפייה.")
            if not shown:
                st.info("אין שיחות שמתאימות לסינון.")
            else:
                widths = [1.7, 1.7, 1.0, 1.1, 1.4, 0.7, 1.6, 2.2, 0.8]
                header = st.columns(widths)
                for column, label in zip(header, ["תאריך", "תאריך עדכון", "משתמש", "נציג", "נציגים שענו", "הודעות", "שיחה", "נצפתה לאחרונה", ""]):
                    column.markdown(f"**{label}**" if label else " ")
                for item in shown[:100]:
                    fresh = unseen(item)
                    with st.container(key=f"sessrow_{'new' if fresh else 'seen'}_{item['session_id']}"):
                        row = st.columns(widths, vertical_alignment="center")
                        row[0].write(when(item["created_at"]))
                        row[1].write(when(item["updated_at"]))
                        row[2].write(item["username"])
                        row[3].write(rep_label(item))
                        row[4].write(rep_names(item, names))
                        row[5].write(str(item["messages"]))
                        row[6].write(item["title"])
                        if not item["viewed_at"]:
                            row[7].markdown(":violet[**● טרם נצפתה**]")
                        else:
                            by = names.get(item["viewed_by"], item["viewed_by"] or "")
                            note = " · **עודכנה מאז**" if fresh else ""
                            row[7].markdown(f"{when(item['viewed_at'])} · {by}{note}")
                        row[8].button("צפייה", key=f"view_{item['session_id']}", use_container_width=True,
                                      on_click=open_session, args=(item["session_id"],))
                if len(shown) > 100:
                    st.caption("מוצגות 100 הראשונות. צמצמי בעזרת הסינון כדי לראות את השאר.")

with tab_users:
    notice = st.session_state.pop("admin_notice", None)
    if notice:
        st.success(notice)
    users = db.list_users()
    with st.container(key="card-users"):
        card_title(f"משתמשים ({len(users)})", "👥")
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
        # הגיבוי נטען מיד עם ההעלאה (בלי כפתור נוסף), פעם אחת לכל קובץ שהועלה
        if uploaded is not None and st.session_state.get("admin_backup_loaded") != uploaded.file_id:
            st.session_state["admin_backup_loaded"] = uploaded.file_id
            try:
                added = auth.import_users_json(uploaded.getvalue().decode("utf-8"))
            except (auth.LoginError, UnicodeDecodeError) as error:
                st.error(str(error) if isinstance(error, auth.LoginError) else "קובץ הגיבוי לא תקין.")
            else:
                st.session_state["admin_notice"] = f"נטענו {added} משתמשים חדשים מהגיבוי."
                st.rerun()
