"""בדיקת עומס מקומית: כמה משתמשים בו-זמנית מול מסד הנתונים, עם מודל שפה מדומה (בלי Gemini ובלי מכסה).

בודקת: (1) שיחות של משתמשים שונים לא מתערבבות, (2) כתיבות מקבילות לאותה שיחה לא דורסות זו את זו,
(3) אין שגיאות מסד ופסקי זמן. SQLite: ברירת מחדל (קובץ זמני). Postgres: DATABASE_URL=postgresql://... (מסד בדיקות בלבד!).

הרצה:  python scripts/load_test.py [מספר_משתמשים]   (ברירת מחדל 20)
"""
import os
import sys
import tempfile
import threading
import time
import traceback

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))
if not os.environ.get("DATABASE_URL"):
    os.environ["STRAUSS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "load.db")

from llama_index.core.base.llms.types import ChatMessage, ChatResponse, MessageRole  # noqa: E402
from llama_index.core.llms.llm import ToolSelection  # noqa: E402

import agent  # noqa: E402
import agent_tools  # noqa: E402
import db  # noqa: E402


class ScriptedLLM:
    """מודל מדומה: כל צעד הוא רשימת קריאות כלים [(שם, פרמטרים)] או טקסט תשובה סופית. מדמה גם השהיה של מודל אמיתי."""

    def __init__(self, steps, delay=0.05):
        self.steps, self.delay = list(steps), delay

    def chat_with_tools(self, tools, chat_history, allow_parallel_tool_calls=False):
        time.sleep(self.delay)
        step = self.steps.pop(0)
        if isinstance(step, str):
            msg = ChatMessage(role=MessageRole.ASSISTANT, content=step)
            msg.additional_kwargs["_calls"] = []
        else:
            msg = ChatMessage(role=MessageRole.ASSISTANT, content="")
            msg.additional_kwargs["_calls"] = [ToolSelection(tool_id=f"c{i}", tool_name=n, tool_kwargs=k)
                                               for i, (n, k) in enumerate(step)]
        return ChatResponse(message=msg)

    def get_tool_calls_from_response(self, response, error_on_no_tool_call=False):
        return response.message.additional_kwargs["_calls"]


errors: list[str] = []


def one_user(index: int) -> None:
    """משתמש שמנהל שיחה של כמה תורות; השכר שלו ייחודי, ובסוף נבדק שהשיחה שלו שלמה ולא התערבבה בשל אחרים."""
    sid = f"load-{index:03d}"
    salary = 10000 + index * 137
    try:
        agent.chat(sid, f"קבוצה ג, שכר {salary}", llm=ScriptedLLM([
            [("update_employee_data", {"eligibility_group": "ג", "base_salary": salary})], "תודה, ומה העמלות?"]))
        agent.chat(sid, "עמלות 1000 בונוס 0", llm=ScriptedLLM([
            [("update_employee_data", {"commissions": 1000, "bonus": 0})], "תודה, ומה אחוז הפנסיה?"]))
        agent.chat(sid, "פנסיה 6 וקרן השתלמות 2.5", llm=ScriptedLLM([
            [("update_employee_data", {"pension_rate": 6, "study_fund_rate": 2.5})], "תודה!"]))
        state = db.load_state(sid)
        messages = db.load_messages(sid)
        users = [m["content"] for m in messages if m["role"] == "user"]
        if state.employee.base_salary != salary or state.employee.commissions != 1000 or state.employee.pension_rate != 6:
            errors.append(f"{sid}: נתוני המצב לא תואמים (שכר {state.employee.base_salary}, צפוי {salary})")
        if users != [f"קבוצה ג, שכר {salary}", "עמלות 1000 בונוס 0", "פנסיה 6 וקרן השתלמות 2.5"]:
            errors.append(f"{sid}: ההודעות התערבבו: {users}")
        if len(messages) != 7:           # פתיחה + 3 x (עובד + בוט)
            errors.append(f"{sid}: מספר הודעות {len(messages)} במקום 7")
    except Exception:
        errors.append(f"{sid}: חריגה\n{traceback.format_exc()}")


def same_session_race(rounds: int = 30) -> None:
    """שני תהליכים כותבים לאותה שיחה בו-זמנית שדות שונים: אחרי כל הסיבובים שני הערכים חייבים להישמר."""
    for i in range(rounds):
        sid = f"race-{i:03d}"
        db.save_state(db.load_state(sid))
        barrier = threading.Barrier(2)

        def writer(field: str, value: float) -> None:
            try:
                barrier.wait(5)
                agent_tools.update_employee_data(sid, **{field: value})
            except Exception:
                errors.append(f"{sid}: חריגה בכותב {field}\n{traceback.format_exc()}")

        threads = [threading.Thread(target=writer, args=("pension_rate", 6.0)),
                   threading.Thread(target=writer, args=("study_fund_rate", 2.5))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)
        emp = db.load_state(sid).employee
        if emp.pension_rate != 6.0 or emp.study_fund_rate != 2.5:
            errors.append(f"{sid}: עדכון אבד (פנסיה {emp.pension_rate}, קרן {emp.study_fund_rate})")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    users = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    print(f"מסד: {'Postgres' if db.using_postgres() else 'SQLite'}; {users} משתמשים בו-זמנית", flush=True)
    db.init_db()
    started = time.time()
    threads = [threading.Thread(target=one_user, args=(i,)) for i in range(users)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(180)
    alive = sum(t.is_alive() for t in threads)
    print(f"{users} משתמשים הסתיימו תוך {time.time() - started:.1f} שניות (תקועים: {alive})", flush=True)
    started = time.time()
    same_session_race()
    print(f"בדיקת כתיבה מקבילה לאותה שיחה (30 סיבובים): {time.time() - started:.1f} שניות", flush=True)
    if alive:
        errors.append(f"{alive} משתמשים לא סיימו בזמן")
    if errors:
        print(f"נכשל: {len(errors)} שגיאות")
        for e in errors[:10]:
            print(" -", e[:400])
        return 1
    print("עבר: אין שגיאות, השיחות לא התערבבו ושום עדכון לא אבד")
    return 0


if __name__ == "__main__":
    sys.exit(main())
