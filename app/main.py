from __future__ import annotations

from datetime import date

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .agent import OpenClawCommandHandler
from .analysis_agent import StockAnalysisAgent
from .config import get_settings
from .database import get_db_session
from .decision_engine import LLMDecisionEngine
from .memory import MemoryRetriever
from .notifier import StdoutNotifier, WebhookNotifier
from .providers import build_market_provider, build_news_provider
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


def _build_notifier(settings):
    if settings.alert_webhook_url:
        return WebhookNotifier(settings.alert_webhook_url)
    return StdoutNotifier()


def get_service(db: Session = Depends(get_db_session)) -> FinanceAgentService:
    settings = get_settings()
    rag_store = ResearchNoteStore(settings.rag_store_path)
    stock_knowledge_store = StockKnowledgeStore(settings.stock_knowledge_dir)
    return FinanceAgentService(
        db=db,
        market_provider=build_market_provider(settings.market_data_provider),
        news_provider=build_news_provider(
            settings.news_data_provider,
            tushare_token=settings.tushare_token,
            news_webhook_url=settings.news_webhook_url,
            news_site_whitelist=settings.news_site_whitelist,
            news_site_timeout=settings.news_site_timeout,
        ),
        notifier=_build_notifier(settings),
        rag_store=rag_store,
        analysis_agent=StockAnalysisAgent(
            model_name=settings.analysis_model,
            api_key=settings.llm_api_key,
            api_base=settings.llm_api_base,
            chat_path=settings.llm_api_chat_path,
            timeout_seconds=settings.llm_api_timeout_seconds,
        ),
        decision_engine=LLMDecisionEngine(
            model_name=settings.analysis_model,
            api_key=settings.llm_api_key,
            api_base=settings.llm_api_base,
            chat_path=settings.llm_api_chat_path,
            timeout_seconds=settings.llm_api_timeout_seconds,
        ),
        stock_knowledge_store=stock_knowledge_store,
        memory_retriever=MemoryRetriever(
            research_store=rag_store,
            stock_knowledge_store=stock_knowledge_store,
            default_limit=settings.memory_retrieval_limit,
        ),
        memory_retrieval_limit=settings.memory_retrieval_limit,
        daily_report_dir=settings.daily_report_dir,
    )


def build_service_for_scheduler(db: Session) -> FinanceAgentService:
    settings = get_settings()
    rag_store = ResearchNoteStore(settings.rag_store_path)
    stock_knowledge_store = StockKnowledgeStore(settings.stock_knowledge_dir)
    return FinanceAgentService(
        db=db,
        market_provider=build_market_provider(settings.market_data_provider),
        news_provider=build_news_provider(
            settings.news_data_provider,
            tushare_token=settings.tushare_token,
            news_webhook_url=settings.news_webhook_url,
            news_site_whitelist=settings.news_site_whitelist,
            news_site_timeout=settings.news_site_timeout,
        ),
        notifier=_build_notifier(settings),
        rag_store=rag_store,
        analysis_agent=StockAnalysisAgent(
            model_name=settings.analysis_model,
            api_key=settings.llm_api_key,
            api_base=settings.llm_api_base,
            chat_path=settings.llm_api_chat_path,
            timeout_seconds=settings.llm_api_timeout_seconds,
        ),
        decision_engine=LLMDecisionEngine(
            model_name=settings.analysis_model,
            api_key=settings.llm_api_key,
            api_base=settings.llm_api_base,
            chat_path=settings.llm_api_chat_path,
            timeout_seconds=settings.llm_api_timeout_seconds,
        ),
        stock_knowledge_store=stock_knowledge_store,
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

        return {
            "database": db_ok,
            "market_provider": market_ok,
            "news_provider": news_ok,
            "market_provider_name": settings.market_data_provider,
            "news_provider_name": settings.news_data_provider,
            "analysis_model": settings.analysis_model,
            "llm_ready": bool(settings.llm_api_base and settings.llm_api_key and settings.analysis_model),
            "memory_backend": settings.memory_backend,
            "memory_retrieval_limit": settings.memory_retrieval_limit,
        }

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
        count = service.evaluate_all_recommendations(date.today())
        return {"evaluated": count}

    @app.post("/api/reports/daily")
    def generate_daily_report(service: FinanceAgentService = Depends(get_service)):
        path = service.generate_daily_tracking_file(date.today())
        return {"report_file": path}

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
        metrics = service.get_dashboard_metrics()
        tracking_rows = service.get_stock_pool_tracking(limit=200)
        daily_records = service.get_daily_tracking_records(limit=500)
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "metrics": metrics,
                "tracking_rows": tracking_rows,
                "daily_records": daily_records,
            },
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
        metrics = service.get_dashboard_metrics()
        tracking_rows = service.get_stock_pool_tracking(limit=200)
        daily_records = service.get_daily_tracking_records(limit=500)
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "metrics": metrics,
                "tracking_rows": tracking_rows,
                "daily_records": daily_records,
                "message": "手动推荐已录入",
            },
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
        metrics = service.get_dashboard_metrics()
        tracking_rows = service.get_stock_pool_tracking(limit=200)
        daily_records = service.get_daily_tracking_records(limit=500)
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "metrics": metrics,
                "tracking_rows": tracking_rows,
                "daily_records": daily_records,
                "message": (
                    f"批量导入完成：解析{result['total_records']}条，"
                    f"新增{result['created']}条，重复{result['duplicates']}条，忽略{result['ignored']}条，"
                    f"沉淀资讯{result['rag_notes']}条"
                ),
            },
        )

    @app.post("/manual/research", response_class=HTMLResponse)
    def manual_research_form(
        request: Request,
        research_text: str = Form(...),
        operator_name: str = Form("研究员"),
        service: FinanceAgentService = Depends(get_service),
    ):
        result = service.ingest_research_text(
            text=research_text,
            operator_name=operator_name,
            source="manual_research",
        )
        metrics = service.get_dashboard_metrics()
        tracking_rows = service.get_stock_pool_tracking(limit=200)
        daily_records = service.get_daily_tracking_records(limit=500)
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "metrics": metrics,
                "tracking_rows": tracking_rows,
                "daily_records": daily_records,
                "message": f"资讯已入库：{result['saved']} 条",
            },
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
