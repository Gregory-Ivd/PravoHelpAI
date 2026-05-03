from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from docxtpl import DocxTemplate

from pravohelp.config import PROJECT_ROOT

log = structlog.get_logger(__name__)

TEMPLATES_DIR = PROJECT_ROOT / "templates"
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"


@dataclass(frozen=True)
class GeneratedDocument:
    template: str
    title: str
    path: Path


def render(template_name: str, context: dict[str, Any], *, telegram_id: int) -> Path:
    """Рендеримо шаблон у DOCX-файл, повертаємо шлях."""

    template_path = TEMPLATES_DIR / template_name
    if not template_path.exists():
        raise FileNotFoundError(
            f"Шаблон не знайдено: {template_path}. "
            f"Спочатку згенеруй шаблони через `python scripts/build_templates.py`."
        )

    user_dir = OUTPUT_DIR / str(telegram_id)
    user_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = user_dir / f"{template_path.stem}_{timestamp}.docx"

    doc = DocxTemplate(str(template_path))
    doc.render(context)
    doc.save(str(output_path))

    log.info(
        "document_generated",
        template=template_name,
        telegram_id=telegram_id,
        output=output_path.name,
    )
    return output_path
