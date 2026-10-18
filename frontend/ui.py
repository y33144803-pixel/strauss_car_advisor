"""רכיבי תצוגה משותפים למסכים: עיצוב RTL, זיהוי שיחה וכרטיס ההשוואה."""
import html
import json
import os
import re
import sys
import uuid

import pandas as pd
import streamlit as st

BACKEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# הענף הראשון תופס סכום שכבר עטוף (כדי לא לעטוף שוב), השני סכום חשוף
_BACKEND_MTIMES: dict[str, float] = {}


def reload_changed_modules(directory: str = BACKEND_DIR, mtimes: dict | None = None) -> list[str]:
    """Streamlit עוקב אחרי שינויים רק בתיקיית הסקריפט (frontend), לא ב-backend. לכן שינוי בקוד ה-backend בזמן
    שהשרת רץ משאיר בזיכרון גרסה ישנה (ושגיאות כמו ImportError). כאן מזהים שינוי בקבצי התיקייה, ואז מוחקים את
    מודולי התיקייה מ-sys.modules, כך שהייבוא הבא טוען אותם מחדש. מחזיר את שמות המודולים שנמחקו."""
    mtimes = _BACKEND_MTIMES if mtimes is None else mtimes
    folder = os.path.normcase(os.path.abspath(directory))
    snapshot = {}
    for entry in os.listdir(folder):
        if entry.endswith(".py"):
            snapshot[entry] = os.path.getmtime(os.path.join(folder, entry))
    changed = bool(mtimes) and snapshot != mtimes          # ההרצה הראשונה רק שומרת מצב התחלתי
    mtimes.clear()
    mtimes.update(snapshot)
    removed = []
    if changed:
        for name, module in list(sys.modules.items()):
            path = getattr(module, "__file__", None)
            if path and os.path.normcase(os.path.abspath(path)).startswith(folder + os.sep):
                del sys.modules[name]
                removed.append(name)
    return removed


def _find_bot_image() -> str:
    """תמונת הבוט: images/bot-image.* (jpg / png / jpeg / webp)."""
    folder = os.path.join(os.path.dirname(BACKEND_DIR), "images")
    for extension in ("jpg", "png", "jpeg", "webp"):
        path = os.path.join(folder, f"bot-image.{extension}")
        if os.path.exists(path):
            return path
    return os.path.join(folder, "bot-image.jpg")


BOT_AVATAR = _find_bot_image()


def _find_login_image() -> str | None:
    """תמונת מסך הכניסה: images/login-image.* ; אם אין - תמונת הבוט."""
    folder = os.path.join(os.path.dirname(BACKEND_DIR), "images")
    for extension in ("jpg", "png", "jpeg", "webp"):
        path = os.path.join(folder, f"login-image.{extension}")
        if os.path.exists(path):
            return path
    return BOT_AVATAR if os.path.exists(BOT_AVATAR) else None


LOGIN_IMAGE = _find_login_image()


def avatar_for(role: str) -> str | None:
    """תמונת הבוט (images/bot-image.jpg) בבועות של הבוט. בבועות העובדת נשארת ברירת המחדל של Streamlit
    (st.chat_message לא מקבל אות עברית כאווטאר), והיא מוחלפת באות הראשונה של שמה ב-CSS: ראו user_avatar_css."""
    return BOT_AVATAR if role == "assistant" and os.path.exists(BOT_AVATAR) else None


def user_avatar_css() -> str:
    """CSS שמציג בעיגול של העובדת את האות הראשונה של שמה (במקום האייקון הכללי)."""
    user = st.session_state.get("user") or {}
    name = (user.get("display_name") or "").strip()
    if not name:
        return ""
    letter = json.dumps(name[0], ensure_ascii=False)    # מחרוזת CSS בטוחה (בלי הימלטות JSON שאינה חוקית ב-CSS)
    return ("<style>[data-testid='stChatMessageAvatarUser'] { position: relative; }"
            "[data-testid='stChatMessageAvatarUser'] > * { visibility: hidden; }"
            "[data-testid='stChatMessageAvatarUser']::after { content: " + letter + "; position: absolute; inset: 0; "
            "display: flex; align-items: center; justify-content: center; color: #fff; font-weight: 700; "
            "font-size: 1.6rem; }</style>")


_AMOUNT_RE = re.compile(r"(⁦[^⁩]*⁩)|(\d[\d,]*(?:\.\d+)?\s?₪)")


def fix_bidi(text: str) -> str:
    """בתוך משפט בעברית, סכום בשקלים ("9,321 ₪") עלול להתבלגן בגלל כיווניות. עוטפים כל סכום כיחידה אחת
    בתווי בידוד (LRI/PDI) - רק בתצוגה, כך שהטקסט השמור, הנשלח למודל וזה שמועתק נשארים נקיים."""
    return _AMOUNT_RE.sub(lambda m: m.group(1) or "⁦" + m.group(2) + "⁩", text)


def show_markdown(text: str) -> None:
    st.markdown(fix_bidi(text))

