from __future__ import annotations

from datetime import date, datetime
from threading import Lock, Thread
from time import perf_counter
from typing import Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .agent import OpenClawCommandHandler
from .analysis_agent import StockAnalysisAgent
from .config import get_settings
from .database import SessionLocal, get_db_session
from .decision_engine import LLMDecisionEngine
from .input_parser_agent import LLMInputParserAgent
from .llm_usage_store import LLMUsageStore
from .memory import MemoryRetriever
from .notifier import StdoutNotifier, WebhookNotifier
from .providers import ProviderError, build_intraday_provider, build_market_provider, build_news_provider
from .rag_store import ResearchNoteStore
from .stock_knowledge_store import StockKnowledgeStore
from .schemas import (
    AlertRequest,
    BulkImportTextRequest,
    BulkImportTextResponse,
    CommandRequest,
    CommandResponse,
    DailyEvaluationRequest,
    IngestMessageRequest,
    IntradayBatchSyncRequest,
    IntradayBatchSyncResponse,
    IntradayBarsResponse,
    IntradaySyncResponse,
    IntradayTradesResponse,
    ManualRecommendationRequest,
    NewsCandidateListResponse,
    NewsCandidatePromoteRequest,
    NewsScanRequest,
    NewsScanResponse,
    ResearchTextRequest,
)
from .scheduler import add_news_scan_job, create_scheduler
from .services import FinanceAgentService


templates = Jinja2Templates(directory="app/templates")


def _latest_trading_date(base_day: date) -> date:
    trading_day = base_day
    while trading_day.weekday() >= 5:
        trading_day = date.fromordinal(trading_day.toordinal() - 1)
    return trading_day


def _ensure_manual_refresh_state(app: FastAPI) -> dict:
    if not hasattr(app.state, "manual_refresh_lock"):
        app.state.manual_refresh_lock = Lock()
    if not hasattr(app.state, "manual_refresh_status"):
        app.state.manual_refresh_status = {
            "state": "idle",
            "message": "尚未执行手动刷新",
            "started_at": "",
            "finished_at": "",
            "trading_date": "",
            "duration_seconds": 0.0,
        }
    return app.state.manual_refresh_status


def _run_manual_refresh(service: FinanceAgentService, settings, trading_day: date) -> dict[str, str]:
    cleanup_result = service.cleanup_invalid_market_data()
    news_message = "新闻扫描未执行"
    try:
        scan_result = service.run_news_discovery_scan(
            trading_date=trading_day,
            min_score=settings.news_discovery_min_score,
            auto_promote=False,
            auto_promote_min_score=settings.news_auto_promote_min_score,
            limit=40,
        )
        news_message = (
            f"新闻扫描完成（按交易日 {trading_day.isoformat()}）：发现{scan_result['raw_discovered']}条，"
            f"保存候选{scan_result['saved_candidates']}条，"
            f"更新跟踪{scan_result['updated_tracking']}条。"
        )
    except Exception as exc:
        news_message = f"新闻扫描失败：{exc}"

    try:
        evaluated = service.evaluate_all_recommendations(trading_day)
        conclusion_updates = service.get_last_conclusion_updates()
        if conclusion_updates:
            preview = "；".join(conclusion_updates[:3])
            run_message = (
                f"已完成交易日 {trading_day.isoformat()} 评估 {evaluated} 只股票。"
                f"结论更新 {len(conclusion_updates)} 条：{preview}"
            )
        else:
            run_message = f"已完成交易日 {trading_day.isoformat()} 评估 {evaluated} 只股票。本轮无显著结论变更。"
    except Exception as exc:
        run_message = f"当日评估失败：{exc}"

    return {
        "message": (
            f"已清理无效周末记录：日评估{cleanup_result['deleted_daily']}条，"
            f"预测{cleanup_result['deleted_predictions']}条。"
            f" {news_message} {run_message}"
        ),
        "trading_date": trading_day.isoformat(),
    }


