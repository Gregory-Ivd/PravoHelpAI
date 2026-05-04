"""Адмінські команди — доступ тільки для ADMIN_TELEGRAM_IDS з .env."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select
from telegram import Update
from telegram.ext import ContextTypes

from pravohelp.config import load_settings
from pravohelp.storage.db import get_session
from pravohelp.storage.models import ScenarioDraft, ScenarioRequest, User

log = structlog.get_logger(__name__)


def _is_admin(telegram_id: int) -> bool:
    return telegram_id in load_settings().admin_telegram_ids


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return

    if not _is_admin(update.effective_user.id):
        log.info("stats_denied", telegram_id=update.effective_user.id)
        return  # тиша для не-адмінів — навмисно

    now = datetime.now(UTC)
    day_ago = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)

    with get_session() as session:
        users_total = session.scalar(select(func.count(User.id))) or 0
        users_accepted = session.scalar(
            select(func.count(User.id)).where(User.disclaimer_accepted_at.is_not(None))
        ) or 0

        completed_total = session.scalar(select(func.count(ScenarioRequest.id))) or 0
        completed_24h = session.scalar(
            select(func.count(ScenarioRequest.id)).where(
                ScenarioRequest.completed_at >= day_ago
            )
        ) or 0
        completed_7d = session.scalar(
            select(func.count(ScenarioRequest.id)).where(
                ScenarioRequest.completed_at >= week_ago
            )
        ) or 0

        docs_total = session.scalar(
            select(func.coalesce(func.sum(ScenarioRequest.documents_generated), 0))
        ) or 0

        plan_rows = session.execute(
            select(ScenarioRequest.plan_chosen, func.count(ScenarioRequest.id))
            .group_by(ScenarioRequest.plan_chosen)
        ).all()
        plans = {row[0] or "—": row[1] for row in plan_rows}

        drafts_active = session.scalar(select(func.count(ScenarioDraft.id))) or 0

    plan_lines = "\n".join(
        f"  • {_plan_label(p)}: {n}" for p, n in sorted(plans.items(), key=lambda x: -x[1])
    ) or "  (поки нема)"

    await update.message.reply_html(
        "<b>📊 Статистика PravoHelpAI</b>\n\n"
        "<b>👥 Користувачі</b>\n"
        f"  • Всього: {users_total}\n"
        f"  • Прийняли умови: {users_accepted}\n\n"
        "<b>✅ Завершені сценарії</b>\n"
        f"  • Всього: {completed_total}\n"
        f"  • За 24 год: {completed_24h}\n"
        f"  • За 7 днів: {completed_7d}\n\n"
        "<b>📄 Згенеровано документів</b>\n"
        f"  • Всього: {docs_total}\n\n"
        "<b>🎯 Обрані плани</b>\n"
        f"{plan_lines}\n\n"
        "<b>⏳ Активних чернеток зараз</b>\n"
        f"  • {drafts_active}"
    )


def _plan_label(plan: str) -> str:
    return {
        "employer": "Претензія роботодавцю",
        "labor_office": "Скарга в Держпраці",
        "court": "Позов до суду",
        "all": "Усі три документи",
    }.get(plan, plan)