CSS = """
<style>
:root { --primary:#7C3AA8; --accent:#C2378C; --ink:#2B2540; --muted:#6F6A80; --soft:#F6F0FA;
        --good:#1E8E5A; --bad:#C0392B; --line:rgba(43,37,64,.12); }
html, body, [data-testid="stAppViewContainer"], [data-testid="stHeader"], [data-testid="stChatMessage"],
[data-testid="stMarkdownContainer"], [data-testid="stExpander"], [data-testid="stForm"], .stTextInput,
.stNumberInput, .stSelectbox, [data-testid="stChatInput"], [data-testid="stAlert"], [data-testid="stCaptionContainer"]
{ direction: rtl; text-align: right; }
/* ה-header של Streamlit מוסתר: הבאנר שלנו קבוע בראש המסך ומחליף אותו */
[data-testid="stHeader"] { display: none; }
.block-container { max-width: 1280px; padding-top: 6.2rem; padding-bottom: 1rem; }
.st-key-topbar img { border-radius: 14px; }

/* באנר עליון קבוע, עם כפתורי הניווט (מסך עובד / מסך נציג) */
.st-key-topbar { position: fixed; top: 0; left: 0; right: 0; z-index: 1000002; padding: 8px 28px;
                 background: radial-gradient(circle at 12% 30%, rgba(255,255,255,.22), transparent 38%),
                             linear-gradient(100deg, var(--accent), var(--primary));
                 border-bottom: 3px solid rgba(255,255,255,.35); border-radius: 0 0 18px 18px;
                 box-shadow: 0 6px 18px rgba(80,20,110,.35); direction: rtl; }
.st-key-topbar img { border: 3px solid rgba(255,255,255,.65); box-shadow: 0 3px 10px rgba(0,0,0,.25); }
.st-key-topbar .bar-title { text-shadow: 0 1px 3px rgba(0,0,0,.25); }
.st-key-topbar .user-chip { display: inline-flex; align-items: center; gap: 10px; background: rgba(255,255,255,.18);
                            border: 1px solid rgba(255,255,255,.35); border-radius: 999px; padding: 5px 16px 5px 6px; }
.st-key-topbar .user-chip .dot { width: 34px; height: 34px; border-radius: 50%; background: #fff; color: var(--primary);
                                 display: inline-flex; align-items: center; justify-content: center; font-weight: 800;
                                 font-size: 1.1rem; }
.st-key-topbar .user-chip .dot, .st-key-topbar .user-chip .dot * { color: var(--primary); }
.st-key-topbar .user-chip .who { line-height: 1.15; font-weight: 700; font-size: .95rem; }
.st-key-topbar .user-chip .role { font-size: .75rem; opacity: .9; font-weight: 400; }
.st-key-topbar, .st-key-topbar * { color: #fff; }
.st-key-topbar .bar-title { font-size: 1.35rem; font-weight: 700; line-height: 1.2; }
.st-key-topbar .bar-sub { font-size: .85rem; opacity: .92; }
/* ל-markdown של Streamlit יש שוליים שליליים שמצמצמים את הבאנר ומחתכים את טקסט המשנה */
.st-key-topbar [data-testid="stMarkdown"], .st-key-topbar [data-testid="stMarkdownContainer"] { margin: 0; }
.st-key-topbar a, .st-key-topbar .nav-pill { background: rgba(255,255,255,.16); border-radius: 999px; padding: .3rem 1rem;
                                             text-decoration: none; justify-content: center; text-align: center; }
.st-key-topbar a:hover { background: rgba(255,255,255,.32); }
.st-key-topbar a, .st-key-topbar .nav-pill { white-space: nowrap; font-size: .92rem; padding: .3rem .7rem; }
.st-key-topbar button { background: rgba(255,255,255,.16); border: 0; color: #fff; border-radius: 999px; white-space: nowrap; }
.st-key-topbar button:hover { background: rgba(255,255,255,.32); color: #fff; }
.st-key-topbar .nav-pill.active { background: #fff; color: var(--primary); font-weight: 700; }

/* כפתור העתקה מתחת לכל בועת הודעה: הסוכן - בצד שמאל, העובדת - בצד ימין */
[data-testid="stChatMessage"] { position: relative; margin-bottom: 1.9rem; }
.copy-msg { position: absolute; bottom: -1.7rem; left: 10px; border: 0; background: transparent; color: var(--primary);
            font-size: 1rem; cursor: pointer; opacity: .55; transition: opacity .15s; padding: 2px 8px; border-radius: 6px; }
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) .copy-msg { left: auto; right: 10px; }
[data-testid="stChatMessage"]:hover .copy-msg, .copy-msg:hover { opacity: 1; }
.copy-msg:hover { background: rgba(0,0,0,.07); }

/* סטטוס המתנה: הטקסט נכתב אות אחרי אות (מימין לשמאל), נשאר רגע, וחוזר על עצמו */
.typing-status { display: inline-block; direction: rtl; white-space: nowrap; color: var(--primary); font-weight: 600;
                 animation: typing-reveal 2.4s steps(var(--n)) infinite; }
@keyframes typing-reveal { 0% { clip-path: inset(0 0 0 100%); } 70%, 100% { clip-path: inset(0 0 0 0); } }

/* כפתור "העתקת ההשוואה" - בנוי כמו כפתורי Streamlit */
.cmp-btn { width: 100%; padding: .45rem 1rem; border: 1px solid rgba(49,51,63,.2); border-radius: .5rem;
           background: #fff; color: inherit; font: inherit; cursor: pointer; }
.cmp-btn:hover { border-color: var(--primary); color: var(--primary); }

/* חלון השיחה מתאים את גובהו למסך, כך שתיבת הכתיבה תמיד גלויה בלי גלילה */
div.st-key-chatbox.st-key-chatbox { height: calc(100vh - 320px) !important; min-height: 300px; flex: 0 0 auto !important; }
div.st-key-chatbox-picker.st-key-chatbox-picker { height: calc(100vh - 510px) !important; min-height: 200px; flex: 0 0 auto !important; }
div.st-key-chatbox-btn.st-key-chatbox-btn { height: calc(100vh - 440px) !important; min-height: 280px; flex: 0 0 auto !important; }
div:has(> .st-key-chatbox), div:has(> .st-key-chatbox-btn), div:has(> .st-key-chatbox-picker) { flex: 0 0 auto !important; height: auto !important; }

/* כשההשוואה ארוכה והדף גולל, עמודת השיחה נשארת במקומה (דבוקה מתחת לבאנר) ולא נגללת למעלה */
[data-testid="stColumn"]:has(.st-key-chatbox, .st-key-chatbox-btn, .st-key-chatbox-picker) { align-self: flex-start; position: sticky; top: 5.9rem; }

/* תיבת הכתיבה באותו רוחב כמו עמודת השיחה (3 מתוך 5, פחות הרווח בין העמודות), צמודה לצד הימני */
[data-testid="stBottomBlockContainer"] [data-testid="stChatInput"] { width: calc((100% - 2rem) * 0.6); margin-right: 0; margin-left: auto; }

/* מסך הכניסה: כרטיס טופס, והתמונה צמודה לתחתית המסך (בלי שוליים) */
.st-key-logincard { background: #fff; border: 1px solid #E6DDF0; border-radius: 18px; padding: 26px 24px;
                    box-shadow: 0 8px 24px rgba(0,0,0,.07); }
.st-key-logincard h3 { margin-top: 0; }
.st-key-loginimage { position: fixed; left: 0; bottom: 0; width: auto; margin: 0; padding: 0; line-height: 0; z-index: 1; }
.st-key-loginimage, .st-key-loginimage * { margin: 0 !important; padding: 0 !important; }
.st-key-loginimage img { display: block; height: calc(100vh - 110px); width: auto !important; max-width: 52vw;
                         object-fit: contain; object-position: left bottom; border-radius: 0; }
@media (max-width: 900px) {
  .st-key-loginimage { position: static; }
  .st-key-loginimage img { height: auto; width: 100% !important; max-width: 100%; }
}

/* סרגל הצד: רשימת שיחות (כפתור לכל שיחה, השיחה הפעילה בסגול) ושורת איקונים בתחתית */
.st-key-chat-list button { justify-content: flex-start; text-align: right; }
.st-key-chat-list button p { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; width: 100%; }
/* איקון ⋯ בתוך כפתור השיחה, בקצה השמאלי שלו */
[class*="st-key-chatrow_"] { position: relative; }
[class*="st-key-chatrow_"] > [data-testid="stLayoutWrapper"]:has([data-testid="stPopover"]) {
    position: absolute; left: 6px; top: 50%; transform: translateY(-50%); width: auto; z-index: 2; }
[class*="st-key-chatrow_"] [data-testid="stPopover"] button { background: transparent; border: 0; box-shadow: none;
    min-height: 0; padding: 2px 4px; color: #6b6b7b; }
[class*="st-key-chatrow_"] [data-testid="stPopover"] button:hover { background: rgba(0,0,0,.08); }
[class*="st-key-chatrow_a_"] [data-testid="stPopover"] button, [class*="st-key-chatrow_a_"] [data-testid="stPopover"] button * { color: #fff; }
[class*="st-key-chatrow_"] [data-testid="stPopover"] button div[aria-hidden="true"] { display: none; }
.st-key-chat-list button p { padding-left: 34px; }
[data-testid="stSidebarUserContent"] > div > [data-testid="stVerticalBlock"] { min-height: calc(100vh - 7rem); }
div:has(> .st-key-sidebar-footer) { order: 999; margin-top: auto; }

/* פאנל הצד: "הנתונים שנקלטו עד כה" */
[data-testid="stSidebarContent"] { padding-top: 6.4rem; }
[data-testid="stSidebarHeader"] { display: none; }
.state-card { border: 1px solid var(--line); border-radius: 14px; padding: 12px 14px; background: #fff; direction: rtl; }
.state-card .state-title { font-weight: 700; color: var(--primary); margin-bottom: 4px; }
.state-row { display: flex; justify-content: space-between; gap: 10px; padding: 6px 0;
             border-bottom: 1px solid var(--line); font-size: .92rem; }
.state-row:last-child { border-bottom: 0; }
.state-row .k { color: var(--muted); white-space: nowrap; }
.state-row .v { font-weight: 700; color: var(--ink); text-align: left; unicode-bidi: plaintext; }
.state-row.empty .v { font-weight: 400; font-style: italic; color: #9A95A8; }
.state-row.filled .k::before { content: "✓ "; color: var(--good); font-weight: 700; }
.state-row.empty .k::before { content: "○ "; color: #B7B2C4; }
.state-hint { font-size: .8rem; color: var(--muted); margin-top: 8px; }

/* בורר הרכבים מעל תיבת השיחה */
.st-key-car_picker { border: 1px solid var(--line); border-radius: 14px; padding: 10px 12px; background: var(--soft); }

/* בועות שיחה */
[data-testid="stChatMessage"] { border-radius:14px; padding:.7rem 1rem; background:#F4F4F8; font-size:1.02rem; }
/* הסוכן בצד ימין והעובד בצד שמאל, כל בועה ברוחב התוכן שלה (עד 85%). כל הודעה עטופה ב-stLayoutWrapper */
[data-testid="stLayoutWrapper"]:has(> [data-testid="stChatMessage"]) { align-self: flex-start; max-width: 85%; }
[data-testid="stLayoutWrapper"]:has(> [data-testid="stChatMessage"] [data-testid="stChatMessageAvatarUser"]) { align-self: flex-end; }
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) { flex-direction: row-reverse; }
/* העובד: לבן עם מסגרת סגולה; הסוכן: רקע אפור-בהיר - כך ברור מי אמר מה */
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) { background:#fff; border:1.5px solid var(--primary); }

/* תמונת הבוט בבועות: גדולה וברורה (ברירת המחדל של Streamlit קטנה מאוד) */
[data-testid="stChatMessage"] img[alt$="avatar"] { width:4.2rem; height:4.2rem; min-width:4.2rem; border-radius:16px; object-fit:cover; }
[data-testid="stImage"] img { border-radius:18px; }

/* כרטיסי מדדים */
.kpis { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin:6px 0 14px; direction:rtl; }
.kpi { border:1px solid var(--line); border-radius:12px; padding:10px 14px; background:#fff; }
.kpi-label { font-size:.82rem; color:var(--muted); }
.kpi-value { font-size:1.5rem; font-weight:700; color:var(--ink); direction:ltr; text-align:right; white-space:nowrap; }
.kpi.diff.pos { background:#EAF7F0; border-color:#BFE5D0; } .kpi.diff.pos .kpi-value { color:var(--good); }
.kpi.diff.neg { background:#FCEDEB; border-color:#F2C6C0; } .kpi.diff.neg .kpi-value { color:var(--bad); }

/* טבלת השוואה */
.cmp-table { width:100%; border-collapse:collapse; font-size:.95rem; direction:rtl; table-layout:fixed; }
.cmp-table col.c-label { width:44%; } .cmp-table col.c-val { width:28%; }
.cmp-table th { background:var(--soft); color:var(--primary); padding:8px 10px; text-align:right; font-weight:700; }
.cmp-table td { padding:7px 10px; border-bottom:1px solid var(--line); text-align:right; }
.cmp-table td.num { direction:ltr; text-align:right; white-space:nowrap; }
.cmp-table td.text { font-weight:600; }
.cmp-table .sub { font-size:.8rem; color:var(--muted); }
.cmp-table tr.total td { font-weight:800; font-size:1.05rem; background:var(--soft); border-top:2px solid var(--primary); }
.cmp-note { font-size:.85rem; color:var(--muted); margin:8px 0; }
.empty-card { border:2px dashed var(--line); border-radius:14px; padding:28px 18px; text-align:center; color:var(--muted); }
.empty-card .big { font-size:2.2rem; }
</style>
"""


