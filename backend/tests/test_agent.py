"""בדיקות לולאת הסוכן עם מודל מדומה (בלי מפתח API): הפעלת כלים, שמירת הודעות, תמונת מצב ותשובות נציג."""
import pytest
from llama_index.core.base.llms.types import ChatMessage, ChatResponse, MessageRole
from llama_index.core.llms.llm import ToolSelection

import db
from agent import GREETING, chat, check_representative_updates, start_session, state_snapshot

SID = "agent-session"


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("STRAUSS_DB_PATH", str(tmp_path / "test.db"))
    import agent
    agent._COOLDOWN.clear()                  # הפסקות של מודלים שנכשלו לא יזלגו בין בדיקות


class ScriptedLLM:
    """מודל מדומה: כל צעד הוא רשימת קריאות כלים [(שם, פרמטרים)] או טקסט תשובה סופית."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.seen_system_prompts = []
        self.parallel_flags = []

    def chat_with_tools(self, tools, chat_history, allow_parallel_tool_calls=False):
        self.seen_system_prompts.append(chat_history[0].content)
        self.parallel_flags.append(allow_parallel_tool_calls)
        step = self.steps.pop(0)
        if isinstance(step, str):
            msg = ChatMessage(role=MessageRole.ASSISTANT, content=step)
            msg.additional_kwargs["_calls"] = []
        else:
            msg = ChatMessage(role=MessageRole.ASSISTANT, content="")
            msg.additional_kwargs["_calls"] = [
                ToolSelection(tool_id=f"call-{i}", tool_name=name, tool_kwargs=kwargs)
                for i, (name, kwargs) in enumerate(step)]
        return ChatResponse(message=msg)

    def get_tool_calls_from_response(self, response, error_on_no_tool_call=False):
        return response.message.additional_kwargs["_calls"]


def test_greeting_on_new_session():
    messages = start_session(SID)
    assert messages == [{"role": "assistant", "content": GREETING}]
    assert start_session(SID) == messages          # לא נוספת פעמיים


def test_tool_calls_update_state_and_reply_saved():
    llm = ScriptedLLM([
        [("update_employee_data", {"eligibility_group": "ג", "base_salary": 14200})],
        "תודה! יש רכיבי שכר נוספים?",
    ])
    result = chat(SID, "קבוצה ג, 14,200 ₪", llm=llm)
    assert result["reply"] == "תודה! יש רכיבי שכר נוספים?"
    assert result["tool_calls"][0]["output"]["ok"]
    assert db.load_state(SID).employee.base_salary == 14200
    # הסוכן רואה את המצב המעודכן בסבב שאחרי הכלי
    assert '"base_salary": 14200' in llm.seen_system_prompts[1]
    roles = [m["role"] for m in db.load_messages(SID)]
    assert roles == ["assistant", "user", "assistant"]


def test_tool_error_returned_to_model_not_crash():
    llm = ScriptedLLM([
        [("update_employee_data", {"pension_rate": 600})],
        "אחוז הפנסיה שהתקבל לא הגיוני - אפשר לבדוק?",
    ])
    result = chat(SID, "פנסיה 600", llm=llm)
    assert result["tool_calls"][0]["output"]["ok"] is False
    assert db.load_state(SID).employee.pension_rate is None


def test_unknown_tool_handled():
    llm = ScriptedLLM([[("make_coffee", {})], "סליחה"])
    result = chat(SID, "היי", llm=llm)
    assert result["tool_calls"][0]["output"]["ok"] is False


def test_tool_loop_is_bounded():
    llm = ScriptedLLM([[("show_catalog", {})]] * 20)
    result = chat(SID, "היי", llm=llm)
    assert "לא הצלחתי" in result["reply"]


def test_snapshot_lists_missing_fields():
    snap = state_snapshot(SID)
    assert "שכר בסיס" in snap and '"waiting_for_representative": false' in snap


def test_representative_answer_resumes_conversation():
    llm = ScriptedLLM([
        [("update_employee_data", {"eligibility_group": "ג", "base_salary": 14200, "commissions": 1000,
                                   "bonus": 0, "pension_rate": 6, "study_fund_rate": 2.5,
                                   "credit_points": 2.25})],
        [("find_current_car", {"commercial_name": "COROLLA CROSS", "registration_year": 2025})],
        [("set_current_situation", {"kind": "car", "candidate_number": 1})],
        [("update_employee_data", {"current_participation": 300})],
        [("verify_budget_and_eligibility", {"choice": "CAR-10"})],
        [("request_representative_info", {"missing_field": "new_participation",
                                          "context_message": "השתתפות ב-ATTO 3",
                                          "employee_confirmed_unknown": True})],
        "חסר לי סכום ההשתתפות שלך ברכב החדש - שלחתי שאלה לנציג התחבורה.",
    ])
    chat(SID, "כל הנתונים...", llm=llm)
    state = db.load_state(SID)
    assert state.waiting_for_representative and state.last_result["status"] == "provisional"

    assert check_representative_updates(SID) is None    # עוד אין תשובה
    q = db.list_questions("open")[0]
    db.answer_question(q["id"], 350, effective_from="1/1")
    message = check_representative_updates(SID)
    assert "350" in message and "בתוקף מ-1/1" in message and "הפרש" in message
    assert db.load_messages(SID)[-1]["source"] == "representative"
    assert db.load_state(SID).last_result["status"] == "final"


# ------------------------------------------------------------ הגנה מפני מספרים שהומצאו

def test_unsupported_amounts_detected():
    from agent import unsupported_amounts
    evidence = {14200.0, 3130.0, 661.0}
    assert unsupported_amounts("נטו 14,028 ₪", evidence) == ["14,028"]
    assert unsupported_amounts("השכר 14,200, שווי שימוש 3,130 ושנת 2025 ופנסיה 6%", evidence | {2025.0}) == []
    assert unsupported_amounts("ההפרש 662 ₪", evidence) == []        # סטייה של 1 (עיגול) מותרת


def test_invented_numbers_corrected_then_blocked():
    llm = ScriptedLLM(["הנטו שלך יעלה ל-14,358 ₪", "הנטו יעלה ל-14,358 ₪ בלי ספק"])
    result = chat(SID, "כמה אקבל?", llm=llm)
    assert "14,358" not in result["reply"]
    assert "לא אציג" in result["reply"]                              # תשובת הבטיחות
    assert llm.steps == []                                           # נעשה ניסיון תיקון אחד בלבד


def test_invented_numbers_recover_after_correction():
    llm = ScriptedLLM(["הנטו שלך יעלה ל-14,358 ₪", "אין לי עדיין חישוב - חסרים נתונים."])
    result = chat(SID, "כמה אקבל?", llm=llm)
    assert result["reply"] == "אין לי עדיין חישוב - חסרים נתונים."


def test_snapshot_includes_last_result():
    from agent_tools import update_employee_data
    state = db.load_state(SID)
    state.last_result = {"status": "final", "net_today": 11032, "net_alternative": 11693, "diff": 661,
                         "changes": [], "explanation": "x"}
    db.save_state(state)
    assert '"net_alternative": 11693' in state_snapshot(SID)


def test_tool_calls_are_sequential():
    llm = ScriptedLLM([[("show_catalog", {})], "תשובה"])
    chat(SID, "היי", llm=llm)
    assert llm.parallel_flags and not any(llm.parallel_flags)    # כלי אחד בכל סבב


# ------------------------------------------------------------ ניסיון חוזר, שגיאות ידידותיות, מודלי גיבוי

def test_pending_user_message_and_resume_without_duplicate():
    from agent import pending_user_message
    start_session(SID)
    messages = db.load_messages(SID)
    messages.append({"role": "user", "content": "שלום, שכר 15,000"})      # הודעה שנשמרה אך לא נענתה
    db.save_messages(SID, messages)
    assert pending_user_message(SID) == "שלום, שכר 15,000"

    llm = ScriptedLLM(["קיבלתי."])
    result = chat(SID, llm=llm)                                          # בלי טקסט: ממשיכים את ההודעה השמורה
    assert result["reply"] == "קיבלתי."
    saved = db.load_messages(SID)
    assert [m["role"] for m in saved].count("user") == 1                 # לא נשלחה פעמיים
    assert pending_user_message(SID) is None


def test_resume_without_pending_message_fails():
    start_session(SID)
    with pytest.raises(ValueError):
        chat(SID, llm=ScriptedLLM(["x"]))


def test_friendly_errors_hide_technical_details():
    from agent import LLMNotConfigured, LLMUnavailable, friendly_error
    unavailable = friendly_error(LLMUnavailable("WinError 10060 gemini-3.7-flash: 503"))
    assert "עמוס" in unavailable and "WinError" not in unavailable and "503" not in unavailable
    assert "מפתח" in friendly_error(LLMNotConfigured("חסר GOOGLE_API_KEY"))
    assert "בלתי צפויה" in friendly_error(ValueError("boom"))


def test_fallback_llm_raises_unavailable_when_all_models_fail(monkeypatch):
    import agent

    class Broken:
        def chat_with_tools(self, tools, **kwargs):
            raise ConnectionError("WinError 10060")

    monkeypatch.setattr(agent, "get_llm", lambda name: Broken())
    with pytest.raises(agent.LLMUnavailable):
        agent.FallbackLLM(["a", "b"]).chat_with_tools([])


def test_model_names_default_and_explicit_fallbacks(monkeypatch):
    import agent
    monkeypatch.setattr(agent, "load_dotenv", lambda *a, **k: None)      # לא לקרוא את ה-.env האמיתי
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_FALLBACK_MODELS", raising=False)
    names = agent.model_names()
    assert names[0] == agent.DEFAULT_MODELS["gemini"] and len(names) > 1  # ברירת מחדל כוללת גיבוי
    monkeypatch.setenv("LLM_FALLBACK_MODELS", "")
    assert agent.model_names() == [agent.DEFAULT_MODELS["gemini"]]        # הגדרה ריקה = בלי גיבוי
    monkeypatch.setenv("LLM_MODEL", "m1")
    monkeypatch.setenv("LLM_FALLBACK_MODELS", "m2, m1 ,m3")
    assert agent.model_names() == ["m1", "m2", "m3"]


def test_display_name_returned_by_tools():
    from agent_tools import find_current_car, set_current_situation, show_catalog, update_employee_data
    r = find_current_car(SID, "corolla cross", 2025)
    assert r["data"]["candidates"][0]["display_name"] == "טויוטה יפן COROLLA CROSS (2025)"
    update_employee_data(SID, eligibility_group="ג")
    set_current_situation(SID, "none")
    cars = show_catalog(SID)["data"]["cars"]
    assert all(c["display_name"].endswith(f"({c['registration_year']})") for c in cars)


def test_fallback_retries_same_model_on_malformed_response(monkeypatch):
    import agent
    calls = []

    class Flaky:
        def __init__(self, name):
            self.name = name

        def chat_with_tools(self, tools, **kwargs):
            calls.append(self.name)
            if self.name == "a" and calls.count("a") == 1:
                raise ValueError("Response was terminated early: MALFORMED_RESPONSE")
            return "ok"

    monkeypatch.setattr(agent, "get_llm", lambda name: Flaky(name))
    assert agent.FallbackLLM(["a", "b"]).chat_with_tools([]) == "ok"
    assert calls == ["a", "a"]                                           # ניסיון חוזר על אותו מודל, בלי לעבור ל-b


def test_fallback_moves_on_immediately_for_overload(monkeypatch):
    import agent
    calls = []

    class Overloaded:
        def __init__(self, name):
            self.name = name

        def chat_with_tools(self, tools, **kwargs):
            calls.append(self.name)
            if self.name == "a":
                raise ConnectionError("503 UNAVAILABLE")
            return "ok"

    monkeypatch.setattr(agent, "get_llm", lambda name: Overloaded(name))
    assert agent.FallbackLLM(["a", "b"]).chat_with_tools([]) == "ok"
    assert calls == ["a", "b"]                                           # עומס: מיד למודל הבא


def test_model_hitting_quota_is_skipped_for_a_while(monkeypatch):
    import agent
    calls = []

    class Model:
        def __init__(self, name):
            self.name = name

        def chat_with_tools(self, tools, **kwargs):
            calls.append(self.name)
            if self.name == "a":
                raise ConnectionError("429 RESOURCE_EXHAUSTED You exceeded your current quota")
            return "ok"

    monkeypatch.setattr(agent, "get_llm", lambda name: Model(name))
    llm = agent.FallbackLLM(["a", "b"])
    assert llm.chat_with_tools([]) == "ok" and calls == ["a", "b"]
    calls.clear()
    assert llm.chat_with_tools([]) == "ok" and calls == ["b"]            # "a" בהפסקה - לא מבזבזים עליו זמן

    # כשהזמן עבר - מנסים אותו שוב
    agent._COOLDOWN["a"] = 0
    calls.clear()
    llm.chat_with_tools([])
    assert calls[0] == "a"


def test_all_models_in_cooldown_are_still_tried(monkeypatch):
    import agent
    agent._COOLDOWN.update({"a": time_far_future(), "b": time_far_future()})
    monkeypatch.setattr(agent, "get_llm", lambda name: type("M", (), {"chat_with_tools": lambda s, t, **k: "ok"})())
    assert agent.FallbackLLM(["a", "b"]).chat_with_tools([]) == "ok"


def time_far_future():
    import time
    return time.time() + 10_000


def test_picker_context_note_reminds_the_agent_of_the_rejected_request():
    from agent import picker_context_note
    messages = [{"role": "assistant", "content": "שלום"},
                {"role": "user", "content": "אני רוצה לבדוק את קיה קרניבל"},
                {"role": "assistant", "content": "לא ניתן לבחור בקרניבל - אין ארבעה ילדים עד גיל 23"},
                {"role": "user", "content": "אני רוצה לבדוק את קיה קוריאה PICANTO (2025) (מזהה CAR-01)"}]
    note = picker_context_note(messages)
    assert note and "קרניבל" in note and "לחיצה בבורר" in note
    messages[-1]["content"] = "מה ההשתתפות?"                       # הודעה שהוקלדה - אין הערה
    assert picker_context_note(messages) is None
    assert picker_context_note(messages[:2]) is None                  # אין הקשר קודם


def test_tool_name_leaked_into_reply_is_removed(monkeypatch):
    import agent
    from llama_index.core.llms import ChatMessage, MessageRole

    class Leaky:
        def chat_with_tools(self, tools, **kwargs):
            class R:
                message = ChatMessage(role=MessageRole.ASSISTANT, content="אפשר לבחור בלחיצה: [show_catalog]")
            return R()

        def get_tool_calls_from_response(self, response, **kwargs):
            return []

    agent.start_session(SID)
    assert agent.chat(SID, "שלום", llm=Leaky())["reply"] == "אפשר לבחור בלחיצה:"


def test_snapshot_lists_known_fields_so_the_agent_does_not_ask_again():
    import json
    from agent import state_snapshot
    from agent_tools import update_employee_data
    update_employee_data(SID, occupation="מפתח/ת תוכנה")                  # הקבוצה נשלפת מהעיסוק
    known = json.loads(state_snapshot(SID))["already_known_do_not_ask_again"]
    assert "עיסוק" in known and "קבוצת זכאות" in known


def test_system_prompt_refuses_budget_cash_out_without_representative():
    from agent import load_system_prompt
    prompt = load_system_prompt()
    assert "מדיניות החברה אינה מאפשרת פדיון של תקציב רכב שלא נוצל" in prompt
    assert "ואל תפנה/י את הבקשה לנציג" in prompt


def test_status_labels_follow_what_the_agent_really_does():
    import agent
    from llama_index.core.llms import ChatMessage, MessageRole
    from llama_index.core.tools import ToolSelection

    class TwoStep:
        calls = 0

        def chat_with_tools(self, tools, **kwargs):
            TwoStep.calls += 1

            class R:
                message = ChatMessage(role=MessageRole.ASSISTANT, content="" if TwoStep.calls == 1 else "סיימתי")
            return R()

        def get_tool_calls_from_response(self, response, **kwargs):
            if TwoStep.calls == 1:
                return [ToolSelection(tool_id="1", tool_name="find_current_car",
                                      tool_kwargs={"commercial_name": "NIRO", "registration_year": 2025})]
            return []

    agent.start_session(SID)
    labels = []
    agent.chat(SID, "יש לי NIRO 2025", llm=TwoStep(), on_status=labels.append)
    assert labels == [agent.STATUS_ANALYZING, "מחפש את הרכב במאגר...", agent.STATUS_COMPOSING]
    assert "calculate_net_salary" in agent.TOOL_STATUS and "מחשב" in agent.TOOL_STATUS["calculate_net_salary"]


def test_representative_answer_without_full_data_says_what_is_missing():
    import agent
    from agent_tools import (find_current_car, request_representative_info, set_current_situation,
                             update_employee_data, verify_budget_and_eligibility)
    import db
    update_employee_data(SID, eligibility_group="ג", base_salary=18500)       # עוד חסרות עמלות, פנסיה וכו'
    find_current_car(SID, "corolla cross", 2025)
    set_current_situation(SID, "car", candidate_number=1, participation=300)
    verify_budget_and_eligibility(SID, "CAR-10")
    request_representative_info(SID, "new_participation", "בדיקה", True)
    q = db.list_questions("open")[-1]
    db.answer_question(q["id"], 50.0, None, None)
    message = agent.check_representative_updates(SID)
    assert "50" in message and "נמשיך מאותה נקודה" not in message
    assert "חסר לי עוד" in message and "עמלות" in message


def test_second_gemini_key_is_tried_when_first_key_quota_is_exhausted(monkeypatch):
    import agent
    agent._COOLDOWN.clear()
    used = []

    class Keyed:
        def __init__(self, label):
            self.label = label

        def chat_with_tools(self, tools, **kwargs):
            used.append(self.label)
            if self.label == "m1/key0":
                raise RuntimeError("429 RESOURCE_EXHAUSTED quota")
            return "ok"

    monkeypatch.setattr(agent, "get_llm", lambda name, key_index=0: Keyed(f"{name}/key{key_index}"))
    assert agent.FallbackLLM(["m1", "m2"], keys=2).chat_with_tools([]) == "ok"
    assert used == ["m1/key0", "m1/key1"]                 # אותו מודל עם המפתח השני, לפני המעבר למודל אחר
    used.clear()
    assert agent.FallbackLLM(["m1", "m2"], keys=2).chat_with_tools([]) == "ok"
    assert used == ["m1/key1"]                            # המפתח הראשון בהפסקה - לא מנסים אותו שוב מיד
    agent._COOLDOWN.clear()


def test_api_key_count_reads_all_configured_keys(monkeypatch):
    import agent
    monkeypatch.setattr(agent, "_provider", lambda: "gemini")
    for name, value in (("GOOGLE_API_KEY", "a"), ("GOOGLE_API_KEY_2", "b"), ("GOOGLE_API_KEY_3", "")):
        monkeypatch.setenv(name, value)
    assert agent.api_key_count() == 2
    monkeypatch.setenv("GOOGLE_API_KEY_2", "")
    assert agent.api_key_count() == 1