def _run_manual_refresh_in_background(app: FastAPI) -> None:
    settings = get_settings()
    status = _ensure_manual_refresh_state(app)
    lock = app.state.manual_refresh_lock

    with lock:
        status.update(
            {
                "state": "running",
                "message": "后台刷新进行中：正在执行新闻扫描、行情评估与日报刷新，请稍后手动刷新页面查看结果。",
                "started_at": datetime.utcnow().isoformat(),
                "finished_at": "",
                "trading_date": _latest_trading_date(date.today()).isoformat(),
                "duration_seconds": 0.0,
            }
        )

    start = perf_counter()
    db = SessionLocal()
    try:
        service = build_service_for_scheduler(db)
        trading_day = _latest_trading_date(date.today())
        result = _run_manual_refresh(service, settings, trading_day)
        final_state = "completed"
        final_message = result["message"]
    except Exception as exc:
        trading_day = _latest_trading_date(date.today())
        final_state = "failed"
        final_message = f"后台刷新失败：{exc}"
    finally:
        db.close()

    with lock:
        status.update(
            {
                "state": final_state,
                "message": final_message,
                "finished_at": datetime.utcnow().isoformat(),
                "trading_date": trading_day.isoformat(),
                "duration_seconds": round(perf_counter() - start, 2),
            }
        )


def _build_market_provider_safe(settings) -> object:
    try:
        return build_market_provider(settings.market_data_provider)
    except ProviderError:
        return build_market_provider("baostock")


def _build_news_provider_safe(settings) -> object:
    try:
        return build_news_provider(
            settings.news_data_provider,
            tushare_token=settings.tushare_token,
            news_webhook_url=settings.news_webhook_url,
            news_site_whitelist=settings.news_site_whitelist,
            news_site_timeout=settings.news_site_timeout,
        )
    except ProviderError:
        return build_news_provider(
            "sites",
            tushare_token=settings.tushare_token,
            news_webhook_url=settings.news_webhook_url,
            news_site_whitelist=settings.news_site_whitelist,
            news_site_timeout=settings.news_site_timeout,
        )


def _build_notifier(settings):
    if settings.alert_webhook_url:
        return WebhookNotifier(settings.alert_webhook_url)
    return StdoutNotifier()


def _build_dashboard_context(request: Request, service: FinanceAgentService, message: str = "") -> dict:
    metrics = service.get_dashboard_metrics()
    tracking_rows = service.get_stock_pool_tracking(limit=200)
    daily_records = service.get_daily_tracking_records(limit=500)
    opportunity_rows = service.list_opportunity_stocks(limit=20)
    recommender_rows = service.get_recommender_list()[:12]
    manual_refresh_status = _ensure_manual_refresh_state(request.app)
    return {
        "request": request,
        "metrics": metrics,
        "tracking_rows": tracking_rows,
        "daily_records": daily_records,
        "opportunity_rows": opportunity_rows,
        "recommender_rows": recommender_rows,
        "manual_refresh_status": manual_refresh_status,
        "macro_notes": service.get_recent_macro_research(limit=8),
        "stock_notes_feed": service.get_recent_stock_research_feed(limit=8),
        "llm_usage_summary": service.get_llm_usage_summary(hours=24),
        "message": message,
    }


def get_service(db: Session = Depends(get_db_session)) -> FinanceAgentService:
    settings = get_settings()
    rag_store = ResearchNoteStore(settings.rag_store_path)
    stock_knowledge_store = StockKnowledgeStore(settings.stock_knowledge_dir)
    llm_usage_store = LLMUsageStore(settings.llm_usage_store_path)
    return FinanceAgentService(
        db=db,
        market_provider=_build_market_provider_safe(settings),
        intraday_provider=get_intraday_provider(),
        news_provider=_build_news_provider_safe(settings),
        notifier=_build_notifier(settings),
        rag_store=rag_store,
        analysis_agent=StockAnalysisAgent(
            model_name=settings.analysis_model,
            api_key=settings.llm_api_key,
            api_base=settings.llm_api_base,
            chat_path=settings.llm_api_chat_path,
            timeout_seconds=settings.llm_api_timeout_seconds,
            usage_store=llm_usage_store,
        ),
        decision_engine=LLMDecisionEngine(
            model_name=settings.analysis_model,
            api_key=settings.llm_api_key,
            api_base=settings.llm_api_base,
            chat_path=settings.llm_api_chat_path,
            timeout_seconds=settings.llm_api_timeout_seconds,
            usage_store=llm_usage_store,
        ),
        stock_knowledge_store=stock_knowledge_store,
        input_parser_agent=LLMInputParserAgent(
            model_name=settings.analysis_model,
            api_key=settings.llm_api_key,
            api_base=settings.llm_api_base,
            chat_path=settings.llm_api_chat_path,
            timeout_seconds=settings.llm_api_timeout_seconds,
            usage_store=llm_usage_store,
        ),
        llm_usage_store=llm_usage_store,
        memory_retriever=MemoryRetriever(
            research_store=rag_store,
            stock_knowledge_store=stock_knowledge_store,
            default_limit=settings.memory_retrieval_limit,
        ),
        memory_retrieval_limit=settings.memory_retrieval_limit,
        daily_report_dir=settings.daily_report_dir,
    )