COPY_BUTTON_JS = """
(() => {                         // עטיפה: אחרת ההרצה השנייה של הסקריפט מצהירה שוב על doc ומקבלת שגיאה
const doc = window.parent.document;
if (!doc.__copyButtons) {
  doc.__copyButtons = true;
  const add = () => doc.querySelectorAll('[data-testid="stChatMessage"]').forEach(msg => {
    if (msg.querySelector('.copy-msg')) return;
    const btn = doc.createElement('button');
    btn.className = 'copy-msg'; btn.type = 'button'; btn.title = 'העתקה'; btn.textContent = '⧉';
    btn.onclick = async () => {
      const text = (msg.querySelector('[data-testid="stChatMessageContent"]') || msg).innerText.replace(/⁦|⁩/g, '').trim();
      try { await navigator.clipboard.writeText(text); } catch (e) {
        const t = doc.createElement('textarea'); t.value = text; doc.body.appendChild(t); t.select(); doc.execCommand('copy'); t.remove();
      }
      btn.textContent = '✓'; setTimeout(() => btn.textContent = '⧉', 1200);
    };
    msg.appendChild(btn);
  });
  new MutationObserver(add).observe(doc.body, {childList: true, subtree: true});
  add();
}
})();
"""


def setup_page() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(user_avatar_css(), unsafe_allow_html=True)
    emit_cookie_changes()
    st.html(f"<script>{COPY_BUTTON_JS}</script>", unsafe_allow_javascript=True)   # כפתור "העתקה" בכל הודעה בשיחה


