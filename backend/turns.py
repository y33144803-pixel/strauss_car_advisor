"""תור שיחה שרץ ברקע, בנפרד מריצת המסך של Streamlit.

לחיצה על כפתור או מתג בזמן שהבוט חושב מפעילה ריצה חדשה של המסך וקוטעת את הקודמת. אם התור רץ בתוך ריצת המסך, הוא נקטע
גם הוא (ההודעה נשמרה אבל אין תשובה). לכן התור רץ בשרשור משלו, והמסך רק עוקב אחריו: ריצה חדשה של המסך מתחברת לאותו
תור שכבר רץ ומציגה את הסטטוס שלו עד שיסתיים."""
import threading

_TURNS: dict[str, "Turn"] = {}
_LOCK = threading.Lock()


class Turn:
    def __init__(self) -> None:
        self.status: str | None = None     # הסטטוס האחרון שהסוכן דיווח ("מחשב..." וכו')
        self.steps = 0                      # כמה כלים הסתיימו (המסך מצייר מחדש את הנתונים בכל שינוי)
        self.error: BaseException | None = None
        self.done = False

    def on_status(self, label: str) -> None:
        self.status = label

    def on_step(self, _step=None) -> None:
        self.steps += 1


def start(session_id: str, work) -> Turn:
    """מפעיל את work(turn) ברקע. אם כבר רץ תור באותה שיחה - מחזיר אותו ולא מפעיל שוב."""
    with _LOCK:
        existing = _TURNS.get(session_id)
        if existing is not None and not existing.done:
            return existing
        turn = _TURNS[session_id] = Turn()

    def run() -> None:
        try:
            work(turn)
        except BaseException as e:          # השגיאה נמסרת למסך; השרשור לא נופל בשקט
            turn.error = e
        finally:
            turn.done = True

    threading.Thread(target=run, daemon=True, name=f"turn-{session_id}").start()
    return turn


def get(session_id: str) -> Turn | None:
    return _TURNS.get(session_id)


def finish(session_id: str) -> Turn | None:
    """מסיר את התור שהסתיים (אחרי שהמסך טיפל בתוצאה) ומחזיר אותו."""
    with _LOCK:
        turn = _TURNS.get(session_id)
        if turn is not None and turn.done:
            del _TURNS[session_id]
            return turn
    return None
