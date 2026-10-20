"""בדיקות קריאת תלוש שכר: בדיקת קבצים, ניקוי ערכים, גיבוי בין מודלים ושמירה אחרי אישור (בלי קריאה אמיתית למודל)."""
import pytest

import db
import payslip
from payslip import PayslipError, apply_payslip, assess, check_file, clean_fields, extract_payslip

SID = "payslip-test"


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("STRAUSS_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-key-1")
    monkeypatch.setenv("GOOGLE_API_KEY_2", "fake-key-2")


def test_check_file_accepts_pdf_and_images_only():
    assert check_file("a.PDF", 100) == "application/pdf"
    assert check_file("a.jpeg", 100) == "image/jpeg"
    for bad in ("a.exe", "a.docx", "noext"):
        with pytest.raises(PayslipError):
            check_file(bad, 100)
    with pytest.raises(PayslipError):
        check_file("a.pdf", payslip.MAX_BYTES + 1)
    with pytest.raises(PayslipError):
        check_file("a.pdf", 0)


def test_clean_fields_drops_missing_and_unreasonable_values():
    cleaned = clean_fields({"base_salary": 14200, "commissions": None, "bonus": -5, "pension_rate": 600,
                            "study_fund_rate": 2.5, "credit_points": 2.25, "evil": 1, "extra": True})
    assert cleaned == {"base_salary": 14200.0, "study_fund_rate": 2.5, "credit_points": 2.25}
    assert clean_fields({"base_salary": "14200", "pension_rate": True}) == {}      # מחרוזת / בוליאני - לא ערך


def test_extract_falls_back_to_next_key_and_model():
    calls = []

    def flaky(model, key, data, mime):
        calls.append((model, key))
        if len(calls) < 3:
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return {"base_salary": 14200, "pension_rate": 6}

    assert extract_payslip(b"x", "image/png", generate=flaky).fields == {"base_salary": 14200.0, "pension_rate": 6.0}
    assert len(calls) == 3 and calls[0][1] != calls[1][1]          # קודם המפתח השני של אותו מודל, אחר כך המודל הבא


def test_extract_reports_friendly_error_when_everything_fails():
    def broken(model, key, data, mime):
        raise RuntimeError("503 UNAVAILABLE secret-detail")

    with pytest.raises(PayslipError) as e:
        extract_payslip(b"x", "image/png", generate=broken)
    assert "secret-detail" not in str(e.value) and "503" not in str(e.value)


def test_extract_without_keys(monkeypatch):
    import agent
    monkeypatch.setattr(agent, "_gemini_keys", lambda: [])
    with pytest.raises(PayslipError):
        extract_payslip(b"x", "image/png", generate=lambda *a: {})


def test_apply_saves_confirmed_values_and_ignores_garbage():
    outcome = apply_payslip(SID, {"base_salary": 14200, "commissions": 1000, "pension_rate": 6,
                                  "study_fund_rate": 2.5, "credit_points": 2.25, "pension_rate_x": 1})
    assert outcome["ok"]
    emp = db.load_state(SID).employee
    assert (emp.base_salary, emp.commissions, emp.pension_rate, emp.study_fund_rate, emp.credit_points) == \
           (14200.0, 1000.0, 6.0, 2.5, 2.25)
    assert emp.bonus is None                                       # מה שלא היה בתלוש - נשאר חסר (הבוט ישאל)


CLEAR = {"base_salary": 14200, "commissions": 1000, "pension_rate": 6, "study_fund_rate": 2.5, "credit_points": 2.25,
         "gross_total": 15200, "occupation": "מנהל/ת אזור מכירות", "unclear_fields": []}


def test_clear_payslip_is_certain_and_includes_occupation():
    result = assess(CLEAR)
    assert result.certain and not result.reasons
    assert result.fields["occupation"] == "מנהל/ת אזור מכירות" and result.fields["base_salary"] == 14200.0


def test_unrecognized_job_title_is_left_out_but_still_certain():
    result = assess({**CLEAR, "occupation": "קוסם/ת", "job_title": "קוסם"})
    assert result.certain and "occupation" not in result.fields             # הבוט ישאל על התפקיד


@pytest.mark.parametrize("change,reason", [
    ({"unclear_fields": ["pension_rate"]}, "לא היו ברורים"),
    ({"gross_total": 20000}, "לא מסתכמים"),
    ({"pension_rate": 600}, "לא הגיוניים"),
    ({"base_salary": None, "gross_total": None}, "שכר בסיס"),
])
def test_unclear_or_inconsistent_payslip_needs_confirmation(change, reason):
    result = assess({**CLEAR, **change})
    assert not result.certain and any(reason in r for r in result.reasons)


def test_apply_saves_occupation_and_derives_group():
    assert apply_payslip(SID, assess(CLEAR).fields)["ok"]
    emp = db.load_state(SID).employee
    assert emp.occupation == "מנהל/ת אזור מכירות" and emp.eligibility_group == "ג"


def test_poor_image_quality_needs_confirmation():
    for quality in ("poor", "fair", "POOR"):
        result = assess({**CLEAR, "image_quality": quality})
        assert not result.certain and any("איכות" in r for r in result.reasons)
    assert assess({**CLEAR, "image_quality": "good"}).certain and assess(CLEAR).certain


def test_unmatched_job_title_is_returned_for_the_chat_to_mention():
    result = assess({**CLEAR, "occupation": None, "job_title": "קוסם"})
    assert result.certain and result.job_title == "קוסם" and "occupation" not in result.fields
    assert assess(CLEAR).job_title is None                                    # עיסוק זוהה - אין צורך בשם הגולמי


def test_rate_must_match_the_amount_on_the_payslip():
    ok = assess({**CLEAR, "pension_amount": 912, "study_fund_amount": 380})
    assert ok.certain
    bad = assess({**CLEAR, "pension_rate": 16, "pension_amount": 912})        # 16% לא תואם 912 על 15,200
    assert not bad.certain and any("לא תואם" in r for r in bad.reasons)


def test_extract_gives_up_after_the_time_budget(monkeypatch):
    monkeypatch.setattr(payslip, "TOTAL_BUDGET_SECONDS", -1)
    calls = []
    with pytest.raises(PayslipError):
        extract_payslip(b"x", "image/png", generate=lambda *a: calls.append(a) or {})
    assert not calls                                               # השירות עמוס: לא ממשיכים לנסות בלי סוף


def test_similar_job_title_is_matched_to_the_closest_occupation():
    result = assess({**CLEAR, "occupation": None, "job_title": "מנהל מכירות"})
    assert result.fields["occupation"] == "מנהל/ת אזור מכירות" and result.occupation_from_title == "מנהל מכירות"
    assert result.job_title is None
    assert not result.certain and any("לפי דמיון" in r for r in result.reasons)      # עיסוק לפי דמיון תמיד לאישור העובד


def test_far_or_ambiguous_job_title_is_not_guessed():
    for title in ("מנקה", "טכנאי מזגן", "נהג"):
        result = assess({**CLEAR, "occupation": None, "job_title": title})
        assert "occupation" not in result.fields and result.job_title == title
