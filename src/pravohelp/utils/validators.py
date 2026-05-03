from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation


class ValidationError(ValueError):
    """Користувацька помилка вводу — текст з повідомлення показуємо юзеру в чаті."""


_EDRPOU_RE = re.compile(r"^\d{8}$")
_TAX_ID_RE = re.compile(r"^\d{10}$")
_PHONE_RE = re.compile(r"^(?:\+?38)?0?\d{9}$")
_MONTH_YEAR_RE = re.compile(r"^(0[1-9]|1[0-2])[./\-](20\d{2})$")
_DATE_RE = re.compile(r"^(0[1-9]|[12]\d|3[01])[./\-](0[1-9]|1[0-2])[./\-](20\d{2})$")


def validate_text(value: str, *, min_len: int = 2, max_len: int = 500, label: str) -> str:
    value = value.strip()
    if len(value) < min_len:
        raise ValidationError(f"{label} занадто короткий (мінімум {min_len} символи).")
    if len(value) > max_len:
        raise ValidationError(f"{label} занадто довгий (максимум {max_len} символів).")
    return value


def validate_edrpou(value: str) -> str:
    raw_lower = value.strip().lower()
    if raw_lower in {"не знаю", "невідомо", "пропустити", "-", "—"}:
        return ""
    cleaned = value.strip().replace(" ", "")
    if not _EDRPOU_RE.match(cleaned):
        raise ValidationError("ЄДРПОУ — це рівно 8 цифр. Якщо не знаєш — напиши «не знаю».")
    return cleaned


def validate_tax_id(value: str) -> str:
    cleaned = value.strip().replace(" ", "")
    if not _TAX_ID_RE.match(cleaned):
        raise ValidationError("ІПН — це рівно 10 цифр (без пробілів і дефісів).")
    return cleaned


def validate_phone(value: str) -> str:
    cleaned = re.sub(r"[\s\-()]", "", value.strip())
    if not _PHONE_RE.match(cleaned):
        raise ValidationError("Телефон у форматі +380XXXXXXXXX або 0XXXXXXXXX.")
    if cleaned.startswith("+380"):
        return cleaned
    if cleaned.startswith("380"):
        return "+" + cleaned
    if cleaned.startswith("0"):
        return "+38" + cleaned
    return "+380" + cleaned


def validate_amount_uah(value: str) -> Decimal:
    cleaned = value.strip().replace(" ", "").replace(",", ".").replace("грн", "")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as e:
        raise ValidationError("Сума має бути числом, наприклад: 15000 або 15000.50") from e
    if amount <= 0:
        raise ValidationError("Сума має бути більшою за 0.")
    if amount > Decimal("100_000_000"):
        raise ValidationError("Сума виглядає завеликою. Перевір ще раз.")
    return amount.quantize(Decimal("0.01"))


def validate_month_year(value: str) -> tuple[int, int]:
    cleaned = value.strip()
    m = _MONTH_YEAR_RE.match(cleaned)
    if not m:
        raise ValidationError(
            "Формат: ММ.РРРР, наприклад 01.2026 (січень 2026)."
        )
    month, year = int(m.group(1)), int(m.group(2))
    return month, year


def validate_date(value: str) -> date:
    cleaned = value.strip()
    m = _DATE_RE.match(cleaned)
    if not m:
        raise ValidationError("Формат дати: ДД.ММ.РРРР, наприклад 15.04.2026.")
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return date(year, month, day)
    except ValueError as e:
        raise ValidationError("Така дата не існує. Перевір число і місяць.") from e


def format_amount_uah(amount: Decimal) -> str:
    integer_part, decimal_part = f"{amount:.2f}".split(".")
    integer_with_spaces = " ".join(
        [integer_part[max(i - 3, 0) : i] for i in range(len(integer_part), 0, -3)][::-1]
    )
    return f"{integer_with_spaces},{decimal_part} грн"


def format_date(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def format_month_year(month: int, year: int) -> str:
    months_uk = [
        "січня", "лютого", "березня", "квітня", "травня", "червня",
        "липня", "серпня", "вересня", "жовтня", "листопада", "грудня",
    ]
    return f"{months_uk[month - 1]} {year} р."


def now_date_str() -> str:
    return datetime.now().strftime("%d.%m.%Y")
