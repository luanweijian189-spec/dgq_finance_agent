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


def _job_run_news_scan(service_factory, min_score: float, auto_promote: bool, auto_promote_min_score: float):
    session = SessionLocal()
    try:
        service: FinanceAgentService = service_factory(session)
        service.run_news_discovery_scan(
            trading_date=date.today(),
            min_score=min_score,
            auto_promote=auto_promote,
            auto_promote_min_score=auto_promote_min_score,
            limit=40,
        )
    finally:
        session.close()


def _job_run_intraday_refresh(
    service_factory,
    limit: int,
    min_change_percent: float,
    force_notify: bool,
):
    session = SessionLocal()
    try:
        service: FinanceAgentService = service_factory(session)
        service.run_intraday_refresh_cycle(
            limit=limit,
            min_change_percent=min_change_percent,
            force_notify=force_notify,
        )
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


def add_news_scan_job(
    scheduler: BackgroundScheduler,
    cron_expr: str,
    service_factory,
    min_score: float,
    auto_promote: bool,
    auto_promote_min_score: float,
) -> None:
    scheduler.add_job(
        _job_run_news_scan,
        trigger=CronTrigger.from_crontab(cron_expr),
        kwargs={
            "service_factory": service_factory,
            "min_score": min_score,
            "auto_promote": auto_promote,
            "auto_promote_min_score": auto_promote_min_score,
        },
        id="news_scan",
        replace_existing=True,
    )


def add_intraday_refresh_job(
    scheduler: BackgroundScheduler,
    cron_expr: str,
    service_factory,
    limit: int,
    min_change_percent: float,
    force_notify: bool,
) -> None:
    scheduler.add_job(
        _job_run_intraday_refresh,
        trigger=CronTrigger.from_crontab(cron_expr),
        kwargs={
            "service_factory": service_factory,
            "limit": max(int(limit), 1),
            "min_change_percent": float(min_change_percent),
            "force_notify": bool(force_notify),
        },
        id="intraday_refresh",
        replace_existing=True,
    )
