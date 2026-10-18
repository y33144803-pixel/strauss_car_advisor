"""קובץ ההגדרות הוא מקור האמת: כל מתג בו משפיע על החישוב, וערך שאינו נתמך נעצר בהודעה ברורה."""
import copy

import pytest

from calc_engine import CarSituation, SalaryInput, calculate_net
from data_store import find_car, get_catalog, get_rules
from eligibility import check_car_choice, family_employee_share, is_family_car
from rules_validation import ConfigError, validate_catalog_against_rules, validate_rules


def rules_with(path: tuple, value) -> dict:
    """עותק של ההגדרות עם ערך אחד שונה (ההגדרות המקוריות לא משתנות)."""
    rules = copy.deepcopy(get_rules())
    node = rules
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return rules


SALARY = SalaryInput(base_salary=27000, commissions=3000, pension_rate=7, study_fund_rate=2.5, credit_points=2.75)
WAIVER = CarSituation(label="ויתור", maintenance=2000)
CAR = CarSituation(label="רכב", usage_value=4000, participation=300)


# ------------------------------------------------------------ המתגים משפיעים על החישוב

def test_maintenance_pensionable_adds_pension_and_study_fund_on_maintenance():
    base = calculate_net(SALARY, WAIVER)
    flipped = calculate_net(SALARY, WAIVER, rules_with(("waiver", "maintenanceIsPensionable"), True))
    # פנסיה 7% + קרן 2.5% על 2,000 ₪ אחזקה = 190 ₪ הפרשות נוספות
    assert flipped["pension"] - base["pension"] == pytest.approx(140)
    assert flipped["study_fund"] - base["study_fund"] == pytest.approx(50)
    assert base["net"] - flipped["net"] == pytest.approx(190)


def test_maintenance_not_taxable_lowers_tax_base_but_still_paid():
    base = calculate_net(SALARY, WAIVER)
    flipped = calculate_net(SALARY, WAIVER, rules_with(("waiver", "maintenanceIsTaxable"), False))
    assert base["taxable_income"] - flipped["taxable_income"] == 2000
    assert flipped["income_tax"] < base["income_tax"]
    assert flipped["net"] > base["net"]
    assert flipped["maintenance"] == 2000                        # האחזקה עדיין מגיעה לעובד בכסף


def test_participation_reduces_value_of_use_lowers_taxable_income():
    base = calculate_net(SALARY, CAR)
    flipped = calculate_net(SALARY, CAR, rules_with(("netMethod", "participation", "reducesValueOfUse"), True))
    assert base["taxable_income"] - flipped["taxable_income"] == 300
    assert flipped["net"] > base["net"]
    assert flipped["participation"] == 300                        # ההשתתפות עדיין יורדת מהנטו


def test_participation_bigger_than_value_of_use_never_goes_negative():
    big = CarSituation(label="x", usage_value=200, participation=900)
    flipped = calculate_net(SALARY, big, rules_with(("netMethod", "participation", "reducesValueOfUse"), True))
    assert flipped["taxable_income"] == SALARY.gross             # שווי שימוש חייב הופך ל-0, לא למינוס


def test_contributions_base_only_uses_base_salary_for_pension_and_study_fund():
    base = calculate_net(SALARY, WAIVER)
    flipped = calculate_net(SALARY, WAIVER, rules_with(("netMethod", "contributionsBase"), "base_only"))
    # 3,000 ₪ עמלות מחוץ לבסיס ההפרשות: 7% + 2.5% = 285 ₪ פחות הפרשות
    assert base["pension"] - flipped["pension"] == pytest.approx(210)
    assert base["study_fund"] - flipped["study_fund"] == pytest.approx(75)
    assert flipped["net"] - base["net"] == pytest.approx(285)


def test_default_config_still_matches_challenge_reference():
    """ההגדרות שבקובץ נותנות בדיוק את נטו הייחוס של האתגר (E-005: 14,244 -> 17,806)."""
    salary = SalaryInput(base_salary=27000, pension_rate=7, study_fund_rate=2.5, credit_points=2.75)
    assert round(calculate_net(salary, WAIVER)["net"]) == 17806


# ------------------------------------------------------------ רכבי המסלול המשפחתי מהקובץ

def test_family_cars_come_from_config():
    assert is_family_car(find_car("CAR-19")) and is_family_car(find_car("CAR-20"))
    assert not is_family_car(find_car("CAR-01"))
    only_one = rules_with(("familyTrack", "cars"), ["CAR-19"])
    assert is_family_car(find_car("CAR-19"), only_one) and not is_family_car(find_car("CAR-20"), only_one)
    assert family_employee_share(find_car("CAR-20"), "ב", only_one) == 0
    assert family_employee_share(find_car("CAR-19"), "ב", only_one) == 1150


def test_family_car_still_needs_children():
    assert check_car_choice("ב", find_car("CAR-19"), 3)["eligible"] is False
    assert check_car_choice("ב", find_car("CAR-19"), 4)["eligible"] is True


# ------------------------------------------------------------ בדיקת תקינות הקובץ

def test_real_config_is_valid():
    assert validate_rules(copy.deepcopy(get_rules()))
    validate_catalog_against_rules(get_rules(), get_catalog())


@pytest.mark.parametrize("path, value, fragment", [
    (("chooseFrom",), "any", "chooseFrom"),
    (("unusedBudget",), "paid_in_salary", "unusedBudget"),
    (("electric", "method"), "magic", "electric.method"),
    (("netMethod", "contributionsBase"), "bonus_only", "contributionsBase"),
    (("netMethod", "basis"), "cumulative", "netMethod.basis"),
    (("waiver", "maintenanceIsTaxable"), "yes", "maintenanceIsTaxable"),
    (("netMethod", "participation", "reducesValueOfUse"), 1, "reducesValueOfUse"),
    (("electric", "netBonus"), -250, "netBonus"),
    (("familyTrack", "gapSplitEmployee"), 1.5, "gapSplitEmployee"),
    (("familyTrack", "cars"), [], "familyTrack.cars"),
])
def test_unsupported_values_stop_with_clear_message(path, value, fragment):
    with pytest.raises(ConfigError) as error:
        validate_rules(rules_with(path, value))
    assert fragment in str(error.value)


def test_missing_key_is_reported():
    rules = copy.deepcopy(get_rules())
    del rules["waiver"]["maintenanceIsPensionable"]
    with pytest.raises(ConfigError, match="maintenanceIsPensionable"):
        validate_rules(rules)


def test_budgets_must_match_groups():
    rules = copy.deepcopy(get_rules())
    del rules["budgets"]["ד"]
    with pytest.raises(ConfigError, match="budgets"):
        validate_rules(rules)


def test_brackets_must_be_ascending_and_open_ended():
    rules = copy.deepcopy(get_rules())
    rules["netMethod"]["brackets"][0], rules["netMethod"]["brackets"][1] = (
        rules["netMethod"]["brackets"][1], rules["netMethod"]["brackets"][0])
    with pytest.raises(ConfigError, match="brackets"):
        validate_rules(rules)


def test_catalog_cross_check():
    with pytest.raises(ConfigError, match="CAR-99"):
        validate_catalog_against_rules(rules_with(("familyTrack", "cars"), ["CAR-19", "CAR-99"]), get_catalog())
    # רכב עם קבוצה לא מוכרת שאינו ברשימת המסלול המשפחתי
    with pytest.raises(ConfigError, match="CAR-20"):
        validate_catalog_against_rules(rules_with(("familyTrack", "cars"), ["CAR-19"]), get_catalog())
