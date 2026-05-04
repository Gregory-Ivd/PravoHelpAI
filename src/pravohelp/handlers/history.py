"""Команда /history — показує користувачу його минулі сценарії без PII."""

from __future__ import annotations

from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from pravohelp.storage.db import get_session
from pravohelp.storage.models import ScenarioRequest, User

HISTORY_LIMIT = 10


SCENARIO_LABELS: dict[str, str] = {
    "salary": "💰 Невиплата зарплати",
    "summons": "🪖 Повістка / мобілізація",
    "fine": "🚗 Штраф ПДР",
}

PLAN_LABELS: dict[str, str] = {
    "employer": "Претензія роботодавцю",
    "labor_office": "Скарга до Держпраці",
    "court": "Позов до суду",
    "all": "Усі три документи",
}

STATUS_LABELS: dict[str, str] = {
    "completed": "✅ Завершено",
    "started": "▶️ Розпочато",
    "abandoned": "⏸️ Покинуто",
}


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return

    telegram_id = update.effective_user.id

    with get_session() as session:
        user = session.execute(
            select(User).where(User.telegram_id == telegram_id)
        ).scalar_one_or_none()
        if user is None:
            await update.message.reply_text(
                "У тебе ще немає історії. Натисни /menu щоб запустити перший сценарій."
            )
            return

        rows = session.execute(
            select(ScenarioRequest)
            .where(ScenarioRequest.user_id == user.id)
            .order_by(ScenarioRequest.started_at.desc())
            .limit(HISTORY_LIMIT)
        ).scalars().all()

    if not rows:
        await update.message.reply_text(
            "У тебе ще немає завершених сценаріїв. /menu — почати."
        )
        return

    lines = ["<b>📜 Твоя історія (останні запити):</b>\n"]
    for r in rows:
        scenario = SCENARIO_LABELS.get(r.scenario, r.scenario)
        status = STATUS_LABELS.get(r.status, r.status)
        date = r.started_at.strftime("%d.%m.%Y")
        line = f"• <b>{date}</b> — {scenario} — {status}"
        if r.plan_chosen:
            line += f"\n   План: <i>{PLAN_LABELS.get(r.plan_chosen, r.plan_chosen)}</i>"
        if r.documents_generated:
            line += f", документів: {r.documents_generated}"
        lines.append(line)

    lines.append(
        "\n<i>У БД зберігається лише факт запитів — без твоїх ПІБ, телефонів і адрес.</i>"
    )
    await update.message.reply_html("\n".join(lines))