def get_intraday_provider():
    settings = get_settings()
    return build_intraday_provider(
        settings.intraday_data_provider,
        cache_dir=settings.intraday_cache_dir,
        request_interval_seconds=settings.intraday_request_interval_seconds,
        max_retries=settings.intraday_max_retries,
        pytdx_hosts=settings.intraday_pytdx_hosts,
        pytdx_bar_count=settings.intraday_pytdx_bar_count,
        pytdx_tick_limit=settings.intraday_pytdx_tick_limit,
    )


def _parse_optional_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"非法时间格式: {value}，请使用 ISO 格式") from exc


def build_service_for_scheduler(db: Session) -> FinanceAgentService:
    settings = get_settings()
    rag_store = ResearchNoteStore(settings.rag_store_path)
    stock_knowledge_store = StockKnowledgeStore(settings.stock_knowledge_dir)
    llm_usage_store = LLMUsageStore(settings.llm_usage_store_path)
    return FinanceAgentService(
        db=db,
        market_provider=_build_market_provider_safe(settings),
        intraday_provider=get_intraday_provider(),
        news_provider=_build_news_provider_safe(settings),
        notifier=_build_notifier(settings),
        rag_store=rag_store,
        analysis_agent=StockAnalysisAgent(
            model_name=settings.analysis_model,
            api_key=settings.llm_api_key,
            api_base=settings.llm_api_base,
            chat_path=settings.llm_api_chat_path,
            timeout_seconds=settings.llm_api_timeout_seconds,
            usage_store=llm_usage_store,
        ),
        decision_engine=LLMDecisionEngine(
            model_name=settings.analysis_model,
            api_key=settings.llm_api_key,
            api_base=settings.llm_api_base,
            chat_path=settings.llm_api_chat_path,
            timeout_seconds=settings.llm_api_timeout_seconds,
            usage_store=llm_usage_store,
        ),
        stock_knowledge_store=stock_knowledge_store,
        input_parser_agent=LLMInputParserAgent(
            model_name=settings.analysis_model,
            api_key=settings.llm_api_key,
            api_base=settings.llm_api_base,
            chat_path=settings.llm_api_chat_path,
            timeout_seconds=settings.llm_api_timeout_seconds,
            usage_store=llm_usage_store,
        ),
        llm_usage_store=llm_usage_store,
        memory_retriever=MemoryRetriever(
            research_store=rag_store,
            stock_knowledge_store=stock_knowledge_store,
            default_limit=settings.memory_retrieval_limit,
        ),
        memory_retrieval_limit=settings.memory_retrieval_limit,
        daily_report_dir=settings.daily_report_dir,
    )


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="DGQ Finance Agent", debug=settings.debug)
    _ensure_manual_refresh_state(app)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/system/check")
    def system_check(service: FinanceAgentService = Depends(get_service)):
        db_ok = True
        try:
            service.get_dashboard_metrics()
        except Exception:
            db_ok = False

        market_ok = service.market_provider.health_check()
        news_ok = service.news_provider.health_check()
        intraday_ok = get_intraday_provider().health_check()

        return {
            "database": db_ok,
            "market_provider": market_ok,
            "intraday_provider": intraday_ok,
            "news_provider": news_ok,
            "market_provider_name": settings.market_data_provider,
            "intraday_provider_name": settings.intraday_data_provider,
            "news_provider_name": settings.news_data_provider,
            "analysis_model": settings.analysis_model,
            "llm_ready": bool(
                settings.llm_api_base
                and settings.analysis_model
                and (
                    settings.llm_api_key
                    or settings.llm_api_base.startswith("http://127.0.0.1")
                    or settings.llm_api_base.startswith("http://localhost")
                )
            ),
            "memory_backend": settings.memory_backend,
            "memory_retrieval_limit": settings.memory_retrieval_limit,
            "llm_usage_summary": service.get_llm_usage_summary(hours=24),
            "input_parser_ready": bool(
                settings.llm_api_base
                and settings.analysis_model
                and (
                    settings.llm_api_key
                    or settings.llm_api_base.startswith("http://127.0.0.1")
                    or settings.llm_api_base.startswith("http://localhost")
                )
            ),
        }

    @app.get("/api/intraday/{stock_code}", response_model=IntradayBarsResponse)
    def get_intraday_bars(
        stock_code: str,
        period: str = "1",
        adjust: str = "",
        start_datetime: Optional[str] = None,
        end_datetime: Optional[str] = None,
        use_storage: bool = False,
        limit: int = 240,
        provider=Depends(get_intraday_provider),
        service: FinanceAgentService = Depends(get_service),
    ):
        start_value = _parse_optional_datetime(start_datetime)
        end_value = _parse_optional_datetime(end_datetime)
        if use_storage:
            bars = service.list_intraday_bars_from_storage(
                stock_code=stock_code,
                period=period,
                adjust=adjust,
                limit=limit,
            )
            return IntradayBarsResponse(
                stock_code=stock_code,
                source=settings.intraday_data_provider,
                period=period,
                adjust=adjust,
                used_cache=False,
                start_datetime=start_value,
                end_datetime=end_value,
                bars=[item.__dict__ for item in bars],
            )

        try:
            bars, used_cache = provider.get_minute_bars(
                stock_code=stock_code,
                period=period,
                adjust=adjust,
                start_datetime=start_value,
                end_datetime=end_value,
            )
        except ProviderError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        return IntradayBarsResponse(
            stock_code=stock_code,
            source=settings.intraday_data_provider,
            period=period,
            adjust=adjust,
            used_cache=used_cache,
            start_datetime=start_value,
            end_datetime=end_value,
            bars=[item.__dict__ for item in bars],
        )

    @app.get("/api/intraday/{stock_code}/ticks", response_model=IntradayTradesResponse)
    def get_intraday_ticks(
        stock_code: str,
        use_storage: bool = False,
        limit: int = 120,
        provider=Depends(get_intraday_provider),
        service: FinanceAgentService = Depends(get_service),
    ):
        if use_storage:
            trades = service.list_intraday_ticks_from_storage(stock_code=stock_code, limit=limit)
            return IntradayTradesResponse(
                stock_code=stock_code,
                source=settings.intraday_data_provider,
                used_cache=False,
                trades=[item.__dict__ for item in trades],
            )

        try:
            trades, used_cache = provider.get_trade_ticks(stock_code=stock_code)
        except ProviderError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        return IntradayTradesResponse(
            stock_code=stock_code,
            source=settings.intraday_data_provider,
            used_cache=used_cache,
            trades=[item.__dict__ for item in trades],
        )

    @app.post("/api/intraday/{stock_code}/sync", response_model=IntradaySyncResponse)
    def sync_intraday_stock(
        stock_code: str,
        period: str = "1",
        adjust: str = "",
        start_datetime: Optional[str] = None,
        end_datetime: Optional[str] = None,
        include_ticks: bool = True,
        provider=Depends(get_intraday_provider),
        service: FinanceAgentService = Depends(get_service),
    ):
        start_value = _parse_optional_datetime(start_datetime)
        end_value = _parse_optional_datetime(end_datetime)
        try:
            bars, used_cache = provider.get_minute_bars(
                stock_code=stock_code,
                period=period,
                adjust=adjust,
                start_datetime=start_value,
                end_datetime=end_value,
            )
        except ProviderError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        bar_result = service.save_intraday_bars(
            stock_code=stock_code,
            bars=bars,
            period=period,
            adjust=adjust,
            source=settings.intraday_data_provider,
            used_cache=used_cache,
        )

        tick_result = {"saved": 0, "latest_timestamp": ""}
        if include_ticks:
            try:
                trades, tick_used_cache = provider.get_trade_ticks(stock_code=stock_code)
                tick_result = service.save_intraday_ticks(
                    stock_code=stock_code,
                    trades=trades,
                    source=settings.intraday_data_provider,
                    used_cache=tick_used_cache,
                )
            except ProviderError:
                tick_result = {"saved": 0, "latest_timestamp": ""}

        detail = service.get_stock_detail(stock_code) or {}
        return IntradaySyncResponse(
            stock_code=stock_code,
            stock_name=detail.get("stock_name", ""),
            source=settings.intraday_data_provider,
            period=period,
            adjust=adjust,
            used_cache=used_cache,
            saved_bars=bar_result["saved"],
            saved_ticks=tick_result["saved"],
            latest_bar_timestamp=bar_result["latest_timestamp"],
            latest_tick_timestamp=tick_result["latest_timestamp"],
        )

    @app.post("/api/intraday/batch_sync", response_model=IntradayBatchSyncResponse)
    def sync_intraday_batch(
        payload: IntradayBatchSyncRequest,
        provider=Depends(get_intraday_provider),
        service: FinanceAgentService = Depends(get_service),
    ):
        targets = payload.stock_codes or [
            item["stock_code"] for item in service.list_intraday_sync_candidates(limit=payload.limit)
        ]
        items = []
        success_count = 0
        for stock_code in targets:
            detail = service.get_stock_detail(stock_code) or {}
            try:
                bars, used_cache = provider.get_minute_bars(
                    stock_code=stock_code,
                    period=payload.period,
                    adjust=payload.adjust,
                    start_datetime=payload.start_datetime,
                    end_datetime=payload.end_datetime,
                )
                bar_result = service.save_intraday_bars(
                    stock_code=stock_code,
                    bars=bars,
                    period=payload.period,
                    adjust=payload.adjust,
                    source=settings.intraday_data_provider,
                    used_cache=used_cache,
                )
                tick_saved = 0
                if payload.include_ticks:
                    try:
                        trades, tick_used_cache = provider.get_trade_ticks(stock_code=stock_code)
                        tick_saved = service.save_intraday_ticks(
                            stock_code=stock_code,
                            trades=trades,
                            source=settings.intraday_data_provider,
                            used_cache=tick_used_cache,
                        )["saved"]
                    except ProviderError:
                        tick_saved = 0
                items.append(
                    {
                        "stock_code": stock_code,
                        "stock_name": detail.get("stock_name", ""),
                        "ok": True,
                        "message": "synced",
                        "saved_bars": bar_result["saved"],
                        "saved_ticks": tick_saved,
                        "latest_bar_timestamp": bar_result["latest_timestamp"],
                    }
                )
                success_count += 1
            except ProviderError as exc:
                items.append(
                    {
                        "stock_code": stock_code,
                        "stock_name": detail.get("stock_name", ""),
                        "ok": False,
                        "message": str(exc),
                        "saved_bars": 0,
                        "saved_ticks": 0,
                        "latest_bar_timestamp": "",
                    }
                )

        return IntradayBatchSyncResponse(
            source=settings.intraday_data_provider,
            period=payload.period,
            adjust=payload.adjust,
            total_requested=len(targets),
            success_count=success_count,
            failed_count=len(targets) - success_count,
            items=items,
        )

    @app.post("/api/messages")
    @app.post("/api/messages/ingest")
    def ingest_message(payload: IngestMessageRequest, service: FinanceAgentService = Depends(get_service)):
        records = service.ingest_message(
            message=payload.message,
            recommender_name=payload.recommender_name,
            wechat_id=payload.wechat_id,
            recommend_ts=payload.recommend_ts,
            source=payload.source,
        )
        return {"created": len(records), "recommendation_ids": [item.id for item in records]}

    @app.post("/api/messages/import_text", response_model=BulkImportTextResponse)
    def import_text_messages(payload: BulkImportTextRequest, service: FinanceAgentService = Depends(get_service)):
        result = service.ingest_bulk_text(
            raw_text=payload.raw_text,
            default_recommender_name=payload.default_recommender_name,
            source=payload.source,
        )
        return BulkImportTextResponse(**result)

    @app.post("/api/research/ingest")
    def ingest_research(payload: ResearchTextRequest, service: FinanceAgentService = Depends(get_service)):
        result = service.ingest_research_text(
            text=payload.text,
            operator_name=payload.operator_name,
            source=payload.source,
        )
        return result

    @app.post("/api/recommendations/manual")
    def add_manual(payload: ManualRecommendationRequest, service: FinanceAgentService = Depends(get_service)):
        recommendation = service.add_manual_recommendation(
            stock_code=payload.stock_code,
            logic=payload.logic,
            recommender_name=payload.recommender_name,
            stock_name=payload.stock_name,
            wechat_id=payload.wechat_id,
            recommend_ts=payload.recommend_ts,
        )
        return {"id": recommendation.id}

    @app.post("/api/evaluations/daily")
    def evaluate_daily(payload: DailyEvaluationRequest, service: FinanceAgentService = Depends(get_service)):
        daily = service.evaluate_recommendation(
            recommendation_id=payload.recommendation_id,
            close_price=payload.close_price,
            high_price=payload.high_price,
            low_price=payload.low_price,
            pnl_percent=payload.pnl_percent,
            max_drawdown=payload.max_drawdown,
            sharpe_ratio=payload.sharpe_ratio,
            logic_validated=payload.logic_validated,
            market_cap_score=payload.market_cap_score,
            elasticity_score=payload.elasticity_score,
            liquidity_score=payload.liquidity_score,
            daily_date=payload.date or date.today(),
            notes=payload.notes,
        )
        return {"id": daily.id, "evaluation_score": daily.evaluation_score}

    @app.post("/api/evaluations/run")
    def run_daily_job(service: FinanceAgentService = Depends(get_service)):
        trading_day = _latest_trading_date(date.today())
        count = service.evaluate_all_recommendations(trading_day)
        return {"evaluated": count, "trading_date": trading_day.isoformat()}

    @app.post("/api/reports/daily")
    def generate_daily_report(service: FinanceAgentService = Depends(get_service)):
        trading_day = _latest_trading_date(date.today())
        path = service.generate_daily_tracking_file(trading_day)
        return {"report_file": path, "trading_date": trading_day.isoformat()}

    @app.post("/api/commands", response_model=CommandResponse)
    def run_command(payload: CommandRequest, service: FinanceAgentService = Depends(get_service)):
        handler = OpenClawCommandHandler(service)
        result = handler.handle(payload.command, operator="api_user")
        return CommandResponse(result=result)

    @app.post("/api/news/scan", response_model=NewsScanResponse)
    def scan_news(payload: NewsScanRequest, service: FinanceAgentService = Depends(get_service)):
        result = service.run_news_discovery_scan(
            trading_date=date.today(),
            min_score=payload.min_score,
            auto_promote=payload.auto_promote,
            auto_promote_min_score=payload.auto_promote_min_score,
            limit=payload.limit,
        )
        return NewsScanResponse(**result)

    @app.get("/api/news/candidates", response_model=NewsCandidateListResponse)
    def list_news_candidates(
        status: str = "candidate",
        limit: int = 50,
        service: FinanceAgentService = Depends(get_service),
    ):
        items = service.list_news_candidates(limit=limit, status=status)
        return NewsCandidateListResponse(items=items)

    @app.post("/api/news/candidates/promote")
    def promote_news_candidate(payload: NewsCandidatePromoteRequest, service: FinanceAgentService = Depends(get_service)):
        recommendation = service.promote_news_candidate(payload.candidate_id, operator="api_user")
        if recommendation is None:
            return {"ok": False, "message": "candidate not found"}
        return {"ok": True, "recommendation_id": recommendation.id, "stock_code": recommendation.stock.stock_code}

    @app.post("/api/alerts/subscribe")
    def subscribe(payload: AlertRequest, service: FinanceAgentService = Depends(get_service)):
        alert = service.subscribe_alert(payload.stock_code, payload.subscriber)
        return {"id": alert.id, "stock_code": alert.stock_code, "subscriber": alert.subscriber}

    @app.post("/api/connectors/wechat/webhook")
    def wechat_webhook(payload: dict, service: FinanceAgentService = Depends(get_service)):
        message = payload.get("message", "")
        recommender_name = payload.get("recommender_name", "未知")
        wechat_id = payload.get("wechat_id", "")
        records = service.ingest_message(message, recommender_name, wechat_id=wechat_id, source="wechaty")
        return {"created": len(records)}

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, service: FinanceAgentService = Depends(get_service)):
        return templates.TemplateResponse("dashboard.html", _build_dashboard_context(request, service))

    @app.get("/stocks/{stock_code}", response_class=HTMLResponse)
    def stock_detail(stock_code: str, request: Request, service: FinanceAgentService = Depends(get_service)):
        detail = service.get_stock_detail(stock_code)
        if detail is None:
            return templates.TemplateResponse(
                "dashboard.html",
                _build_dashboard_context(request, service, message=f"未找到股票 {stock_code}"),
                status_code=404,
            )
        return templates.TemplateResponse(
            "stock_detail.html",
            {
                "request": request,
                "detail": detail,
            },
        )

    @app.post("/manual/refresh", response_class=HTMLResponse)
    def manual_refresh_form(
        request: Request,
        service: FinanceAgentService = Depends(get_service),
    ):
        status = _ensure_manual_refresh_state(request.app)
        if status.get("state") == "running":
            message = "后台刷新已在执行中，请勿重复点击。稍后刷新页面查看结果。"
        else:
            worker = Thread(target=_run_manual_refresh_in_background, args=(request.app,), daemon=True)
            worker.start()
            message = "已启动后台刷新任务。页面会先立即返回，待任务完成后可刷新查看最新结果。"

        return templates.TemplateResponse(
            "dashboard.html",
            _build_dashboard_context(request, service, message=message),
        )

    @app.post("/manual/add", response_class=HTMLResponse)
    def manual_add_form(
        request: Request,
        stock_code: str = Form(...),
        stock_name: str = Form(""),
        logic: str = Form(...),
        recommender_name: str = Form(...),
        service: FinanceAgentService = Depends(get_service),
    ):
        service.add_manual_recommendation(
            stock_code=stock_code,
            stock_name=stock_name,
            logic=logic,
            recommender_name=recommender_name,
        )
        return templates.TemplateResponse(
            "dashboard.html",
            _build_dashboard_context(request, service, message="手动推荐已录入"),
        )

    @app.post("/manual/import", response_class=HTMLResponse)
    def manual_import_form(
        request: Request,
        raw_text: str = Form(...),
        default_recommender_name: str = Form("群友"),
        service: FinanceAgentService = Depends(get_service),
    ):
        result = service.ingest_bulk_text(
            raw_text=raw_text,
            default_recommender_name=default_recommender_name,
            source="manual_bulk",
        )
        return templates.TemplateResponse(
            "dashboard.html",
            _build_dashboard_context(
                request,
                service,
                message=(
                    f"批量导入完成：解析{result['total_records']}条，"
                    f"新增{result['created']}条，重复{result['duplicates']}条，忽略{result['ignored']}条，"
                    f"沉淀资讯{result['rag_notes']}条"
                ),
            ),
        )

    @app.post("/manual/research", response_class=HTMLResponse)
    def manual_research_form(
        request: Request,
        research_text: str = Form(...),
        operator_name: str = Form("研究员"),
        research_scope: str = Form("stock"),
        service: FinanceAgentService = Depends(get_service),
    ):
        if research_scope == "macro":
            result = service.ingest_macro_research_text(
                text=research_text,
                operator_name=operator_name,
                source="manual_macro_research",
            )
            message = f"宏观/通用资讯已入库：{result['saved']} 条"
        else:
            result = service.ingest_stock_research_text(
                text=research_text,
                operator_name=operator_name,
                source="manual_stock_research",
            )
            message = (
                f"个股资讯已入库：{result['saved']} 条，"
                f"关联股票 {result['linked_stocks']} 只，知识条目 {result['linked_entries']} 条"
            )
        return templates.TemplateResponse(
            "dashboard.html",
            _build_dashboard_context(request, service, message=message),
        )

    if settings.scheduler_enabled:
        scheduler = create_scheduler(settings.scheduler_cron, build_service_for_scheduler)
        if settings.scheduler_news_scan_enabled:
            add_news_scan_job(
                scheduler=scheduler,
                cron_expr=settings.scheduler_news_scan_cron,
                service_factory=build_service_for_scheduler,
                min_score=settings.news_discovery_min_score,
                auto_promote=False,
                auto_promote_min_score=settings.news_auto_promote_min_score,
            )

        @app.on_event("startup")
        def startup_event() -> None:
            scheduler.start()

        @app.on_event("shutdown")
        def shutdown_event() -> None:
            if scheduler.running:
                scheduler.shutdown(wait=False)

    return app


app = create_app()
