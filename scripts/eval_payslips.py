"""בדיקת קריאת תלושי שכר מול המודל האמיתי: תלושים ברורים, לא ברורים ומקרי קצה.
העיקרון הנבדק: אם הקריאה סומנה "ודאית" (נשמרת בלי אישור) - כל ערך שנשמר חייב להיות נכון. אחרת - כרטיס אישור.

הרצה:  python scripts/eval_payslips.py [שם_מקרה ...]
כל מקרה: תלוש שנוצר ב-render_payslip, קריאה אמיתית דרך payslip.extract_payslip, ובדיקות אוטומטיות. אין שמירה בשום מקום.
"""
import io
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))
sys.path.insert(0, os.path.join(BASE, "scripts"))

from make_sample_payslip import non_payslip, render_payslip  # noqa: E402

TRUTH = {"base_salary": 14200.0, "commissions": 1000.0, "pension_rate": 6.0, "study_fund_rate": 2.5, "credit_points": 2.25,
         "occupation": "מנהל/ת אזור מכירות"}
KEYS = set(TRUTH)


def all_correct(fields: dict, truth: dict) -> bool:
    """כל שדות האמת נקראו נכון (בונוס 0 כשאין שורת בונוס - מותר)."""
    return KEYS <= set(fields) and correct(fields, {**truth, "bonus": 0.0})


def correct(fields: dict, truth: dict) -> bool:
    """כל ערך שנקרא תואם לאמת (שדות שחסרים בקריאה - לא נספרים כטעות)."""
    for key, value in fields.items():
        expected = truth.get(key)
        if isinstance(value, str):
            if expected != value:
                return False
        elif expected is None or abs(float(value) - float(expected)) > 1e-6:
            return False
    return True


def png(image) -> tuple[bytes, str]:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue(), "image/png"


def pdf(image) -> tuple[bytes, str]:
    buffer = io.BytesIO()
    image.save(buffer, "PDF", resolution=150)
    return buffer.getvalue(), "application/pdf"


