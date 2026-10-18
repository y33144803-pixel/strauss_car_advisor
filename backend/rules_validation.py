"""בדיקת תקינות קובץ ההגדרות (rules_config.json).

העיקרון: כל ערך בקובץ חייב להיות משהו שהקוד באמת יודע לבצע. ערך שאינו נתמך (למשל chooseFrom: "any") לא יתעלמו
ממנו בשקט - המערכת תיעצר בהודעה ברורה שמציינת איפה הבעיה ומה הערכים הנתמכים.
"""


class ConfigError(ValueError):
    """הקובץ מכיל ערך חסר או ערך שהקוד אינו תומך בו."""


# נתיב בקובץ -> הערכים שהקוד יודע לבצע
SUPPORTED_VALUES = {
    ("chooseFrom",): ("own_group_or_lower",),
    ("unusedBudget",): ("not_paid",),
    ("electric", "method"): ("gross_up", "gross"),
    ("familyTrack", "priceSource"): ("catalogue",),
    ("waiver", "allowedBy"): ("occupation",),
    ("netMethod", "basis"): ("monthly",),
    ("netMethod", "contributionsBase"): ("gross", "base_only"),
    ("netMethod", "participation", "treatment"): ("net_deduction",),
    ("netMethod", "rounding", "during"): ("none",),
    ("netMethod", "rounding", "display"): ("shekel",),
}
BOOLEAN_FLAGS = [
    ("waiver", "maintenanceIsTaxable"),
    ("waiver", "maintenanceIsPensionable"),
    ("netMethod", "participation", "reducesValueOfUse"),
]
POSITIVE_NUMBERS = [
    ("electric", "netBonus"),
    ("netMethod", "creditPointValue"),
    ("familyTrack", "minChildren"),
    ("familyTrack", "childMaxAge"),
]


def _get(rules: dict, path: tuple):
    node = rules
    for key in path:
        if not isinstance(node, dict) or key not in node:
            raise ConfigError(f"חסר בקובץ ההגדרות: {'.'.join(path)}")
        node = node[key]
    return node


def validate_rules(rules: dict) -> dict:
    """מחזיר את אותו מילון אם הוא תקין, אחרת זורק ConfigError."""
    for path, supported in SUPPORTED_VALUES.items():
        value = _get(rules, path)
        if value not in supported:
            raise ConfigError(f"הערך {value!r} ב-{'.'.join(path)} אינו נתמך. ערכים נתמכים: "
                              f"{', '.join(supported)}")
    for path in BOOLEAN_FLAGS:
        if not isinstance(_get(rules, path), bool):
            raise ConfigError(f"{'.'.join(path)} חייב להיות true או false")
    for path in POSITIVE_NUMBERS:
        value = _get(rules, path)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ConfigError(f"{'.'.join(path)} חייב להיות מספר חיובי (נמצא: {value!r})")

    order = _get(rules, ("groupOrder",))
    budgets = _get(rules, ("budgets",))
    if not order or set(budgets) != set(order):
        raise ConfigError("budgets ו-groupOrder חייבים להכיל בדיוק את אותן קבוצות")
    if any(not isinstance(v, (int, float)) or v <= 0 for v in budgets.values()):
        raise ConfigError("כל תקציב בקבוצה חייב להיות מספר חיובי")

    split = _get(rules, ("familyTrack", "gapSplitEmployee"))
    if not isinstance(split, (int, float)) or not 0 <= split <= 1:
        raise ConfigError("familyTrack.gapSplitEmployee חייב להיות בין 0 ל-1")
    cars = _get(rules, ("familyTrack", "cars"))
    if not cars or not all(isinstance(c, str) for c in cars):
        raise ConfigError("familyTrack.cars חייב להיות רשימה לא ריקה של מזהי רכבים")

    brackets = _get(rules, ("netMethod", "brackets"))
    ceilings = [c for c, _ in brackets[:-1]]
    if (not brackets or brackets[-1][0] is not None or ceilings != sorted(ceilings)
            or any(c is None for c in ceilings)):
        raise ConfigError("netMethod.brackets חייבות להיות בסדר עולה, והמדרגה האחרונה בלי תקרה (null)")
    _get(rules, ("valueOfUse", "taxYear"))
    return rules


def validate_catalog_against_rules(rules: dict, catalog: list[dict]) -> None:
    """בדיקה צולבת: רכבי המסלול המשפחתי קיימים בקטלוג, וכל רכב אחר שייך לקבוצה מוכרת."""
    ids = {car["id"] for car in catalog}
    family = set(rules["familyTrack"]["cars"])
    missing = family - ids
    if missing:
        raise ConfigError(f"familyTrack.cars מכיל רכבים שאינם בקטלוג: {', '.join(sorted(missing))}")
    order = set(rules["groupOrder"])
    for car in catalog:
        if car["id"] not in family and car["group"] not in order:
            raise ConfigError(f"הרכב {car['id']} שייך לקבוצה '{car['group']}' שאינה מוגדרת, "
                              "והוא גם לא ברשימת familyTrack.cars")