def topbar(pages: list, current, user: dict | None = None) -> None:
    """הבאנר העליון: קבוע בראש המסך תמיד, וכפתורי הניווט עליו (רק המסכים המותרים לתפקיד המשתמש).
    pages - דפי st.navigation המותרים; current - הדף הפעיל (מסומן בלבן, ולא כקישור).
    כשמשתמש מחובר: כפתור "יציאה". הקיבוע נעשה ב-CSS לפי המפתח "topbar"."""
    with st.container(key="topbar"):
        title, mid, nav = st.columns([3.2, 2.6, 4.2], vertical_alignment="center")
        title.markdown("<div class='bar-title'>🚗 יועץ הרכב של שטראוס</div>"
                       "<div class='bar-sub'>מה יקרה לנטו שלי אם אחליף רכב, אעבור לרכב חשמלי או אוותר על הרכב?</div>",
                       unsafe_allow_html=True)
        with mid:                        # באמצע: הרובוט והכיתוב "היועץ שלך"
            mascot, tagline = st.columns([1, 3], vertical_alignment="center")
            if avatar_for("assistant"):
                mascot.image(avatar_for("assistant"), width=64)
            tagline.markdown("<div class='bar-title'>היועץ שלך</div><div class='bar-sub'>שואלים אותי הכול על הנטו</div>",
                             unsafe_allow_html=True)
        with nav:
            if not pages:                            # מסך הכניסה והמשתמשים המחוברים: הבאנר בלי כפתורים
                if user and user.get("role") != "both":      # (הגדרות ויציאה בסרגל הצד) - רק מי שמחובר
                    name = (user.get("display_name") or user["username"]).strip()
                    role = "עובד" if user.get("role") == "employee" else "נציג תחבורה"
                    st.markdown("<div style='display:flex; justify-content:flex-end; direction:ltr'>"
                                "<div class='user-chip' style='direction:rtl'>"
                                f"<span class='dot'>{html.escape(name[0])}</span>"
                                f"<span class='who'>שלום, {html.escape(name)}"
                                f"<br><span class='role'>{role}</span></span></div></div>",
                                unsafe_allow_html=True)
                return
            columns = st.columns(len(pages))
            for column, page in zip(columns, pages):
                if current is not None and page.title == current.title:
                    column.markdown(f"<div class='nav-pill active'>{page.icon} {html.escape(page.title)}</div>",
                                    unsafe_allow_html=True)
                else:
                    column.page_link(page, label=page.title, icon=page.icon, use_container_width=True)


def _rate(value) -> str:
    return f"{value:g}%"


def state_rows(state) -> list[tuple[str, str | None]]:
    """שורות כרטיס "הנתונים שנקלטו עד כה": (כותרת, ערך או None אם טרם הוזן). לפי סדר השיחה."""
    emp, cur, tgt = state.employee, state.current, state.target
    rows = [
        ("קבוצת זכאות", emp.eligibility_group),
        ("שכר בסיס", None if emp.base_salary is None else _money(emp.base_salary)),
        ("עמלות", None if emp.commissions is None else _money(emp.commissions)),
        ("בונוס", None if emp.bonus is None else _money(emp.bonus)),
        ("פנסיה (עובד)", None if emp.pension_rate is None else _rate(emp.pension_rate)),
        ("קרן השתלמות (עובד)", None if emp.study_fund_rate is None else _rate(emp.study_fund_rate)),
        ("נקודות זיכוי", None if emp.credit_points is None else f"{emp.credit_points:g}"),
    ]
    # עיסוק וילדים נדרשים רק בחלק מהמקרים (ויתור / מסלול ברוכת ילדים) - מוצגים רק אם נמסרו
    if emp.occupation:
        rows.append(("עיסוק", emp.occupation))
    if emp.children_under_23 is not None:
        rows.append(("ילדים עד גיל 23", str(emp.children_under_23)))

    if cur.kind is None:
        rows.append(("רכב נוכחי", None))
    elif cur.kind == "car":
        rows.append(("רכב נוכחי", cur.label))
        rows.append(("השתתפות ברכב הנוכחי", None if cur.participation is None else _money(cur.participation)))
    else:
        rows.append(("רכב נוכחי", cur.label))

    if tgt.kind is None:
        rows.append(("חלופה מבוקשת", None))
    elif tgt.kind == "car":
        rows.append(("חלופה מבוקשת", tgt.label))
        if tgt.participation is None:
            rows.append(("השתתפות ברכב החדש", None))
        else:
            suffix = " (זמנית)" if tgt.participation_status == "provisional" else ""
            rows.append(("השתתפות ברכב החדש", _money(tgt.participation) + suffix))
    else:
        rows.append(("חלופה מבוקשת", tgt.label))
    return rows


