"""מנוע חישוב הנטו - פונקציות טהורות ודטרמיניסטיות.

כל הפרמטרים (מדרגות מס, נקודת זיכוי, ביטוח לאומי, החזר חשמל) נקראים מ-rules_config.json.
אותם נתונים + אותם כללים = אותה תוצאה, תמיד. החישוב ללא עיגול; עיגול לשקל רק בתצוגה.
"""
import math

from pydantic import BaseModel, Field

from data_store import get_rules

MAX_CREDIT_POINTS = 100      # תקרת נקודות זיכוי שמתקבלת (0 הוא המינימום החוקי)
MAX_MONTHLY_AMOUNT = 1_000_000   # תקרת היגיון לסכום חודשי (שכר, עמלות, בונוס): מעבר לה - כנראה טעות הקלדה


class SalaryInput(BaseModel):
    """נתוני השכר של העובד. אחוזים מוזנים כמספר (6 = 6%)."""
    base_salary: float = Field(gt=0, le=MAX_MONTHLY_AMOUNT, description="שכר בסיס חודשי")
    commissions: float = Field(default=0, ge=0, le=MAX_MONTHLY_AMOUNT, description="עמלות")
    bonus: float = Field(default=0, ge=0, le=MAX_MONTHLY_AMOUNT, description="בונוס")
    pension_rate: float = Field(ge=0, le=100, description="אחוז הפרשת עובד לפנסיה")
    study_fund_rate: float = Field(default=0, ge=0, le=100, description="אחוז הפרשת עובד לקרן השתלמות")
    credit_points: float = Field(ge=0, le=MAX_CREDIT_POINTS, description="נקודות זיכוי")

    @property
    def gross(self) -> float:
        return self.base_salary + self.commissions + self.bonus


class CarSituation(BaseModel):
    """מצב רכב אחד (היום או חלופה): רכב, ויתור עם אחזקה, או מסלול משפחתי."""
    label: str
    usage_value: float = Field(default=0, ge=0, description="שווי שימוש חודשי")
    participation: float = Field(default=0, ge=0, description="השתתפות עובד חודשית (ניכוי נטו)")
    maintenance: float = Field(default=0, ge=0, description="אחזקת רכב בוויתור (חייבת במס, לא פנסיונית)")
    is_electric: bool = False
    family_employee_share: float = Field(default=0, ge=0, description="חלק העובד בפער המסלול המשפחתי (ניכוי נטו)")


def display_round(x: float) -> int:
    """עיגול לשקל שלם לתצוגה (חצי כלפי מעלה, לא עיגול בנקאי)."""
    return math.floor(x + 0.5)


def _bracket_tax(income: float, brackets: list) -> float:
    tax, lower = 0.0, 0.0
    for ceiling, rate in brackets:
        upper = math.inf if ceiling is None else ceiling
        if income > lower:
            tax += (min(income, upper) - lower) * rate
        if income <= upper:
            break
        lower = upper
    return tax


def _two_tier(income: float, cfg: dict) -> float:
    """ביטוח לאומי / מס בריאות: שיעור מופחת עד הסף, שיעור מלא עד התקרה."""
    base = min(income, cfg["ceiling"])
    low = min(base, cfg["threshold"])
    high = max(0.0, base - cfg["threshold"])
    return low * cfg["lowRate"] + high * cfg["highRate"]


