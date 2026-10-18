"""בדיקות מנוע החישוב מול נטו הייחוס של 6 העובדים לדוגמה (גיליונות 'עובדים לדוגמה' ו'חישובים')."""
import json
import os

import pytest
from pydantic import ValidationError

from calc_engine import CarSituation, SalaryInput, calculate_net, compare, display_round
from data_store import DATA_DIR, find_car, get_catalog
from eligibility import (car_situation, check_car_choice, check_waiver, lookup_catalog_usage_value,
                         waiver_situation)

with open(os.path.join(DATA_DIR, "sample_employees.json"), encoding="utf-8") as f:
    EMPLOYEES = {e["id"]: e for e in json.load(f)}

# המצב היום והחלופה של כל עובד: מזהה רכב מהקטלוג, או ('waiver', None) לוויתור.
# ההשתתפות בחלופה = ברירת המחדל בקטלוג.
SCENARIOS = {
    "E-001": ("CAR-09", "CAR-10"),        # קורולה קרוס -> ATTO 3 חשמלי
    "E-002": ("CAR-05", "CAR-19"),        # נירו -> קרניבל, מסלול ברוכת ילדים
    "E-003": ("waiver", "CAR-06"),        # אחזקה היום -> MG4 חשמלי
    "E-004": ("CAR-12", "CAR-11"),        # טוסון -> EV3
    "E-005": ("CAR-13", "waiver"),        # מאזדה 3 -> ויתור
    "E-006": ("CAR-15", "CAR-14"),        # אוקטביה -> טסלה מודל 3
}


def salary_of(emp: dict) -> SalaryInput:
    return SalaryInput(base_salary=emp["base_salary"], commissions=emp["commissions"], bonus=emp["bonus"],
                       pension_rate=emp["pension_rate"], study_fund_rate=emp["study_fund_rate"],
                       credit_points=emp["credit_points"])


def situation_of(emp: dict, choice: str, participation: float) -> CarSituation:
    if choice == "waiver":
        waiver = check_waiver(emp["occupation"])
        assert waiver["eligible"]
        return waiver_situation(waiver["maintenance"])
    return car_situation(find_car(choice), participation, emp["group"])


def run_scenario(emp_id: str) -> dict:
    emp = EMPLOYEES[emp_id]
    today_id, alt_id = SCENARIOS[emp_id]
    today = situation_of(emp, today_id, emp["current_participation"])
    alt_participation = find_car(alt_id)["default_participation"] if alt_id != "waiver" else 0
    alt = situation_of(emp, alt_id, alt_participation)
    return compare(salary_of(emp), today, alt)


@pytest.mark.parametrize("emp_id", list(SCENARIOS))
def test_net_matches_reference(emp_id):
    ref = EMPLOYEES[emp_id]["reference"]
    result = run_scenario(emp_id)
    assert result["net_today"] == ref["net_today"]
    assert result["net_alternative"] == ref["net_alternative"]
    assert result["diff"] == ref["diff"]


@pytest.mark.parametrize("emp_id", list(SCENARIOS))
def test_breakdown_matches_reference(emp_id):
    ref = EMPLOYEES[emp_id]["reference"]
    result = run_scenario(emp_id)
    today, alt = result["today"], result["alternative"]
    assert display_round(today["income_tax"]) == ref["tax_today"]
    assert display_round(alt["income_tax"]) == ref["tax_alternative"]
    assert display_round(today["social_total"]) == ref["social_today"]
    assert display_round(alt["social_total"]) == ref["social_alternative"]
    assert display_round(alt["electric_gross"]) == ref["electric_gross"]
    assert display_round(alt["family_employee_share"]) == ref["family_employee_share"]


@pytest.mark.parametrize("emp_id", list(SCENARIOS))
def test_eligibility_matches_reference(emp_id):
    emp = EMPLOYEES[emp_id]
    ref = emp["reference"]
    alt_id = SCENARIOS[emp_id][1]
    if alt_id == "waiver":
        check = check_waiver(emp["occupation"])
    else:
        check = check_car_choice(emp["group"], find_car(alt_id), emp["children_under_23"])
    assert check["eligible"] is ref["eligible"]
    assert check["reason"] == ref["reason"]


EXPECTED_CHANGES = {
    "E-001": ["שווי שימוש 4,070 → 3,130 ₪", "+250 ₪ נטו החזר חשמל (מגולם)"],
    "E-002": ["שווי שימוש 2,950 → 8,120 ₪", "השתתפות 250 → 500 ₪",
              "חלק העובד בפער המסלול המשפחתי 1,150 ₪"],
    "E-003": ["נוסף שווי שימוש 2,430 ₪", "בוטלה אחזקת רכב 1,500 ₪", "+250 ₪ נטו החזר חשמל (מגולם)",
              "השתתפות 0 → 250 ₪"],
    "E-004": ["שווי שימוש 4,790 → 2,930 ₪", "+250 ₪ נטו החזר חשמל (מגולם)", "השתתפות 400 → 300 ₪"],
    "E-005": ["בוטל שווי שימוש 4,570 ₪", "נוספה אחזקת רכב 2,000 ₪ (חייבת במס)", "השתתפות 350 → 0 ₪"],
    "E-006": ["שווי שימוש 5,150 → 4,150 ₪", "+250 ₪ נטו החזר חשמל (מגולם)"],
}


