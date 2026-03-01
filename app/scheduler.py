from __future__ import annotations

from datetime import date

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .database import SessionLocal
from .services import FinanceAgentService


def _job_run_daily_evaluation(service_factory):
    session = SessionLocal()
    try:
        service: FinanceAgentService = service_factory(session)
        service.evaluate_all_recommendations(trading_date=date.today())
    finally:
        session.close()


def create_scheduler(cron_expr: str, service_factory) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(
        _job_run_daily_evaluation,
        trigger=CronTrigger.from_crontab(cron_expr),
        kwargs={"service_factory": service_factory},
        id="daily_evaluation",
        replace_existing=True,
    )
    return scheduler
