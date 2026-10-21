"""שקיפות החישוב ("איך חושב?"): פירוק הנטו שורה אחרי שורה, ולצד כל שורה הכלל שבקובץ ההגדרות (rules_config.json) שהפעיל אותה.

הפירוק נבנה מתוצאת החישוב עצמה (אותם מספרים שמנוע החישוב החזיר), ללא חישוב נוסף, ולכן הוא תמיד תואם את ההשוואה.
check() מוודאת שהשורות באמת מסתכמות לנטו (עד סטיית עיגול), כך שהפירוק הוא גם הוכחה פנימית לנכונות."""

ROUNDING_TOLERANCE = 5        # ₪: כל שורה מעוגלת לשקל בנפרד, ולכן הסכום יכול לסטות בכמה שקלים


def _brackets_text(brackets: list) -> str:
    parts, lower = [], 0
    for ceiling, rate in brackets:
        top = "ומעלה" if ceiling is None else f"עד {ceiling:,}"
        parts.append(f"{top}: {rate * 100:g}%")
        lower = ceiling or lower
    return " · ".join(parts)


def _rule(path: str, value) -> str:
    return f"rules_config.json › {path} = {value}"


def breakdown(result: dict, rules: dict) -> list[dict]:
    """שורות הפירוק: {label, today, alt, rule}. ערך None = לא רלוונטי / לא נשמר בחישוב ישן (חישוב מחדש ימלא אותו)."""
    t, a = result["today"], result["alternative"]
    m = rules["netMethod"]
    waiver, electric = rules["waiver"], rules["electric"]

    def row(label, key, rule="", sign=""):
        return {"key": key, "label": label, "today": t.get(key), "alt": a.get(key), "rule": rule, "sign": sign}

    rows = [
        row("שכר ברוטו (בסיס + עמלות + בונוס)", "gross_salary", "נתוני העובד (מהשיחה או מהתלוש)"),
        row("שווי שימוש (נוסף לשכר לצורך מס, לא משולם בפועל)", "usage_value",
            f"מטבלת רשות המסים, שנת מס {rules['valueOfUse']['taxYear']} (data/car_usage_value_2026.csv)"),
        row("אחזקת רכב (בוויתור)", "maintenance",
            _rule("waiver.maintenanceIsTaxable", waiver["maintenanceIsTaxable"]) + " (חייבת במס)"),
        row("החזר חשמל ברוטו (מגולם כך שנטו = סכום קבוע)", "electric_refund_gross",
            _rule("electric.netBonus", f"{electric['netBonus']} ₪ נטו, שיטה: {electric['method']}")),
        row("הכנסה חייבת במס (ברוטו + שווי שימוש + אחזקה + החזר חשמל)", "taxable_income", "סכום השורות שמעל"),
        row("מס לפי מדרגות", "tax_before_credits", _rule("netMethod.brackets", _brackets_text(m["brackets"]))),
        row("הפחתת נקודות זיכוי", "credit_points_value", _rule("netMethod.creditPointValue", f"{m['creditPointValue']} ₪ לנקודה"), "−"),
        row("זיכוי על הפרשת פנסיה", "pension_credit",
            _rule("netMethod.pensionCredit", f"{m['pensionCredit']['rate'] * 100:g}% מההפרשה, עד תקרה"), "−"),
        row("מס הכנסה לתשלום", "income_tax", "מס לפי מדרגות פחות הנקודות והזיכוי (לא פחות מ-0)", "−"),
        row("ביטוח לאומי", "national_insurance",
            _rule("netMethod.nationalInsurance", f"{m['nationalInsurance']['lowRate'] * 100:g}% עד {m['nationalInsurance']['threshold']:,}, "
                                                 f"{m['nationalInsurance']['highRate'] * 100:g}% עד {m['nationalInsurance']['ceiling']:,}"), "−"),
        row("מס בריאות", "health",
            _rule("netMethod.health", f"{m['health']['lowRate'] * 100:g}% עד {m['health']['threshold']:,}, "
                                      f"{m['health']['highRate'] * 100:g}% עד {m['health']['ceiling']:,}"), "−"),
        row("פנסיה (עובד)", "pension", _rule("netMethod.contributionsBase", m["contributionsBase"]) + " × אחוז הפנסיה של העובד", "−"),
        row("קרן השתלמות (עובד)", "study_fund", _rule("netMethod.contributionsBase", m["contributionsBase"]) + " × אחוז קרן ההשתלמות", "−"),
        row("השתתפות ברכב", "participation", _rule("netMethod.participation.treatment", m["participation"]["treatment"]), "−"),
        row("חלק העובד בפער המסלול המשפחתי", "family_employee_share", "מסלול ברוכת ילדים (rules_config.json › familyTrack)", "−"),
        row("נטו חודשי", "net", "ברוטו + אחזקה + החזר חשמל − מס הכנסה − ביטוח לאומי − בריאות − פנסיה − קרן השתלמות − השתתפות"),
    ]
    return rows


def check(result: dict) -> dict:
    """בודקת שהשורות מסתכמות לנטו, לכל צד. מחזירה {ok, today_gap, alt_gap}; None אם חסר פירוט (חישוב ישן)."""
    gaps = {}
    for name, side in (("today", result["today"]), ("alt", result["alternative"])):
        needed = ("income_tax", "national_insurance", "health", "pension", "study_fund")
        if any(side.get(k) is None for k in needed):
            return {"ok": None}
        expected = (side["gross_salary"] + side["maintenance"] + side["electric_refund_gross"] - side["income_tax"]
                    - side["national_insurance"] - side["health"] - side["pension"] - side["study_fund"]
                    - side["participation"] - side["family_employee_share"])
        gaps[name] = abs(expected - side["net"])
    return {"ok": all(g <= ROUNDING_TOLERANCE for g in gaps.values()), "today_gap": gaps["today"], "alt_gap": gaps["alt"]}