def _net_breakdown(salary: SalaryInput, situation: CarSituation, electric_gross: float, rules: dict) -> dict:
    method = rules["netMethod"]
    waiver = rules["waiver"]
    gross = salary.gross

    # שווי שימוש חייב במס: אם ההשתתפות מקטינה אותו (reducesValueOfUse) מנכים אותה, ולא מתחת ל-0
    taxable_usage = situation.usage_value
    if method["participation"]["reducesValueOfUse"]:
        taxable_usage = max(0.0, taxable_usage - situation.participation)
    # אחזקת רכב נכנסת לבסיס המס רק אם maintenanceIsTaxable (היא תמיד מגיעה לעובד בכסף)
    taxable_maintenance = situation.maintenance if waiver["maintenanceIsTaxable"] else 0.0
    # בסיס המס והביטוח הלאומי: שכר + שווי שימוש חייב + אחזקה חייבת + החזר חשמל ברוטו
    taxable = gross + taxable_usage + taxable_maintenance + electric_gross

    # בסיס ההפרשות (פנסיה וקרן השתלמות): כל הברוטו או השכר הבסיסי בלבד (contributionsBase);
    # אחזקת רכב נכנסת אליו רק אם maintenanceIsPensionable
    contributions_base = {"gross": gross, "base_only": salary.base_salary}[method["contributionsBase"]]
    if waiver["maintenanceIsPensionable"]:
        contributions_base += situation.maintenance
    pension = contributions_base * salary.pension_rate / 100
    study_fund = contributions_base * salary.study_fund_rate / 100

    pc = method["pensionCredit"]
    pension_credit = pc["rate"] * min(pension, pc["maxContributionRate"] * pc["incomeCeiling"])
    credit_points_value = salary.credit_points * method["creditPointValue"]
    tax_before_credits = _bracket_tax(taxable, method["brackets"])
    income_tax = max(0.0, tax_before_credits - credit_points_value - pension_credit)

    national_insurance = _two_tier(taxable, method["nationalInsurance"])
    health = _two_tier(taxable, method["health"])

    # שווי שימוש אינו כסף בפועל - לכן לא נכנס לתשלום; אחזקה והחזר חשמל כן
    cash_income = gross + situation.maintenance + electric_gross
    net = (cash_income - income_tax - national_insurance - health - pension - study_fund
           - situation.participation - situation.family_employee_share)

    return {
        "label": situation.label,
        "gross_salary": gross,
        "usage_value": situation.usage_value,
        "maintenance": situation.maintenance,
        "electric_gross": electric_gross,
        "taxable_income": taxable,
        "tax_before_credits": tax_before_credits,
        "credit_points_value": credit_points_value,
        "pension_credit": pension_credit,
        "income_tax": income_tax,
        "national_insurance": national_insurance,
        "health": health,
        "social_total": national_insurance + health,
        "pension": pension,
        "study_fund": study_fund,
        "participation": situation.participation,
        "family_employee_share": situation.family_employee_share,
        "net": net,
    }


def _electric_gross(salary: SalaryInput, situation: CarSituation, rules: dict) -> float:
    """החזר החשמל ברוטו. בשיטת gross_up - הסכום שאחרי מס וביטוח לאומי משאיר לעובד בדיוק netBonus."""
    electric = rules["electric"]
    if not situation.is_electric:
        return 0.0
    if electric["method"] == "gross":
        return float(electric["netBonus"])

    target = electric["netBonus"]
    base_net = _net_breakdown(salary, situation, 0.0, rules)["net"]
    # הנטו עולה באופן מונוטוני עם הברוטו, לכן חיפוש בינארי מתכנס לפתרון יחיד
    low, high = float(target), float(target) * 10
    for _ in range(200):
        mid = (low + high) / 2
        if _net_breakdown(salary, situation, mid, rules)["net"] - base_net < target:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def calculate_net(salary: SalaryInput, situation: CarSituation, rules: dict | None = None) -> dict:
    """חישוב נטו מלא למצב אחד, עם פירוט כל הרכיבים (ללא עיגול)."""
    rules = rules or get_rules()
    electric_gross = _electric_gross(salary, situation, rules)
    result = _net_breakdown(salary, situation, electric_gross, rules)
    result["electric_net_bonus"] = rules["electric"]["netBonus"] if situation.is_electric else 0
    return result


def _fmt(x: float) -> str:
    return f"{display_round(x):,}"


def _amt(x: float) -> str:
    """סכום בשקלים. טקסט נקי - בידוד כיווניות לעברית מתווסף רק בתצוגה (frontend/ui.py: fix_bidi)."""
    return f"{_fmt(x)} ₪"


