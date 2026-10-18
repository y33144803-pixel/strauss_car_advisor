"""בדיקת הסוכן מול המודל האמיתי: שיחה מלאה של העובדת E-001 (נועה לוי) מהאקסל, והשוואה לנטו הייחוס.

הרצה:  python scripts/eval_agent.py [--model gemini-3.5-flash-lite] [--no-fallback]
מודפסים: כל תור (כלים שהופעלו וזמן), ובסוף PASS/FAIL לכל בדיקה. משתמשת במסד נתונים זמני, לא נוגעת בשיחות אמיתיות.
"""
import argparse
import json
import os
import sys
import tempfile
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

TURNS = [
    "אני שוקלת לעבור לרכב חשמלי. כמה זה ישנה לי בנטו?",
    "קבוצה ג, 18,500 ₪",
    "אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי",
    "יש לי היום קורולה קרוס 2025, אני משתתפת ב-300 ₪",
    "כן, זה הרכב",
    "אני רוצה לבדוק את ATTO 3",
    "ההשתתפות שלי ברכב הזה היא 300 ₪",
]
# נטו הייחוס של E-001 מגיליון 'חישובים'
EXPECTED = {"net_today": 11032, "net_alternative": 11693, "diff": 661}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", help="מודל ראשי (ברירת מחדל: מה-.env)")
    parser.add_argument("--no-fallback", action="store_true", help="בלי מודלי גיבוי")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    os.environ["STRAUSS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "eval.db")
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE, ".env"))
    if args.model:
        os.environ["LLM_MODEL"] = args.model
    if args.no_fallback:
        os.environ["LLM_FALLBACK_MODELS"] = ""

    import db
    from agent import chat, model_names, start_session

    sid = "eval-e001"
    start_session(sid)
    print(f"מודלים: {model_names()}\n", flush=True)
    durations, failures, tools_used = [], [], []
    for text in TURNS:
        started = time.time()
        print("עובדת:", text, flush=True)
        try:
            result = chat(sid, text)
        except Exception as e:
            print("  שגיאה:", str(e)[:300], flush=True)
            failures.append(f"התור נכשל: {text}")
            break
        elapsed = time.time() - started
        durations.append(elapsed)
        for call in result["tool_calls"]:
            tools_used.append(call["tool"])
            print(f"  ⚙ {call['tool']} -> ok={call['output'].get('ok')}", flush=True)
        print(f"בוט ({elapsed:.1f}s): {result['reply'][:400]}\n", flush=True)

    state = db.load_state(sid)
    checks = {}
    last = state.last_result or {}
    checks["חושבה השוואה סופית"] = last.get("status") == "final"
    for key, value in EXPECTED.items():
        checks[f"{key} = {value}"] = last.get(key) == value
    checks["לא נפתחה שאלה לנציג (העובדת ידעה את ההשתתפות)"] = not db.list_questions(None)
    checks["הרכב הנוכחי נקבע מהקטלוג (CAR-09)"] = state.current.catalog_id == "CAR-09"
    checks["החלופה היא ATTO 3 (CAR-10)"] = state.target.catalog_id == "CAR-10"
    checks["לא הופעלו כלים לא קיימים"] = all(t in {
        "update_employee_data", "find_current_car", "set_current_situation", "show_catalog",
        "verify_budget_and_eligibility", "get_car_usage_value", "calculate_net_salary",
        "request_representative_info"} for t in tools_used)

    print("=" * 50)
    for name, ok in checks.items():
        print(("PASS" if ok else "FAIL"), "-", name)
    for f in failures:
        print("FAIL -", f)
    if durations:
        print(f"\nזמן ממוצע לתור: {sum(durations) / len(durations):.1f} שניות, מקסימום {max(durations):.1f}, "
              f"סה\"כ {sum(durations):.0f} שניות, {len(tools_used)} קריאות כלים")
    return 0 if all(checks.values()) and not failures else 1


if __name__ == "__main__":
    sys.exit(main())