def state_progress(state) -> tuple[int, int]:
    """(כמה מהנתונים הנדרשים לחישוב כבר נקלטו, כמה נדרשים בסך הכול).
    הסכום קבוע (שבעה נתוני שכר + רכב נוכחי + השתתפות בו + חלופה + השתתפות בה), כדי שהיחס לא "יזוז" תוך כדי השיחה.
    השתתפות שאינה רלוונטית (ויתור / ללא רכב) נספרת כהושלמה."""
    from state import REQUIRED_EMPLOYEE_FIELDS
    emp, cur, tgt = state.employee, state.current, state.target
    slots = [getattr(emp, field) is not None for field in REQUIRED_EMPLOYEE_FIELDS]
    slots.append(cur.kind is not None)
    slots.append(cur.kind in ("waiver", "none") or (cur.kind == "car" and cur.participation is not None))
    slots.append(tgt.kind is not None)
    slots.append(tgt.kind == "waiver" or (tgt.kind == "car" and tgt.participation is not None))
    return sum(slots), len(slots)


def state_card_html(state) -> str:
    """כרטיס "הנתונים שנקלטו עד כה": שדות ריקים ("טרם הוזן") שמתמלאים ככל שהשיחה מתקדמת."""
    rows = []
    for label, value in state_rows(state):
        if value is None:
            rows.append(f"<div class='state-row empty'><span class='k'>{html.escape(label)}</span>"
                        "<span class='v'>טרם הוזן</span></div>")
        else:
            rows.append(f"<div class='state-row filled'><span class='k'>{html.escape(label)}</span>"
                        f"<span class='v'>{html.escape(str(value))}</span></div>")
    note = ""
    if state.waiting_for_representative:
        note = "<div class='state-hint'>⏳ ממתין לתשובת נציג התחבורה</div>"
    return f"<div class='state-card'><div class='state-title'>הנתונים שנקלטו עד כה</div>{''.join(rows)}{note}</div>"


def render_state_card(state) -> None:
    """מצייר את הכרטיס (וסרגל התקדמות) במקום שבו הפונקציה נקראת - בפועל בפאנל הצד."""
    done, total = state_progress(state)
    st.progress(done / total, text=f"נקלטו {done} מתוך {total} נתונים נדרשים לחישוב")
    st.markdown(state_card_html(state), unsafe_allow_html=True)


def _owner_for_sessions() -> str | None:
    """שם המשתמש שאליו שייכות השיחות, או None כשההתחברות כבויה (פיתוח מקומי)."""
    user = st.session_state.get("user")
    return user["username"] if user and user.get("role") != "both" else None


def get_session_id() -> str:
    """מזהה השיחה נשמר ב-URL (?sid=...) - לכן רענון העמוד ממשיך את אותה שיחה.
    כשמחוברים: מזהה ששייך למשתמש אחר (או שיחה ישנה בלי בעלים) לא נטען - נפתחת במקומו שיחה של המשתמש
    (האחרונה שלו, או חדשה). כל שיחה חדשה משויכת למשתמש."""
    import db
    owner = _owner_for_sessions()
    sid = st.query_params.get("sid") or st.session_state.get("sid")
    if sid and owner and not db.claim_session(sid, owner):
        sid = None
    if not sid and owner:
        history = db.list_user_sessions(owner)
        sid = history[0]["session_id"] if history else None
    if not sid:
        sid = uuid.uuid4().hex[:12]
        if owner:
            db.claim_session(sid, owner)
    st.session_state["sid"] = sid
    st.query_params["sid"] = sid
    return sid


def new_session() -> None:
    """שיחה חדשה: מזהה חדש (ולא "האחרונה של המשתמש")."""
    sid = uuid.uuid4().hex[:12]
    st.session_state["sid"] = sid
    st.session_state.pop("chat_error", None)
    st.query_params["sid"] = sid


# ---------------------------------------------------------------- התחברות

COOKIE_NAME = "strauss_auth"
COOKIE_HOURS = 12


def _cookie_script(value: str, max_age: int) -> str:
    secure = "; Secure" if os.environ.get("RENDER") else ""
    return ("<script>(() => { window.parent.document.cookie = "
            f"'{COOKIE_NAME}={value}; path=/; max-age={max_age}; SameSite=Lax{secure}'; }})();</script>")


def current_user() -> dict | None:
    """המשתמש המחובר: מהסשן, ואחרי רענון - מהעוגייה (אסימון אקראי שנשמר רק כגיבוב במסד).
    כשההתחברות כבויה (AUTH_DISABLED=1) מחזיר משתמש מקומי שרואה את כל המסכים."""
    import auth
    import db
    if not auth.auth_enabled():
        st.session_state.setdefault("user", {"username": "local", "display_name": "מקומי", "role": "both"})
        return st.session_state["user"]
    if st.session_state.get("user"):
        return st.session_state["user"]
    try:
        token = st.context.cookies.get(COOKIE_NAME)
    except Exception:                      # סביבה בלי גישה לעוגיות (למשל בדיקות)
        token = None
    user = db.user_for_token(token) if isinstance(token, str) and token else None
    if user:
        st.session_state["user"] = user
        st.session_state["auth_token"] = token
    return user


def require_role(*roles: str) -> dict:
    """הגנה בכניסה למסך: רק תפקידים מותרים (גם אם מישהו הקליד כתובת של מסך שאינו שלו)."""
    user = st.session_state.get("user")
    if not user or user.get("role") not in (*roles, "both"):
        st.error("אין הרשאה למסך הזה.")
        st.stop()
    return user


