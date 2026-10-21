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


def _pct(rate: float) -> str:
    return f"{rate * 100:g}%"


def breakdown(result: dict, rules: dict) -> list[dict]:
    """שורות הפירוק: {key, label, today, alt, rule, sign}. הכלל מנוסח בעברית, והערכים נלקחים מקובץ ההגדרות (rules_config.json).
    ערך None = לא נשמר בחישוב ישן (חישוב מחדש ימלא אותו)."""
    t, a = result["today"], result["alternative"]
    m = rules["netMethod"]
    waiver, electric = rules["waiver"], rules["electric"]
    ni, health, pc = m["nationalInsurance"], m["health"], m["pensionCredit"]
    base_text = "מהשכר הכולל (ברוטו)" if m["contributionsBase"] == "gross" else "מהשכר הבסיסי בלבד"

    def row(label, key, rule="", sign=""):
        return {"key": key, "label": label, "today": t.get(key), "alt": a.get(key), "rule": rule, "sign": sign}

    electric_rule = (f"החזר קבוע של {electric['netBonus']} ₪ נטו בחודש; הסכום ברוטו מחושב כך שאחרי מס וביטוח לאומי "
                     "יישארו לעובד בדיוק " + f"{electric['netBonus']} ₪" if electric["method"] == "gross_up"
                     else f"החזר חשמל של {electric['netBonus']} ₪ ברוטו בחודש")
    rows = [
        row("שכר ברוטו (בסיס + עמלות + בונוס)", "gross_salary", "נתוני העובד (מהשיחה או מהתלוש)"),
        row("שווי שימוש (נוסף לשכר לצורך מס, לא משולם בפועל)", "usage_value",
            f"מטבלת רשות המסים, שנת מס {rules['valueOfUse']['taxYear']}"),
        row("אחזקת רכב (בוויתור)", "maintenance",
            "אחזקת הרכב חייבת במס" if waiver["maintenanceIsTaxable"] else "אחזקת הרכב פטורה ממס"),
        row("החזר חשמל ברוטו", "electric_refund_gross", electric_rule),
        row("הכנסה חייבת במס (ברוטו + שווי שימוש + אחזקה + החזר חשמל)", "taxable_income", "סכום השורות שמעל"),
        row("מס לפי מדרגות", "tax_before_credits", "מדרגות המס: " + _brackets_text(m["brackets"])),
        row("הפחתת נקודות זיכוי", "credit_points_value", f"{m['creditPointValue']} ₪ לכל נקודת זיכוי", "−"),
        row("זיכוי על הפרשת פנסיה", "pension_credit",
            f"{_pct(pc['rate'])} מההפרשה לפנסיה, עד תקרה שנקבעה בכללים", "−"),
        row("מס הכנסה לתשלום", "income_tax", "מס לפי מדרגות, פחות נקודות הזיכוי והזיכוי על הפנסיה (לא פחות מ-0)", "−"),
        row("ביטוח לאומי", "national_insurance",
            f"{_pct(ni['lowRate'])} עד {ni['threshold']:,} ₪, ו-{_pct(ni['highRate'])} עד התקרה ({ni['ceiling']:,} ₪)", "−"),
        row("מס בריאות", "health",
            f"{_pct(health['lowRate'])} עד {health['threshold']:,} ₪, ו-{_pct(health['highRate'])} עד התקרה ({health['ceiling']:,} ₪)", "−"),
        row("פנסיה (עובד)", "pension", f"אחוז הפנסיה של העובד {base_text}", "−"),
        row("קרן השתלמות (עובד)", "study_fund", f"אחוז קרן ההשתלמות של העובד {base_text}", "−"),
        row("השתתפות ברכב", "participation",
            "השתתפות העובד, מנוכה מהנטו" if m["participation"]["treatment"] == "net_deduction"
            else "השתתפות העובד", "−"),
        row("חלק העובד בפער המסלול המשפחתי", "family_employee_share", "מסלול ברוכת ילדים: חלק העובד בפער העלות", "−"),
        row("נטו חודשי", "net",
            "ברוטו + אחזקה + החזר חשמל, פחות מס הכנסה, ביטוח לאומי, בריאות, פנסיה, קרן השתלמות והשתתפות"),
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
