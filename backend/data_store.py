"""טעינת קבצי ההגדרות והנתונים מתיקיית data (מקור האמת - לא הקוד)."""
import json
import os
import re
from functools import lru_cache

from rules_validation import validate_catalog_against_rules, validate_rules
from usage_value_db import name_matches

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")


def _read_json(name: str):
    with open(os.path.join(DATA_DIR, name), "r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def get_rules() -> dict:
    """קובץ ההגדרות, אחרי בדיקת תקינות: ערך שהקוד לא תומך בו עוצר בהודעה ברורה ולא מתעלמים ממנו בשקט."""
    return validate_rules(_read_json("rules_config.json"))


@lru_cache(maxsize=1)
def get_catalog() -> list[dict]:
    catalog = _read_json("catalog.json")
    validate_catalog_against_rules(get_rules(), catalog)
    return catalog


@lru_cache(maxsize=1)
def get_occupations() -> list[dict]:
    return _read_json("occupations.json")


def find_car(car_id: str) -> dict | None:
    return next((c for c in get_catalog() if c["id"] == car_id), None)


@lru_cache(maxsize=1)
def _aliases() -> dict:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "car_aliases.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def find_car_by_name(commercial_name: str) -> list[dict]:
    """רכבי קטלוג שהטקסט מתאים להם (יצרן + שם מסחרי, כל מילה, ללא תלות באותיות) - ראו name_matches."""
    def matches(car: dict) -> bool:
        aliases = " ".join(_aliases().get(car["id"], []))     # כינויים בעברית (למשל "קרניבל")
        return name_matches(commercial_name, car["make_name"], f"{car['commercial_name']} {aliases}")
    return [c for c in get_catalog() if matches(c)]


def _occupation_words(text: str) -> list[str]:
    """מילות העיסוק אחרי נרמול, בלי ה' הידיעה בתחילת מילה ("הרכש" = "רכש")."""
    text = re.sub(r"/(?:ית|ת|ה)(?![א-ת])", "", text)
    words = []
    for word in re.split(r"[\s\-]+", text):
        word = "".join(ch for ch in word if ch.isalnum())
        if len(word) > 3 and word.startswith("ה"):
            word = word[1:]
        if len(word) > 3 and word.endswith("ית"):
            word = word[:-2]
        elif len(word) > 3 and word.endswith("ת"):
            word = word[:-1]
        if len(word) > 3 and word.endswith("י"):
            word = word[:-1]
        if word:
            words.append(word)
    return words


def _normalize_occupation(text: str) -> str:
    """השוואה רפויה של עיסוקים: בלי "/ת", סיומות נקבה ("מפתחת" = "מפתח/ת"), ה' הידיעה, גרשיים ורווחים.
    אותו נרמול לשם מהטבלה ולטקסט שהוקלד, ולכן "מפתחת תוכנה" = "מפתח/ת תוכנה"."""
    return "".join(_occupation_words(text))


def find_occupation(name: str) -> dict | None:
    """עיסוק מהטבלה לפי השם המדויק; אחרת התאמה רפויה - רק אם היא חד-משמעית (עיסוק אחד בלבד)."""
    occupations = get_occupations()
    exact = next((o for o in occupations if o["occupation"] == name.strip()), None)
    if exact:
        return exact
    wanted = _normalize_occupation(name)
    if not wanted:
        return None
    matches = [o for o in occupations if wanted == _normalize_occupation(o["occupation"])
               or (len(wanted) >= 4 and wanted in _normalize_occupation(o["occupation"]))]
    if len(matches) == 1:
        return matches[0]
    if matches:
        return None
    # התאמה גמישה: כל מילות העיסוק שבטבלה מופיעות בטקסט ("רכזת הרכש וקניות"), או להפך - רק אם חד-משמעית
    typed = set(_occupation_words(name))
    loose = [o for o in occupations
             if len(_occupation_words(o["occupation"])) >= 2 and set(_occupation_words(o["occupation"])) <= typed]
    return loose[0] if len(loose) == 1 else None
