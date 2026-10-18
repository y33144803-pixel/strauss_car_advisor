"""בדיקות שליפת שווי שימוש מול ערכי הקטלוג באקסל האתגר."""
import pytest

from usage_value_db import get_usage_value, load_db, normalize_code, search_by_name

# (קוד תוצר, קוד דגם, שנת רישום, שווי שימוש לפי גיליון 'קטלוג רכבים', קוד הנעה צפוי)
CATALOG_CARS = [
    ("00885", "00643", 2025, 2690, "0"),  # PICANTO - בנזין
    ("00413", "00296", 2025, 4070, "1"),  # COROLLA CROSS - היברידי
    ("01014", "00003", 2025, 3130, "3"),  # ATTO 3 - חשמלי
    ("00443", "00032", 2025, 2430, "3"),  # MG4 - חשמלי
    ("00253", "00133", 2025, 4790, "0"),  # TUCSON
    ("00885", "00675", 2025, 2930, "3"),  # EV3
    ("00588", "00446", 2025, 4570, "0"),  # MAZDA 3
    ("01327", "00730", 2025, 4150, "3"),  # MODEL 3
    ("00676", "01455", 2025, 5150, "0"),  # OCTAVIA
    ("00416", "00294", 2026, 4550, "2"),  # SPORTAGE HEV - פלאג-אין
    ("00885", "00648", 2025, 8120, "0"),  # CARNIVAL
    ("00481", "00533", 2025, 4700, "0"),  # SANTA FE
]


def test_db_loaded_fully():
    assert len(load_db()) == 77988


@pytest.mark.parametrize("make, model, year, expected, drive", CATALOG_CARS)
def test_catalog_cars_match_official_db(make, model, year, expected, drive):
    result = get_usage_value(2026, year, 1, make, model)
    assert result["found"]
    assert result["usage_value"] == expected
    assert result["drive_code"] == drive


def test_codes_without_leading_zeros_are_accepted():
    assert normalize_code(885) == "00885"
    assert get_usage_value(2026, 2025, 1, 885, 643)["usage_value"] == 2690


def test_car_type_code_is_part_of_the_key():
    # אותו תוצר+דגם (01014/00003) קיים פעמיים: ATTO 3 בסוג 1 ו-TANG בסוג 2
    assert get_usage_value(2026, 2025, 1, "01014", "00003")["commercial_name"] == "ATTO 3"
    assert get_usage_value(2026, 2025, 2, "01014", "00003")["commercial_name"] == "TANG"


def test_missing_record_returns_error_not_guess():
    result = get_usage_value(2026, 2025, 1, "99999", "99999")
    assert result["found"] is False
    assert "לא נמצאה" in result["error"]


def test_search_by_name_returns_candidates():
    candidates = search_by_name("corolla cross", registration_year=2025, tax_year=2026)
    assert any(c["model_code"] == "00296" for c in candidates)
