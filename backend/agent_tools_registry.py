"""חיבור בין פונקציות הכלים (agent_tools.py) לתיאורים שלהן (tool_specs.json) עבור LlamaIndex.

מזהה השיחה מוצמד לכל כלי כאן, כך שהמודל אינו רואה אותו ואינו יכול לפנות לשיחה אחרת.
"""
import copy
import json
import os
from functools import lru_cache

from pydantic import BaseModel

import agent_tools

SPECS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tool_specs.json")
BOUND_PARAM = "session_id"


@lru_cache(maxsize=1)
def load_specs() -> list[dict]:
    with open(SPECS_PATH, encoding="utf-8") as f:
        return json.load(f)["tools"]


def _guarded_update(session_id: str, **values):
    """update_employee_data כפי שהמודל מפעיל אותו: ערכים מספריים שהעובד לא כתב בשיחה נדחים (הם לא נשמרים)."""
    clean, errors = agent_tools.drop_unstated(session_id, values)
    result = agent_tools.update_employee_data(session_id, **clean)
    if errors:
        result["ok"] = False
        result["error"] = " | ".join(filter(None, [result.get("error"), *errors]))
        result["next_step"] = "שאל את העובד על הערכים שלא נשמרו; אל תנחש"
    return result


def tool_function(name: str):
    return getattr(agent_tools, name)


def model_function(name: str):
    """הפונקציה שהמודל מפעיל (כמו tool_function, אבל עדכון נתונים עובר את בדיקת "רק מה שהעובד כתב")."""
    return _guarded_update if name == "update_employee_data" else tool_function(name)


def agent_description(spec: dict) -> str:
    """התיאור שהמודל רואה. השורה הראשונה היא שם הכלי - זה הפורמט של LlamaIndex,
    ושילוב Gemini מדלג על השורה הראשונה ושולח רק את מה שאחריה."""
    return f"{spec['name']}\n{spec['description']}\nמחזיר: {spec['returns']}"


def schema_class(spec: dict) -> type[BaseModel]:
    """מחלקת Pydantic שהסכמה שלה היא בדיוק הסכמה מקובץ התיאורים.
    כך גם OpenAI וגם Gemini (שקוראים את fn_schema) מקבלים את אותה סכמה."""
    parameters = spec["parameters"]

    class SpecSchema(BaseModel):
        @classmethod
        def model_json_schema(cls, *args, **kwargs) -> dict:
            # עותק - ספריות הספקים משנות את המילון שהן מקבלות
            return copy.deepcopy(parameters)

    SpecSchema.__name__ = SpecSchema.__qualname__ = f"{spec['name']}_schema"
    return SpecSchema


def build_tools(session_id: str) -> list:
    """יצירת כלי LlamaIndex לשיחה מסוימת, עם הסכמה מתוך tool_specs.json."""
    from llama_index.core.tools import FunctionTool, ToolMetadata

    return [
        FunctionTool(
            fn=model_function(spec["name"]),
            metadata=ToolMetadata(name=spec["name"], description=agent_description(spec),
                                  fn_schema=schema_class(spec)),
            partial_params={BOUND_PARAM: session_id},
        )
        for spec in load_specs()
    ]
