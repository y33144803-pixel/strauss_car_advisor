"""יצירת תלושי שכר פיקטיביים (תמונה ו-PDF) לבדיקת העלאת תלוש. ברירת המחדל: העובד E-001 - בסיס 14,200, עמלות 1,000,
פנסיה 6%, קרן השתלמות 2.5%, 2.25 נקודות זיכוי. אין כאן אדם אמיתי.

הרצה:  python scripts/make_sample_payslip.py   (נוצר בתיקיית data/)
הפונקציה render_payslip(...) משמשת גם את scripts/eval_payslips.py ליצירת תלושים "לא ברורים" ומקרי קצה.
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
FONT = "C:/Windows/Fonts/arial.ttf"
FONT_BOLD = "C:/Windows/Fonts/arialbd.ttf"
W, H = 1240, 1650                      # A4 בקירוב
PURPLE, GRAY, LINE = (91, 33, 130), (90, 90, 100), (210, 205, 220)


def vis(text: str) -> str:
    """עברית ויזואלית (בלי raqm): הופכים את סדר התווים; מספרים נשארים בסדרם הנכון."""
    out, num = [], ""
    for ch in text:
        if ch.isdigit() or ch in ",.%-/:":
            num += ch
        else:
            if num:
                out.append(num[::-1]); num = ""
            out.append(ch)
    if num:
        out.append(num[::-1])
    return "".join(out)[::-1]


def money(value) -> str:
    return f"{value:,.2f}"


def render_payslip(job_title="מנהל אזור מכירות", base=14200, commissions=1000, bonus=0, pension_rate=6, study_rate=2.5,
                   credit_points=2.25, gross=None, pension_amount=None, study_amount=None, show_pension=True,
                   show_study=True, show_credit=True, show_gross=True, english=False, blur=0, rotate=0,
                   overlap_base=None, title_company="חברה פיקטיבית בע״מ") -> Image.Image:
    """תלוש שכר כתמונה. אפשר להשמיט שורות, לשבש סכומים, לטשטש, לסובב או לכתוב מספר מעל מספר (overlap_base)."""
    total = base + commissions + bonus
    gross = total if gross is None else gross
    pension_amount = round(total * pension_rate / 100, 2) if pension_amount is None else pension_amount
    study_amount = round(total * study_rate / 100, 2) if study_amount is None else study_amount
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    f = lambda size, bold=False: ImageFont.truetype(FONT_BOLD if bold else FONT, size)
    if english:                                    # תלוש באנגלית, מיושר לשמאל
        put = lambda y, text, font, fill="black": d.text((80, y), text, font=font, fill=fill, anchor="la")
        amount = lambda y, text, font, fill="black": d.text((W - 100, y), text, font=font, fill=fill, anchor="ra")
        t = {"title": "Payslip - Example Ltd.", "sub": "Fictional document", "emp": "Employee: Example Worker",
             "role": f"Position: {job_title}", "month": "Month: 09/2026", "pay": "Earnings", "ded": "Deductions",
             "base": "Base salary", "comm": "Sales commissions", "bonus": "Bonus", "gross": "Gross total",
             "pension": f"Pension - employee {pension_rate:g}%", "study": f"Study fund - employee {study_rate:g}%",
             "tax": "Income tax", "ni": "National insurance", "health": "Health insurance",
             "credit": f"Tax credit points: {credit_points:g}", "net": "Net pay"}
    else:
        put = lambda y, text, font, fill="black": d.text((W - 80, y), vis(text), font=font, fill=fill, anchor="ra")
        amount = lambda y, text, font, fill="black": d.text((100, y), text, font=font, fill=fill, anchor="la")
        t = {"title": f"תלוש שכר לדוגמה - {title_company}", "sub": "מסמך פיקטיבי לצורכי הדגמה בלבד",
             "emp": "שם העובד: עובד לדוגמה", "role": f"תפקיד: {job_title}", "month": "חודש שכר: 09/2026", "pay": "תשלומים",
             "ded": "ניכויים", "base": "שכר יסוד", "comm": "עמלות מכירה", "bonus": "בונוס", "gross": "סה״כ ברוטו",
             "pension": f"פנסיה - עובד {pension_rate:g}%", "study": f"קרן השתלמות - עובד {study_rate:g}%",
             "tax": "מס הכנסה", "ni": "ביטוח לאומי", "health": "ביטוח בריאות", "credit": f"נקודות זיכוי: {credit_points:g}",
             "net": "נטו לתשלום"}

    d.rectangle((0, 0, W, 150), fill=PURPLE)
    put(40, t["title"], f(44, True), "white")
    put(100, t["sub"], f(26), (230, 220, 240))
    y = 200
    for line in (t["emp"], t["role"], t["month"]):
        put(y, line, f(30)); y += 48
    y += 30

    def header(label):
        nonlocal y
        d.rectangle((60, y, W - 60, y + 56), fill=(244, 233, 251))
        put(y + 10, label, f(32, True), PURPLE)
        y += 56

    def row(label, value, bold=False):
        nonlocal y
        put(y + 12, label, f(30, bold)); amount(y + 12, value, f(30, bold))
        d.line((60, y + 58, W - 60, y + 58), fill=LINE)
        y += 58

    header(t["pay"])
    row(t["base"], money(base))
    if commissions:
        row(t["comm"], money(commissions))
    if bonus:
        row(t["bonus"], money(bonus))
    if show_gross:
        row(t["gross"], money(gross), True)
    y += 32
    header(t["ded"])
    row(t["tax"], "1,342.00"); row(t["ni"], "566.00"); row(t["health"], "398.00")
    if show_pension:
        row(t["pension"], money(pension_amount))
    if show_study:
        row(t["study"], money(study_amount))
    y += 36
    if show_credit:
        put(y, t["credit"], f(30)); y += 50
    put(y, "הפרשות מעסיק: פנסיה 6.5% · פיצויים 8.33% · קרן השתלמות 7.5%" if not english
        else "Employer contributions: pension 6.5% - severance 8.33% - study fund 7.5%", f(26), GRAY)
    y += 80
    d.rectangle((60, y, W - 60, y + 80), fill=PURPLE)
    put(y + 20, t["net"], f(36, True), "white"); amount(y + 20, "11,602.00", f(36, True), "white")

    if overlap_base is not None:                   # מספר שכתוב מעל השכר (כמו בקובץ שבדקה המשתמשת)
        d.text((100, 395), overlap_base, font=f(40), fill="black", anchor="la")
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    if rotate:
        img = img.rotate(rotate, expand=True, fillcolor="white")
    return img


def non_payslip() -> Image.Image:
    """מסמך שאינו תלוש (מתכון): הבוט לא אמור לקלוט ממנו נתוני שכר."""
    img = Image.new("RGB", (W, 900), "white")
    d = ImageDraw.Draw(img)
    f = lambda size, bold=False: ImageFont.truetype(FONT_BOLD if bold else FONT, size)
    d.text((80, 60), "Chocolate cake recipe", font=f(48, True), fill="black")
    for i, line in enumerate(("200 g flour", "150 g sugar", "100 g butter", "3 eggs", "Bake 35 minutes at 180 C")):
        d.text((80, 160 + i * 60), line, font=f(34), fill="black")
    return img


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    img = render_payslip()
    img.save(os.path.join(OUT_DIR, "sample_payslip_E001.png"))
    img.save(os.path.join(OUT_DIR, "sample_payslip_E001.pdf"), "PDF", resolution=150)
    print("נוצרו: sample_payslip_E001.png, sample_payslip_E001.pdf")


if __name__ == "__main__":
    sys.exit(main())
