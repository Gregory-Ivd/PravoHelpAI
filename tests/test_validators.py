from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from pravohelp.utils.validators import (
    ValidationError,
    format_amount_uah,
    format_date,
    format_month_year,
    validate_amount_uah,
    validate_date,
    validate_edrpou,
    validate_month_year,
    validate_phone,
    validate_tax_id,
    validate_text,
)


class TestEdrpou:
    def test_valid(self):
        assert validate_edrpou("12345678") == "12345678"
        assert validate_edrpou(" 87654321 ") == "87654321"

    def test_invalid_length(self):
        with pytest.raises(ValidationError, match="8 цифр"):
            validate_edrpou("1234567")
        with pytest.raises(ValidationError):
            validate_edrpou("123456789")

    def test_non_digits(self):
        with pytest.raises(ValidationError):
            validate_edrpou("1234567a")

    def test_skip_keywords(self):
        assert validate_edrpou("не знаю") == ""
        assert validate_edrpou("невідомо") == ""
        assert validate_edrpou("ПРОПУСТИТИ") == ""


class TestTaxId:
    def test_valid(self):
        assert validate_tax_id("1234567890") == "1234567890"

    def test_invalid_length(self):
        with pytest.raises(ValidationError):
            validate_tax_id("123456789")
        with pytest.raises(ValidationError):
            validate_tax_id("12345678901")


class TestPhone:
    @pytest.mark.parametrize("raw,expected", [
        ("+380501234567", "+380501234567"),
        ("380501234567", "+380501234567"),
        ("0501234567", "+380501234567"),
        ("+38 050 123 45 67", "+380501234567"),
        ("(050) 123-45-67", "+380501234567"),
    ])
    def test_normalizes(self, raw, expected):
        assert validate_phone(raw) == expected

    def test_too_short(self):
        with pytest.raises(ValidationError):
            validate_phone("123")

    def test_garbage(self):
        with pytest.raises(ValidationError):
            validate_phone("not-a-phone")


class TestAmount:
    def test_integer(self):
        assert validate_amount_uah("15000") == Decimal("15000.00")

    def test_decimal_dot(self):
        assert validate_amount_uah("15000.50") == Decimal("15000.50")

    def test_decimal_comma(self):
        assert validate_amount_uah("15000,50") == Decimal("15000.50")

    def test_with_spaces_and_currency(self):
        assert validate_amount_uah("15 000 грн") == Decimal("15000.00")

    def test_zero_invalid(self):
        with pytest.raises(ValidationError):
            validate_amount_uah("0")

    def test_negative_invalid(self):
        with pytest.raises(ValidationError):
            validate_amount_uah("-100")

    def test_too_large(self):
        with pytest.raises(ValidationError):
            validate_amount_uah("999999999999")

    def test_garbage(self):
        with pytest.raises(ValidationError):
            validate_amount_uah("abc")


class TestMonthYear:
    def test_dot(self):
        assert validate_month_year("01.2026") == (1, 2026)

    def test_slash(self):
        assert validate_month_year("12/2025") == (12, 2025)

    def test_invalid_month(self):
        with pytest.raises(ValidationError):
            validate_month_year("13.2026")

    def test_old_year_format(self):
        with pytest.raises(ValidationError):
            validate_month_year("01.26")


class TestDate:
    def test_valid(self):
        assert validate_date("15.04.2026") == date(2026, 4, 15)
        assert validate_date("01/01/2025") == date(2025, 1, 1)

    def test_impossible(self):
        with pytest.raises(ValidationError):
            validate_date("31.02.2026")

    def test_garbage(self):
        with pytest.raises(ValidationError):
            validate_date("not a date")


class TestText:
    def test_trims(self):
        assert validate_text("  hi there  ", label="X") == "hi there"

    def test_too_short(self):
        with pytest.raises(ValidationError, match="короткий"):
            validate_text("a", label="X")

    def test_too_long(self):
        with pytest.raises(ValidationError, match="довгий"):
            validate_text("a" * 1000, label="X")


class TestFormatters:
    def test_format_amount(self):
        assert format_amount_uah(Decimal("15000.50")) == "15 000,50 грн"
        assert format_amount_uah(Decimal("100.00")) == "100,00 грн"
        assert format_amount_uah(Decimal("1234567.89")) == "1 234 567,89 грн"

    def test_format_date(self):
        assert format_date(date(2026, 4, 15)) == "15.04.2026"

    def test_format_month_year(self):
        assert format_month_year(1, 2026) == "січня 2026 р."
        assert format_month_year(12, 2025) == "грудня 2025 р."