def login_prefill() -> tuple[str, str]:
    """שם משתמש וסיסמה שימולאו מראש בשדות הכניסה (נוחות לדמו), לפי LOGIN_PREFILL_USER.
    רק משתמש בתפקיד עובד; נציג לא ימולא מראש לעולם. הסיסמה נלקחת מהגדרת AUTH_USERS.
    שימו לב: הסיסמה נשלחת לדפדפן, ולכן כל מי שיפתח את האתר יוכל להיכנס כמשתמש הזה."""
    import auth
    wanted = os.environ.get("LOGIN_PREFILL_USER", "").strip()
    if not wanted:
        return "", ""
    try:
        users = auth.parse_users(os.environ.get("AUTH_USERS", ""))
    except ValueError:
        return "", ""
    import db
    for user in users:
        if user["username"] == wanted and user["role"] == "employee":
            stored = db.get_user(user["username"])
            # אם העובד כבר החליף שם משתמש או סיסמה, הפרטים מהגדרות השרת כבר לא נכונים - לא ממלאים מראש
            if stored and auth.verify_password(user["password"], stored["password_hash"]):
                return user["username"], user["password"]
    return "", ""


def render_login() -> None:
    """מסך כניסה (שם משתמש וסיסמה): באנר, כרטיס עם הטופס ותמונה. אחרי כניסה מוצלחת המשתמש נשמר בסשן ובעוגייה."""
    import auth
    import db
    st.markdown("<style>[data-testid='stSidebar'], [data-testid='stSidebarCollapsedControl'] { display: none; }</style>", unsafe_allow_html=True)
    form_col, image_col = st.columns([1, 1], gap="large", vertical_alignment="center")
    with form_col:
        with st.container(key="logincard"):
            st.markdown("### כניסה למערכת")
            st.caption("היכנסו עם שם המשתמש והסיסמה שקיבלתם.")
            with st.form("login", border=False):
                default_user, default_password = login_prefill()
                username = st.text_input("שם משתמש", value=default_user, key="login_username")
                password = st.text_input("סיסמה", value=default_password, type="password", key="login_password")
                submitted = st.form_submit_button("כניסה", type="primary", use_container_width=True)
            if submitted:
                try:
                    user = auth.authenticate(username, password)
                except auth.LoginError as error:
                    st.error(str(error))
                else:
                    token = db.create_token(user["username"], COOKIE_HOURS)
                    st.session_state["user"] = user
                    st.session_state["auth_token"] = token
                    st.session_state["set_cookie"] = token
                    st.rerun()
    with image_col:
        if LOGIN_IMAGE:
            with st.container(key="loginimage"):
                st.image(LOGIN_IMAGE, width="stretch")


def _open_session(session_id: str) -> None:
    st.session_state["sid"] = session_id
    st.query_params["sid"] = session_id
    if st.session_state.get("_page") != "employee":
        st.switch_page("views/employee.py")
    st.rerun()


def render_history_sidebar(current_sid: str | None) -> None:
    """סרגל הצד של העובד, כמו בצ'אט מוכר: שיחה חדשה, והיסטוריית השיחות - פתיחה, שינוי שם ומחיקה (עם אישור)."""
    import db
    owner = _owner_for_sessions()
    with st.sidebar:
        if st.button("שיחה חדשה", icon=":material/add:", key="new_chat_button", use_container_width=True):
            new_session()
            _open_session(st.session_state["sid"])
        if not owner:                              # התחברות כבויה (פיתוח מקומי): אין היסטוריה אישית
            return
        pending = st.session_state.get("confirm_delete")
        sessions = db.list_user_sessions(owner)
        st.caption("השיחות שלי")
        if pending:
            target = next((x for x in sessions if x["session_id"] == pending), None)
            if target is None:
                st.session_state.pop("confirm_delete", None)
            else:
                st.warning(f"למחוק את השיחה \"{target['title']}\"?")
                yes, no = st.columns(2)
                if yes.button("מחיקה", key="confirm_delete_yes", type="primary", use_container_width=True):
                    db.delete_session(pending, owner)
                    st.session_state.pop("confirm_delete", None)
                    if pending == current_sid:
                        new_session()
                    st.rerun()
                if no.button("ביטול", key="confirm_delete_no", use_container_width=True):
                    st.session_state.pop("confirm_delete", None)
                    st.rerun()
        if not sessions:
            st.caption("עוד אין שיחות קודמות.")
        with st.container(key="chat-list"):
            for item in sessions:
                session_id = item["session_id"]
                active = session_id == current_sid
                # שורה אחת: כפתור השיחה, ואיקון התפריט (⋯) מוטבע בתוכו (ממוקם ב-CSS מעל קצה הכפתור)
                with st.container(key=f"chatrow_{'a' if active else 'n'}_{session_id}"):
                    if st.button(item["title"], key=f"open_{session_id}", use_container_width=True,
                                 type="primary" if active else "secondary", help=item["preview"]):
                        _open_session(session_id)
                    with st.popover("", icon=":material/more_horiz:"):
                        new_title = st.text_input("שם השיחה", value=item["title"], key=f"title_{session_id}")
                        if st.button("שמירת שם", key=f"save_{session_id}", use_container_width=True):
                            db.rename_session(session_id, owner, new_title)
                            st.rerun()
                        if st.button("מחיקת השיחה", key=f"delete_{session_id}", icon=":material/delete:",
                                     use_container_width=True):
                            st.session_state["confirm_delete"] = session_id
                            st.rerun()


def render_sidebar_footer() -> None:
    """בתחתית סרגל הצד: איקון הגדרות (האזור האישי) ואיקון יציאה. נקרא פעם אחת ב-app.py; CSS מציב אותו למטה."""
    user = st.session_state.get("user") or {}
    if user.get("role") == "both":                  # פיתוח מקומי בלי התחברות: אין הגדרות או יציאה
        return
    with st.sidebar:
        with st.container(key="sidebar-footer"):
            settings, leave = st.columns(2)
            if settings.button("הגדרות", icon=":material/settings:", key="settings_button", help="הגדרות",
                               use_container_width=True):
                st.switch_page("views/account.py")
            if leave.button("יציאה", icon=":material/logout:", key="logout_icon", help="יציאה",
                            use_container_width=True):
                logout()


def logout() -> None:
    import db
    token = st.session_state.pop("auth_token", None)
    if token:
        db.delete_token(token)
    for key in ("user", "sid", "set_cookie", "chat_error"):
        st.session_state.pop(key, None)
    st.session_state["clear_cookie"] = True
    st.query_params.clear()
    st.rerun()


