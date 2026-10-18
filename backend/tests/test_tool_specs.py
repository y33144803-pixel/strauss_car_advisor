"""מוודא שקובץ התיאורים (tool_specs.json) תואם לפונקציות שב-agent_tools.py."""
import inspect

import pytest

from agent_tools_registry import BOUND_PARAM, build_tools, load_specs, tool_function

SPECS = load_specs()


def test_eight_tools_defined():
    assert len(SPECS) == 8
    assert len({s["name"] for s in SPECS}) == 8


@pytest.mark.parametrize("spec", SPECS, ids=[s["name"] for s in SPECS])
def test_spec_matches_function_signature(spec):
    fn = tool_function(spec["name"])
    sig = inspect.signature(fn)
    fn_params = [p for p in sig.parameters if p != BOUND_PARAM]
    assert list(sig.parameters)[0] == BOUND_PARAM
    assert set(spec["parameters"]["properties"]) == set(fn_params)
    fn_required = {p for p in fn_params if sig.parameters[p].default is inspect.Parameter.empty}
    assert set(spec["parameters"]["required"]) == fn_required


@pytest.mark.parametrize("spec", SPECS, ids=[s["name"] for s in SPECS])
def test_spec_has_descriptions(spec):
    assert spec["description"] and spec["returns"]
    for name, prop in spec["parameters"]["properties"].items():
        assert prop.get("description"), f"{spec['name']}.{name} ללא תיאור"


def test_llama_index_tools_use_spec_schema_and_hide_session():
    tools = build_tools("s-1")
    by_name = {t.metadata.name: t for t in tools}
    schema = by_name["verify_budget_and_eligibility"].metadata.to_openai_tool()["function"]
    assert "session_id" not in schema["parameters"]["properties"]
    assert schema["parameters"]["required"] == ["choice"]
    assert "מחזיר:" in schema["description"]


def test_gemini_receives_all_tools_with_full_description():
    from llama_index.llms.google_genai import GoogleGenAI
    # max_tokens + context_window מונעים פנייה לרשת לשליפת פרטי המודל
    llm = GoogleGenAI(model="gemini-3.8-flash", api_key="dummy", max_tokens=1000, context_window=10000)
    kwargs = llm._prepare_chat_with_tools(build_tools("s-1"), user_msg="hi")
    declarations = kwargs["tools"][0].function_declarations
    assert len(declarations) == 8
    verify = next(d for d in declarations if d.name == "verify_budget_and_eligibility")
    assert verify.description.startswith("בדיקת זכאות")       # התיאור עצמו לא נחתך
    assert "מחזיר:" in verify.description
    assert list(verify.parameters.properties) == ["choice"]
    assert load_specs()[4]["parameters"].get("additionalProperties") is None   # הקובץ לא השתנה


def test_system_prompt_instructs_working_only_through_tools():
    from agent import load_system_prompt
    prompt = load_system_prompt()
    assert "# עבודה עם הכלים" in prompt and "אך ורק דרך הכלים" in prompt
    assert "הסכמה של הפרמטרים" in prompt                           # הוראה מפורשת להתייחס לסכמה
    # הכלים המרכזיים בזרימה מוזכרים בפרומפט בשמם
    for name in ("update_employee_data", "find_current_car", "set_current_situation", "show_catalog",
                 "verify_budget_and_eligibility", "calculate_net_salary", "request_representative_info"):
        assert name in prompt, name