def build_cases():
    """(שם, (bytes, mime), אמת, בדיקות). בדיקה: (תיאור, פונקציה(result) -> bool)."""
    clear_truth = dict(TRUTH)
    def other(name, truth_overrides, **kwargs):
        """תלוש עם ערכים שונים מברירת המחדל: כל מה שנקרא חייב להתאים לערכים שנכתבו בו."""
        truth = {**TRUTH, "occupation": None, **truth_overrides}
        truth = {k: v for k, v in truth.items() if v is not None}
        expect = {k: v for k, v in truth.items() if k != "occupation" and v != 0}   # שורה בערך 0 לא מופיעה בתלוש
        return (name, png(render_payslip(**kwargs)), truth, [
            ("ודאי", lambda r: r.certain),
            ("הערכים נקראו נכון ואין ערכים מומצאים", lambda r, e=expect: all(r.fields.get(k) == v for k, v in e.items()) and all(e.get(k, 0) == v for k, v in r.fields.items() if isinstance(v, float)))])

    return [
        other("salary_9350", {"base_salary": 9350.0, "commissions": 0.0, "pension_rate": 5.5, "study_fund_rate": 0.0,
                              "credit_points": 3.0}, job_title="מזכירה", base=9350, commissions=0, pension_rate=5.5,
              study_rate=0, credit_points=3, show_study=False),
        other("salary_23800", {"base_salary": 23800.0, "commissions": 3200.0, "pension_rate": 6.0, "study_fund_rate": 2.5,
                               "credit_points": 2.75}, job_title="מנהל מחלקה", base=23800, commissions=3200,
              credit_points=2.75),
        other("salary_31475_50", {"base_salary": 31475.5, "commissions": 0.0, "pension_rate": 7.0, "study_fund_rate": 2.5,
                                  "credit_points": 2.25}, base=31475.5, commissions=0, pension_rate=7),
        other("salary_7120_english", {"base_salary": 7120.0, "commissions": 450.0, "pension_rate": 6.0,
                                      "study_fund_rate": 2.5, "credit_points": 2.25}, english=True, base=7120,
              commissions=450, job_title="Clerk"),
        ("clear_png", png(render_payslip()), clear_truth, [
            ("ודאי", lambda r: r.certain), ("הכול נכון", lambda r: all_correct(r.fields, clear_truth))]),
        ("clear_pdf", pdf(render_payslip()), clear_truth, [
            ("ודאי", lambda r: r.certain), ("הכול נכון", lambda r: all_correct(r.fields, clear_truth))]),
        ("english", png(render_payslip(english=True, job_title="Area Sales Manager")), clear_truth, [
            ("ודאי", lambda r: r.certain), ("שכר/פנסיה נכונים", lambda r: r.fields.get("base_salary") == 14200.0 and r.fields.get("pension_rate") == 6.0)]),
        ("bonus_line", png(render_payslip(bonus=2000)), {**clear_truth}, [
            ("ודאי", lambda r: r.certain), ("בונוס 2000 נקלט", lambda r: r.fields.get("bonus") == 2000.0)]),
        ("unknown_job", png(render_payslip(job_title="מנקה")), clear_truth, [
            ("ודאי", lambda r: r.certain), ("בלי עיסוק (לא בטבלה)", lambda r: "occupation" not in r.fields),
            ("התפקיד הגולמי נשמר להודעה", lambda r: r.job_title is not None and "מנק" in r.job_title)]),
        ("similar_job", png(render_payslip(job_title="מפתחת תוכנה")), {**clear_truth, "occupation": "מפתח/ת תוכנה"}, [
            ("עיסוק זוהה לפי דמיון", lambda r: r.fields.get("occupation") == "מפתח/ת תוכנה")]),
        ("pension_mismatch", png(render_payslip(pension_rate=16, pension_amount=912.0)), clear_truth, [
            ("לא ודאי, או שנבחר האחוז התואם לסכום שבתלוש (6)", lambda r: (not r.certain) or r.fields.get("pension_rate") == 6.0)]),
        ("gross_mismatch", png(render_payslip(gross=21000)), clear_truth, [
            ("לא ודאי (סכומים לא מסתכמים)", lambda r: not r.certain)]),
        ("no_pension_study_lines", png(render_payslip(show_pension=False, show_study=False)), clear_truth, [
            ("לא נלקחו אחוזי המעסיק כפנסיה/קה״ש של העובד",
             lambda r: "pension_rate" not in r.fields and "study_fund_rate" not in r.fields),
            ("השכר נקלט", lambda r: r.fields.get("base_salary") == 14200.0)]),
        ("no_credit_points", png(render_payslip(show_credit=False)), clear_truth, [
            ("נקודות זיכוי לא הומצאו", lambda r: "credit_points" not in r.fields)]),
        ("overlapped_base", png(render_payslip(overlap_base="1555555")), clear_truth, [
            ("1555555 לא נשמר כשכר", lambda r: r.fields.get("base_salary") != 1555555.0),
            ("אם ודאי - הערכים נכונים", lambda r: (not r.certain) or correct(r.fields, clear_truth))]),
        ("blur_light", png(render_payslip(blur=2)), clear_truth, [
            ("אם ודאי - הערכים נכונים", lambda r: (not r.certain) or correct(r.fields, clear_truth))]),
        ("blur_heavy", png(render_payslip(blur=7)), clear_truth, [
            ("אם ודאי - הערכים נכונים (אחרת כרטיס אישור)", lambda r: (not r.certain) or correct(r.fields, clear_truth))]),
        ("rotated_90", png(render_payslip(rotate=90)), clear_truth, [
            ("אם ודאי - הערכים נכונים", lambda r: (not r.certain) or correct(r.fields, clear_truth))]),
        ("absurd_values", png(render_payslip(base=5_000_000, commissions=0, pension_rate=6, study_rate=2.5)), clear_truth, [
            ("שכר של 5 מיליון לא נשמר אוטומטית", lambda r: not r.certain and "base_salary" not in r.fields)]),
        ("not_a_payslip", png(non_payslip()), clear_truth, [
            ("אין נתוני שכר", lambda r: "base_salary" not in r.fields),
            ("לא נשמר אוטומטית שכר מומצא", lambda r: not r.certain or not r.fields)]),
    ]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE, ".env"))
    from payslip import PayslipError, extract_payslip
    wanted = set(sys.argv[1:])
    passed = total = 0
    for name, (data, mime), _truth, checks in build_cases():
        if wanted and name not in wanted:
            continue
        print(f"\n=== {name} ===", flush=True)
        started = time.time()
        try:
            result = extract_payslip(data, mime)
        except PayslipError as e:
            print(f"  שגיאת שירות: {e}", flush=True)
            total += len(checks)
            continue
        print(f"  ({time.time() - started:.0f}s) ודאי={result.certain} סיבות={result.reasons} תפקיד גולמי={result.job_title}", flush=True)
        print(f"  שדות: {result.fields}", flush=True)
        for description, check in checks:
            try:
                ok = bool(check(result))
            except Exception as e:
                ok, description = False, f"{description} (חריגה: {e})"
            passed, total = passed + ok, total + 1
            print(f"  {'PASS' if ok else 'FAIL'}: {description}", flush=True)
    print(f"\nסה\"כ: {passed}/{total}", flush=True)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