def emit_cookie_changes() -> None:
    """כותב / מוחק את עוגיית ההתחברות בדפדפן (פעם אחת אחרי כניסה או יציאה)."""
    token = st.session_state.pop("set_cookie", None)
    if token:
        st.html(_cookie_script(token, COOKIE_HOURS * 3600), unsafe_allow_javascript=True)
    elif st.session_state.pop("clear_cookie", None):
        st.html(_cookie_script("", 0), unsafe_allow_javascript=True)


def _money(value) -> str:
    return f"{value:,.0f} ₪"


# (כותרת שורה, מפתח, האם להציג תמיד)
TABLE_ROWS = [
    ("שכר ברוטו (בסיס + משתנים)", "gross_salary", True),
    ("שווי שימוש (מתווסף לבסיס המס)", "usage_value", False),
    ("אחזקת רכב (בוויתור, חייבת במס)", "maintenance", False),
    ("מס הכנסה", "income_tax", True),
    ("ביטוח לאומי ובריאות", "national_insurance_and_health", True),
    ("פנסיה (עובד)", "pension", True),
    ("קרן השתלמות (עובד)", "study_fund", True),
    ("השתתפות ברכב", "participation", False),
    ("חלק העובד בפער - מסלול ברוכת ילדים", "family_employee_share", False),
]


def kpis_html(result: dict) -> str:
    diff = result["diff"]
    tone = "pos" if diff > 0 else "neg" if diff < 0 else ""
    return ("<div class='kpis'>"
            f"<div class='kpi'><div class='kpi-label'>נטו היום</div><div class='kpi-value'>{_money(result['net_today'])}</div></div>"
            f"<div class='kpi'><div class='kpi-label'>נטו בחלופה</div><div class='kpi-value'>{_money(result['net_alternative'])}</div></div>"
            f"<div class='kpi diff {tone}'><div class='kpi-label'>הפרש בחודש</div><div class='kpi-value'>{diff:+,} ₪</div></div>"
            "</div>")


def comparison_table_html(result: dict) -> str:
    """טבלת ההשוואה היום מול החלופה. שורות שהן 0 בשני הצדדים ואינן חובה - מוסתרות."""
    today, alt = result["today"], result["alternative"]
    rows = ["<tr><th></th><th>היום</th><th>חלופה</th></tr>",
            f"<tr><td>רכב / מצב</td><td class='text'>{html.escape(str(today['label']))}</td>"
            f"<td class='text'>{html.escape(str(alt['label']))}</td></tr>"]
    for label, key, always in TABLE_ROWS:
        if always or today[key] or alt[key]:
            rows.append(f"<tr><td>{label}</td><td class='num'>{_money(today[key])}</td>"
                        f"<td class='num'>{_money(alt[key])}</td></tr>")
    if today["electric_refund_net"] or alt["electric_refund_net"]:
        def refund(side):
            if not side["electric_refund_net"]:
                return "-"
            return (f"{_money(side['electric_refund_net'])} נטו"
                    f"<br><span class='sub'>ברוטו {_money(side['electric_refund_gross'])}</span>")
        rows.append(f"<tr><td>החזר חשמל (מגולם)</td><td class='num'>{refund(today)}</td>"
                    f"<td class='num'>{refund(alt)}</td></tr>")
    rows.append(f"<tr class='total'><td>שכר נטו</td><td class='num'>{_money(result['net_today'])}</td>"
                f"<td class='num'>{_money(result['net_alternative'])}</td></tr>")
    return ("<table class='cmp-table'><colgroup><col class='c-label'><col class='c-val'><col class='c-val'></colgroup>"
            + "".join(rows) + "</table>")


CLOSE_PICKER = "__close__"       # הערך שמחזיר הבורר כשהעובד לוחץ "סגור"
WAIVER_PICK = "ויתור"            # זהה ל-WAIVER_CHOICE בכלי verify_budget_and_eligibility
PICKER_PER_ROW = 2               # כמה כפתורי רכב בשורה (העמודה צרה, והשמות ארוכים)


def render_car_picker(picker: dict, nonce: int) -> str | None:
    """בורר הרכבים שמעל תיבת השיחה. עד 5 רכבים - כפתורים; יותר מזה - טבלה שבלחיצה על שורה בוחרים.
    מחזיר את הבחירה: מזהה רכב (למשל "CAR-10"), "ויתור", CLOSE_PICKER, או None אם לא נלחץ כלום.
    nonce מתחלף אחרי כל בחירה, כדי שהטבלה תתחיל ללא שורה מסומנת ולא תפעיל את אותה בחירה שוב."""
    cars = picker["cars"]
    choice = None
    with st.container(key="car_picker"):
        head, close = st.columns([4, 1], vertical_alignment="center")
        head.markdown("**בחר/י רכב לבדיקה**")
        if picker["closable"] and close.button("סגור", key=f"picker_close_{nonce}"):
            choice = CLOSE_PICKER

        if not picker["as_table"]:
            for start in range(0, len(cars), PICKER_PER_ROW):
                columns = st.columns(PICKER_PER_ROW)
                for column, car in zip(columns, cars[start:start + PICKER_PER_ROW]):
                    # שם הדגם באנגלית עטוף כיחידה אחת (בידוד כיוון), אחרת הסוגריים והקבוצה מתבלבלים
                    if column.button(f"קבוצה {car['group']}: ⁦{car['commercial_name']}⁩",
                                     key=f"pick_{car['id']}_{nonce}", use_container_width=True,
                                     help=car["display_name"]):
                        choice = car["id"]
        else:
            # הסדר הפוך (הרכב בעמודה האחרונה) כי הטבלה של Streamlit תמיד משמאל לימין, והעברית נקראת מימין.
            # שלוש עמודות בלבד ברוחב קבוע, כדי שהטבלה תיכנס לעמודת השיחה בלי גלילה צדדית
            table = pd.DataFrame([{
                "שווי שימוש": _money(car["usage_value"]), "הנעה": car["drive"], "רכב": car["name"],
            } for car in cars])
            event = st.dataframe(
                table, hide_index=True, use_container_width=True, key=f"picker_table_{nonce}",
                on_select="rerun", selection_mode="single-row", height=min(36 * (len(cars) + 1) + 3, 235),
                column_config={"שווי שימוש": st.column_config.TextColumn(width=82),
                               "הנעה": st.column_config.TextColumn(width=58),
                               "רכב": st.column_config.TextColumn(width=210)})
            if event.selection.rows:
                choice = cars[event.selection.rows[0]]["id"]

        if picker["waiver_allowed"] and st.button("ויתור על הרכב (אחזקת רכב)", key=f"pick_waiver_{nonce}"):
            choice = WAIVER_PICK
    return choice


