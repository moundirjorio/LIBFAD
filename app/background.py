"""Planificateur de tâches en arrière-plan (APScheduler).

Exécute automatiquement le job quotidien (relances + facturation des abonnements)
pour tous les tenants, sans intervention manuelle.

Activable/désactivable via SCHEDULER_ENABLED dans .env. Horaire : SCHEDULER_HOUR/MINUTE.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.database import SessionLocal
from app.services.scheduler import run_daily_jobs_all_tenants

logger = logging.getLogger("libfad.scheduler")

_scheduler: AsyncIOScheduler | None = None


async def _daily_job() -> None:
    """Tâche planifiée : ouvre une session et lance le job pour tous les tenants."""
    try:
        async with SessionLocal() as db:
            stats = await run_daily_jobs_all_tenants(db)
        logger.info("Job quotidien exécuté : %s", stats)
    except Exception:  # pragma: no cover - on ne veut jamais tuer le scheduler
        logger.exception("Échec du job quotidien planifié")


def start_scheduler() -> None:
    """Démarre le planificateur (idempotent). Appelé au démarrage de l'app."""
    global _scheduler
    if not settings.scheduler_enabled:
        logger.info("Planificateur désactivé (SCHEDULER_ENABLED=false).")
        return
    if _scheduler is not None:
        return

    scheduler = AsyncIOScheduler(timezone=settings.default_timezone)
    scheduler.add_job(
        _daily_job,
        trigger=CronTrigger(hour=settings.scheduler_hour, minute=settings.scheduler_minute),
        id="daily_jobs",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "Planificateur démarré : job quotidien à %02d:%02d (%s).",
        settings.scheduler_hour, settings.scheduler_minute, settings.default_timezone,
    )


def shutdown_scheduler() -> None:
    """Arrête proprement le planificateur. Appelé à l'arrêt de l'app."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Planificateur arrêté.")
