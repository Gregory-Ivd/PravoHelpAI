from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from pravohelp.document.generator import render

TEMPLATE_NAMES = [
    "salary_claim_employer.docx",
    "salary_claim_labor_office.docx",
    "salary_court_claim.docx",
]


@pytest.fixture
def sample_context() -> dict:
    return {
        "today": "03.05.2026",
        "employer_name": "ТОВ «Промінь»",
        "employer_edrpou_or_dash": "12345678",
        "employer_address": "04050, м. Київ, вул. Січових Стрільців, 50",
        "amount_text": "15 000,00 грн",
        "period_from_text": "січня 2026 р.",
        "period_to_text": "квітня 2026 р.",
        "last_payment_date": "15.12.2025",
        "user_name": "Іваненко Олександр Сергійович",
        "user_tax_id": "1234567890",
        "user_address": "03150, м. Київ, вул. В. Васильківська, 100, кв. 25",
        "user_phone": "+380501234567",
        "court_name_or_placeholder": "Шевченківського районного суду м. Києва",
    }


@pytest.fixture(autouse=True)
def _ensure_templates_exist():
    from pravohelp.document.generator import TEMPLATES_DIR
    for name in TEMPLATE_NAMES:
        if not (TEMPLATES_DIR / name).exists():
            pytest.skip(
                f"Шаблон {name} відсутній. Запусти `python scripts/build_templates.py` спочатку."
            )


@pytest.mark.parametrize("template_name", TEMPLATE_NAMES)
def test_render_creates_docx(template_name, sample_context, tmp_path, monkeypatch):
    from pravohelp.document import generator

    monkeypatch.setattr(generator, "OUTPUT_DIR", tmp_path)

    output = render(template_name, sample_context, telegram_id=999999)

    assert output.exists()
    assert output.suffix == ".docx"
    assert output.stat().st_size > 1000  # хоч щось у файлі


def test_render_missing_template_raises(tmp_path, monkeypatch, sample_context):
    from pravohelp.document import generator

    monkeypatch.setattr(generator, "TEMPLATES_DIR", tmp_path)
    monkeypatch.setattr(generator, "OUTPUT_DIR", tmp_path / "out")

    with pytest.raises(FileNotFoundError, match="Шаблон не знайдено"):
        render("nonexistent.docx", sample_context, telegram_id=999999)


def test_rendered_content_contains_user_data(sample_context, tmp_path, monkeypatch):
    """Перевіряємо, що плейсхолдери реально замінились на наші значення."""
    from pravohelp.document import generator

    monkeypatch.setattr(generator, "OUTPUT_DIR", tmp_path)

    output = render("salary_claim_employer.docx", sample_context, telegram_id=111)

    from docx import Document

    doc = Document(str(output))
    full_text = "\n".join(p.text for p in doc.paragraphs)

    assert "ТОВ «Промінь»" in full_text
    assert "Іваненко Олександр Сергійович" in full_text
    assert "15 000,00 грн" in full_text
    assert "12345678" in full_text
    # Жоден плейсхолдер не залишився:
    assert "{{" not in full_text
    assert "}}" not in full_text