def empty_result_html() -> str:
    return ("<div class='empty-card'><div class='big'>📊</div><div><b>ההשוואה תופיע כאן</b></div>"
            "<div>אחרי שנאסוף בשיחה את הנתונים, תראו כאן את הנטו היום מול החלופה.</div></div>")


def comparison_rows(result: dict) -> list[list[str]]:
    """שורות טבלת ההשוואה כטקסט פשוט (להעתקה ולהורדה): [תיאור, היום, חלופה]. ללא תווי כיווניות."""
    today, alt = result["today"], result["alternative"]
    rows = [["", "היום", "חלופה"], ["רכב / מצב", str(today["label"]), str(alt["label"])]]
    for label, key, always in TABLE_ROWS:
        if always or today[key] or alt[key]:
            rows.append([label, _money(today[key]), _money(alt[key])])
    if today["electric_refund_net"] or alt["electric_refund_net"]:
        def refund(side):
            return (f"{_money(side['electric_refund_net'])} נטו (ברוטו {_money(side['electric_refund_gross'])})"
                    if side["electric_refund_net"] else "-")
        rows.append(["החזר חשמל (מגולם)", refund(today), refund(alt)])
    rows.append(["שכר נטו", _money(result["net_today"]), _money(result["net_alternative"])])
    return rows


def comparison_text(result: dict) -> str:
    """ההשוואה המלאה כטקסט להעתקה (למשל להדבקה במייל): הטבלה וההסבר."""
    diff = result["diff"]
    diff_text = (f"עלייה של {diff:,} ₪ בחודש" if diff > 0
                 else f"ירידה של {-diff:,} ₪ בחודש" if diff < 0 else "ללא שינוי")
    lines = [f"השוואה: {result['alternative']['label']}",
             f"נטו היום: {_money(result['net_today'])}", f"נטו בחלופה: {_money(result['net_alternative'])}",
             f"הפרש: {diff_text}", ""]
    lines += [" | ".join(row) for row in comparison_rows(result)]
    lines += ["", "למה זה משתנה?", result["explanation"], "",
              "ההערכה לפי כללי המדיניות ולוחות המס שבקובץ ההגדרות, ואינה תחליף לתלוש השכר."]
    return "\n".join(lines)


def comparison_csv(result: dict) -> bytes:
    """טבלת ההשוואה כקובץ CSV שנפתח באקסל בעברית (UTF-8 עם BOM)."""
    import csv
    import io
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerows(comparison_rows(result))
    return out.getvalue().encode("utf-8-sig")


def _copy_button_html(text: str) -> str:
    """כפתור "העתקה" שמעתיק את הטקסט ללוח (st.html עם JavaScript)."""
    payload = json.dumps(text).replace("</", "<\\/")
    return ("<button id='copy-cmp' class='cmp-btn'>העתקת ההשוואה</button>"
            "<script>(() => {const b = document.getElementById('copy-cmp'); if (!b) return;"
            f"const text = {payload};"
            "b.onclick = async () => { try { await navigator.clipboard.writeText(text); } catch (e) {"
            "const t = document.createElement('textarea'); t.value = text; document.body.appendChild(t);"
            "t.select(); document.execCommand('copy'); t.remove(); }"
            "b.textContent = 'הועתק ✓'; setTimeout(() => b.textContent = 'העתקת ההשוואה', 1500); };})();</script>")


def typing_status_html(label: str) -> str:
    """סטטוס המתנה עם אפקט כתיבה אות אחרי אות (אנימציית CSS; כל טקסט חדש מתחיל את האנימציה מחדש)."""
    return f"<span class='typing-status' style='--n:{max(len(label), 1)}'>{html.escape(label)}</span>"


def render_comparison(result: dict, stale: bool = False) -> None:
    """כרטיס ההשוואה המלא: מדדים, טבלה, זכאות והסבר."""
    alt = result["alternative"]
    if result["status"] == "provisional":
        st.warning("השוואת ביניים: ההשתתפות ברכב החדש ממתינה לאישור הנציג, והתוצאה תתעדכן כשתגיע תשובה.")
    if stale:
        st.info("הנתונים השתנו מאז החישוב האחרון - מומלץ לחשב מחדש.")

    st.markdown(kpis_html(result), unsafe_allow_html=True)
    st.markdown(comparison_table_html(result), unsafe_allow_html=True)

    details = []
    if alt.get("eligibility_reason"):
        details.append(f"זכאות: {alt['eligibility_reason']}")
    if alt.get("budget"):
        details.append(f"תקציב קבוצה: {_money(alt['budget'])}")
    if alt.get("monthly_cost"):
        details.append(f"עלות חודשית: {_money(alt['monthly_cost'])}")
    if alt.get("participation_effective_from"):
        details.append(f"השתתפות בתוקף מ-{alt['participation_effective_from']}")
    if details:
        st.markdown(f"<div class='cmp-note'>{html.escape(' · '.join(details))}</div>", unsafe_allow_html=True)
    st.markdown("**למה זה משתנה?**")
    show_markdown(result["explanation"].replace("\n", "  \n"))
    st.caption("ההערכה לפי כללי המדיניות ולוחות המס שבקובץ ההגדרות, ואינה תחליף לתלוש השכר.")

    copy_col, download_col = st.columns(2)
    with copy_col:
        st.html(_copy_button_html(comparison_text(result)), unsafe_allow_javascript=True)
    download_col.download_button("הורדה (CSV)", comparison_csv(result), file_name="השוואת-נטו.csv", mime="text/csv",
                                 use_container_width=True, key="download_comparison")
