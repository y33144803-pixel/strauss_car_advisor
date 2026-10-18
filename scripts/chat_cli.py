"""שיחה עם הבוט בטרמינל - לבדיקה מהירה לפני שיש מסכים.

הרצה:  python scripts/chat_cli.py [session_id]
פקודות: /tools - הצג קריאות כלים בכל תור, /state - הצג את מצב השיחה, /rep - קלוט תשובות נציג, /exit - יציאה
"""
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

import db  # noqa: E402
from agent import chat, check_representative_updates, start_session, state_snapshot  # noqa: E402


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
    session_id = sys.argv[1] if len(sys.argv) > 1 else f"cli-{uuid.uuid4().hex[:8]}"
    print(f"[שיחה: {session_id}]")
    for m in start_session(session_id):
        print(f"{'בוט' if m['role'] == 'assistant' else 'עובד'}: {m['content']}\n")
    show_tools = False
    while True:
        text = input("עובד: ").strip()
        if not text:
            continue
        if text == "/exit":
            break
        if text == "/tools":
            show_tools = not show_tools
            print(f"[הצגת כלים: {'פעיל' if show_tools else 'כבוי'}]")
            continue
        if text == "/state":
            print(state_snapshot(session_id))
            continue
        if text == "/rep":
            print(check_representative_updates(session_id) or "[אין תשובות חדשות מהנציג]")
            continue
        result = chat(session_id, text)
        if show_tools:
            for call in result["tool_calls"]:
                print(f"  ⚙ {call['tool']}({json.dumps(call['args'], ensure_ascii=False)}) -> ok={call['output'].get('ok')}")
        print(f"\nבוט: {result['reply']}\n")
        if db.load_state(session_id).last_result and result["tool_calls"]:
            last = db.load_state(session_id).last_result
            print(f"  [טבלה: היום {last['net_today']:,} | חלופה {last['net_alternative']:,} | "
                  f"הפרש {last['diff']:+,} | {last['status']}]\n")


if __name__ == "__main__":
    main()