def describe_changes(today: dict, alt: dict, today_sit: CarSituation, alt_sit: CarSituation) -> list[str]:
    """רשימת השינויים בין המצבים, בניסוח קצר."""
    changes = []
    if today["usage_value"] and alt["usage_value"]:
        if today["usage_value"] != alt["usage_value"]:
            changes.append(f"שווי שימוש {_fmt(today['usage_value'])} → {_fmt(alt['usage_value'])} ₪")
    elif alt["usage_value"]:
        changes.append(f"נוסף שווי שימוש {_fmt(alt['usage_value'])} ₪")
    elif today["usage_value"]:
        changes.append(f"בוטל שווי שימוש {_fmt(today['usage_value'])} ₪")

    if today["maintenance"] != alt["maintenance"]:
        if alt["maintenance"] and not today["maintenance"]:
            changes.append(f"נוספה אחזקת רכב {_fmt(alt['maintenance'])} ₪ (חייבת במס)")
        elif today["maintenance"] and not alt["maintenance"]:
            changes.append(f"בוטלה אחזקת רכב {_fmt(today['maintenance'])} ₪")
        else:
            changes.append(f"אחזקת רכב {_fmt(today['maintenance'])} → {_fmt(alt['maintenance'])} ₪")

    if alt_sit.is_electric and not today_sit.is_electric:
        changes.append(f"+{_fmt(alt['electric_net_bonus'])} ₪ נטו החזר חשמל (מגולם)")
    elif today_sit.is_electric and not alt_sit.is_electric:
        changes.append(f"בוטל החזר חשמל {_fmt(today['electric_net_bonus'])} ₪ נטו")

    if today["participation"] != alt["participation"]:
        changes.append(f"השתתפות {_fmt(today['participation'])} → {_fmt(alt['participation'])} ₪")

    if today["family_employee_share"] != alt["family_employee_share"]:
        changes.append(f"חלק העובד בפער המסלול המשפחתי {_fmt(alt['family_employee_share'])} ₪")
    return changes


def explain(today: dict, alt: dict, diff: int) -> str:
    """הסבר בשתי שורות בשפה פשוטה."""
    if diff > 0:
        line1 = f"הנטו החודשי שלך צפוי לעלות ב-{_amt(diff)}, מ-{_amt(today['net'])} ל-{_amt(alt['net'])}."
    elif diff < 0:
        line1 = f"הנטו החודשי שלך צפוי לרדת ב-{_amt(-diff)}, מ-{_amt(today['net'])} ל-{_amt(alt['net'])}."
    else:
        line1 = f"הנטו החודשי שלך כמעט לא משתנה: {_amt(alt['net'])}."

    reasons = []
    uv_delta = alt["usage_value"] - today["usage_value"]
    if uv_delta:
        direction = "יורד" if uv_delta < 0 else "עולה"
        reasons.append(f"שווי השימוש (סכום שנוסף לשכר רק לצורך מס) {direction} ב-{_amt(abs(uv_delta))}, "
                       f"ולכן המס {'יורד' if uv_delta < 0 else 'עולה'}")
    maint_delta = alt["maintenance"] - today["maintenance"]
    if maint_delta:
        reasons.append(f"אחזקת הרכב {'מתווספת' if maint_delta > 0 else 'מתבטלת'} "
                       f"({_amt(abs(maint_delta))} ברוטו)")
    if alt["electric_net_bonus"] != today["electric_net_bonus"]:
        reasons.append(f"החזר החשמל {'מוסיף' if alt['electric_net_bonus'] else 'כבר לא מוסיף'} "
                       f"{_amt(max(alt['electric_net_bonus'], today['electric_net_bonus']))} נטו")
    part_delta = (alt["participation"] + alt["family_employee_share"]) - (
        today["participation"] + today["family_employee_share"])
    if part_delta:
        reasons.append(f"ההשתתפות שלך ברכב {'עולה' if part_delta > 0 else 'יורדת'} ב-{_amt(abs(part_delta))}")
    line2 = ("הסיבה: " + "; ".join(reasons) + ".") if reasons else "אין שינוי ברכיבי הרכב."
    return f"{line1}\n{line2}"


def compare(salary: SalaryInput, today_sit: CarSituation, alt_sit: CarSituation,
            rules: dict | None = None) -> dict:
    """השוואת נטו בין המצב היום לחלופה. ההפרש מחושב על הנטו המעוגל, כך שהטבלה תמיד מסתכמת."""
    rules = rules or get_rules()
    today = calculate_net(salary, today_sit, rules)
    alt = calculate_net(salary, alt_sit, rules)
    net_today, net_alt = display_round(today["net"]), display_round(alt["net"])
    diff = net_alt - net_today
    return {
        "today": today,
        "alternative": alt,
        "net_today": net_today,
        "net_alternative": net_alt,
        "diff": diff,
        "changes": describe_changes(today, alt, today_sit, alt_sit),
        "explanation": explain(today, alt, diff),
    }