@pytest.mark.parametrize("emp_id", list(SCENARIOS))
def test_changes_match_reference_why(emp_id):
    # אותם שינויים כמו בעמודת 'למה' בגיליון החישובים
    assert run_scenario(emp_id)["changes"] == EXPECTED_CHANGES[emp_id]


def test_electric_refund_is_exactly_250_net():
    emp = EMPLOYEES["E-001"]
    salary = salary_of(emp)
    sit = car_situation(find_car("CAR-10"), 300, emp["group"])
    with_bonus = calculate_net(salary, sit)
    without_bonus = calculate_net(salary, sit.model_copy(update={"is_electric": False}))
    assert abs((with_bonus["net"] - without_bonus["net"]) - 250) < 1e-6


def test_simple_gross_method_alternative():
    from data_store import get_rules
    rules = json.loads(json.dumps(get_rules()))
    rules["electric"]["method"] = "gross"
    sit = car_situation(find_car("CAR-10"), 300, "ג")
    result = calculate_net(salary_of(EMPLOYEES["E-001"]), sit, rules)
    assert result["electric_gross"] == 250


def test_same_input_same_output():
    assert run_scenario("E-004") == run_scenario("E-004")


def test_explanation_is_two_lines():
    explanation = run_scenario("E-001")["explanation"]
    assert len(explanation.split("\n")) == 2
    assert "661" in explanation


def test_catalog_usage_values_match_official_db():
    for car in get_catalog():
        record = lookup_catalog_usage_value(car)
        assert record["found"] and record["usage_value"] == car["usage_value"], car["id"]


# --- כללי זכאות ---

def test_cannot_choose_higher_group():
    check = check_car_choice("ג", find_car("CAR-14"))  # טסלה - קבוצה ד
    assert check["eligible"] is False


def test_can_choose_lower_group():
    assert check_car_choice("ד", find_car("CAR-01"))["eligible"] is True


def test_family_car_requires_children():
    car = find_car("CAR-19")
    assert check_car_choice("ב", car, 3)["eligible"] is False
    assert check_car_choice("ב", car, 4)["eligible"] is True
    assert check_car_choice("ב", car, None)["eligible"] is None  # חסר מידע - לא מנחשים


def test_field_role_cannot_waive():
    assert check_waiver("נציג/ת מכירות שטח")["eligible"] is False


def test_unknown_occupation_is_missing_not_guessed():
    check = check_waiver("אסטרונאוט/ית")
    assert check["eligible"] is None and check["missing"] == "occupation"


def test_group_b_waiver():
    # מהטיפים: עובדת מקבוצה ב שרוצה לוותר על הרכב
    check = check_waiver("אנליסט/ית נתונים")
    assert check["eligible"] is True and check["maintenance"] == 1500


def test_invalid_group():
    assert check_car_choice("ה", find_car("CAR-01"))["eligible"] is False


# --- אימות קלט ---

@pytest.mark.parametrize("field, value", [
    ("base_salary", 0), ("base_salary", -100), ("pension_rate", 150),
    ("study_fund_rate", -1), ("credit_points", -2), ("credit_points", 101), ("commissions", -50),
])
def test_invalid_salary_input_rejected(field, value):
    data = dict(base_salary=10000, pension_rate=6, study_fund_rate=2.5, credit_points=2.25)
    data[field] = value
    with pytest.raises(ValidationError):
        SalaryInput(**data)


def test_negative_participation_rejected():
    with pytest.raises(ValidationError):
        CarSituation(label="x", participation=-10)


@pytest.mark.parametrize("points", [0, 2.25, 100])
def test_credit_points_range_boundaries_accepted(points):
    data = SalaryInput(base_salary=10000, pension_rate=6, study_fund_rate=2.5, credit_points=points)
    assert data.credit_points == points


def test_many_credit_points_never_produce_negative_tax():
    # 100 נקודות = 24,200 ₪ זיכוי בחודש - המס יורד ל-0 ולא נהיה שלילי
    salary = SalaryInput(base_salary=18000, pension_rate=6, study_fund_rate=2.5, credit_points=100)
    result = calculate_net(salary, CarSituation(label="x"))
    assert result["income_tax"] == 0 and result["net"] > 0
