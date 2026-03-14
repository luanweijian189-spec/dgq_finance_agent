from __future__ import annotations

import base64
import logging
import secrets
from datetime import date, datetime
from threading import Lock, Thread
from time import perf_counter
from typing import Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .agent import OpenClawCommandHandler
from .agent_matrix import build_agent_matrix_manager
from .analysis_agent import StockAnalysisAgent
from .config import get_basic_auth_exempt_paths, get_settings
from .database import SessionLocal, get_db_session
from .decision_engine import LLMDecisionEngine
from .input_parser_agent import LLMInputParserAgent
from .llm_usage_store import LLMUsageStore
from .memory import MemoryRetriever
from .notifier import (
    CompositeNotifier,
    DingTalkNotifier,
    OpenClawNotifier,
    QQBotNotifier,
    QQOfficialBotNotifier,
    StdoutNotifier,
    WebhookNotifier,
)
from .providers import ProviderError, build_intraday_provider, build_market_provider, build_news_provider
from .qq_official_bot import (
    QQ_CALLBACK_DISPATCH,
    QQ_CALLBACK_HEARTBEAT,
    QQ_CALLBACK_VALIDATION,
    build_dispatch_ack,
    build_heartbeat_ack,
    build_validation_response,
    get_qq_official_bot_client,
    parse_qq_callback_body,
    parse_qq_message_event,
    verify_qq_signature,
)
from .repo_ops import build_repo_ops_manager
from .rag_store import ResearchNoteStore
from .stock_knowledge_store import StockKnowledgeStore
from .schemas import (
    AlertRequest,
    AgentMatrixTaskActionRequest,
    AgentMatrixTaskCreateRequest,
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
    RepoOpsTaskActionRequest,
    RepoOpsTaskCreateRequest,
    ResearchTextRequest,
)
from .scheduler import add_intraday_refresh_job, add_news_scan_job, create_scheduler
from .services import FinanceAgentService


templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)


def _unauthorized_basic_auth_response() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": "authentication required"},
        headers={"WWW-Authenticate": 'Basic realm="dgq-finance-agent"'},
    )


def _verify_basic_auth(request: Request, settings) -> bool:
    if not settings.web_basic_auth_enabled:
        return True

    path = request.url.path or "/"
    for prefix in get_basic_auth_exempt_paths(settings):
        if path == prefix or path.startswith(f"{prefix.rstrip('/')}/"):
            return True

    auth_header = str(request.headers.get("authorization") or "").strip()
    if not auth_header.lower().startswith("basic "):
        return False

    encoded = auth_header[6:].strip()
    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
    except Exception:
        return False

    username, sep, password = decoded.partition(":")
    if not sep:
        return False
    return secrets.compare_digest(username, settings.web_basic_auth_username) and secrets.compare_digest(
        password,
        settings.web_basic_auth_password,
    )


def _default_manual_refresh_status() -> dict[str, object]:
    return {
        "state": "idle",
        "message": "尚未执行手动刷新",
        "started_at": "",
        "finished_at": "",
        "trading_date": "",
        "duration_seconds": 0.0,
        "progress_percent": 0,
        "stage": "idle",
        "stage_label": "未开始",
        "last_updated_at": "",
        "run_id": "",
    }


def _latest_trading_date(base_day: date) -> date:
    trading_day = base_day
    while trading_day.weekday() >= 5:
        trading_day = date.fromordinal(trading_day.toordinal() - 1)
    return trading_day


def _ensure_manual_refresh_state(app: FastAPI) -> dict:
    if not hasattr(app.state, "manual_refresh_lock"):
        app.state.manual_refresh_lock = Lock()
    if not hasattr(app.state, "manual_refresh_status"):
        app.state.manual_refresh_status = _default_manual_refresh_status()
    return app.state.manual_refresh_status


def _snapshot_manual_refresh_status(app: FastAPI) -> dict[str, object]:
    status = _ensure_manual_refresh_state(app)
    lock = app.state.manual_refresh_lock
    with lock:
        return dict(status)


def _update_manual_refresh_status(app: FastAPI, **updates) -> dict[str, object]:
    status = _ensure_manual_refresh_state(app)
    lock = app.state.manual_refresh_lock
    with lock:
        status.update(updates)
        status["last_updated_at"] = datetime.utcnow().isoformat()
        return dict(status)


def _run_manual_refresh(service: FinanceAgentService, settings, trading_day: date, progress_callback=None) -> dict[str, str]:
    if progress_callback is not None:
        progress_callback(
            stage="cleanup",
            stage_label="清理异常数据",
            progress_percent=10,
            message="正在清理周末等无效日评估/预测记录。",
        )
    cleanup_result = service.cleanup_invalid_market_data()
    news_message = "新闻扫描未执行"

    if progress_callback is not None:
        progress_callback(
            stage="news_scan",
            stage_label="扫描新闻与候选",
            progress_percent=35,
            message="正在扫描新闻、候选新股，并尝试更新跟踪池。",
        )
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

    if progress_callback is not None:
        progress_callback(
            stage="evaluate",
            stage_label="评估股票池",
            progress_percent=70,
            message="正在拉取行情、执行股票池评估并刷新结论。",
        )
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

    if progress_callback is not None:
        progress_callback(
            stage="finalize",
            stage_label="整理结果",
            progress_percent=95,
            message="正在汇总执行结果并准备更新前端状态。",
        )

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
    run_id = datetime.utcnow().strftime("manual-refresh-%Y%m%d-%H%M%S")
    _update_manual_refresh_status(
        app,
        state="running",
        message="后台刷新进行中：正在执行新闻扫描、行情评估与日报刷新。",
        started_at=datetime.utcnow().isoformat(),
        finished_at="",
        trading_date=_latest_trading_date(date.today()).isoformat(),
        duration_seconds=0.0,
        progress_percent=3,
        stage="booting",
        stage_label="任务启动中",
        run_id=run_id,
    )

    start = perf_counter()
    db = SessionLocal()
    try:
        service = build_service_for_scheduler(db)
        trading_day = _latest_trading_date(date.today())
        result = _run_manual_refresh(
            service,
            settings,
            trading_day,
            progress_callback=lambda **kwargs: _update_manual_refresh_status(app, state="running", **kwargs),
        )
        final_state = "completed"
        final_message = result["message"]
    except Exception as exc:
        trading_day = _latest_trading_date(date.today())
        final_state = "failed"
        final_message = f"后台刷新失败：{exc}"
    finally:
        db.close()

    _update_manual_refresh_status(
        app,
        state=final_state,
        message=final_message,
        finished_at=datetime.utcnow().isoformat(),
        trading_date=trading_day.isoformat(),
        duration_seconds=round(perf_counter() - start, 2),
        progress_percent=100 if final_state == "completed" else 0,
        stage="done" if final_state == "completed" else "failed",
        stage_label="已完成" if final_state == "completed" else "执行失败",
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
    notifiers = [StdoutNotifier()]
    if settings.alert_webhook_url:
        notifiers.append(WebhookNotifier(settings.alert_webhook_url))
    if (
        settings.dingtalk_bot_enabled
        and settings.dingtalk_client_id
        and settings.dingtalk_client_secret
        and settings.dingtalk_robot_code
        and settings.dingtalk_open_conversation_id
    ):
        notifiers.append(
            DingTalkNotifier(
                client_id=settings.dingtalk_client_id,
                client_secret=settings.dingtalk_client_secret,
                robot_code=settings.dingtalk_robot_code,
                open_conversation_id=settings.dingtalk_open_conversation_id,
                api_base_url=settings.dingtalk_api_base_url,
                oauth_url=settings.dingtalk_oauth_url,
                timeout_seconds=settings.dingtalk_timeout_seconds,
            )
        )
    if settings.openclaw_notifier_enabled:
        notifiers.append(
            OpenClawNotifier(
                command=settings.openclaw_command,
                profile=settings.openclaw_profile,
                channel=settings.openclaw_channel,
                recipient=settings.openclaw_recipient,
                timeout_seconds=settings.openclaw_timeout_seconds,
            )
        )
    if settings.qq_bot_enabled and settings.qq_bot_base_url and settings.qq_bot_target_id:
        notifiers.append(
            QQBotNotifier(
                base_url=settings.qq_bot_base_url,
                target_type=settings.qq_bot_target_type,
                target_id=settings.qq_bot_target_id,
                access_token=settings.qq_bot_access_token,
            )
        )
    if (
        settings.qq_official_bot_enabled
        and settings.qq_official_bot_app_id
        and settings.qq_official_bot_app_secret
        and settings.qq_official_bot_target_id
    ):
        notifiers.append(
            QQOfficialBotNotifier(
                app_id=settings.qq_official_bot_app_id,
                app_secret=settings.qq_official_bot_app_secret,
                target_type=settings.qq_official_bot_target_type,
                target_id=settings.qq_official_bot_target_id,
                api_base_url=settings.qq_official_bot_api_base_url,
                token_url=settings.qq_official_bot_token_url,
                timeout_seconds=settings.qq_official_bot_timeout_seconds,
            )
        )
    return CompositeNotifier(notifiers)


def _build_dashboard_context(request: Request, service: FinanceAgentService, message: str = "") -> dict:
    metrics = service.get_dashboard_metrics()
    tracking_rows = service.get_stock_pool_tracking(limit=200)
    daily_records = service.get_daily_tracking_records(limit=500)
    opportunity_rows = service.list_opportunity_stocks(limit=20)
    recommender_rows = service.get_recommender_list()[:12]
    manual_refresh_status = _snapshot_manual_refresh_status(request.app)
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
            completions_path=settings.llm_api_completions_path,
            api_mode=settings.llm_api_mode,
            timeout_seconds=settings.llm_api_timeout_seconds,
            usage_store=llm_usage_store,
        ),
        decision_engine=LLMDecisionEngine(
            model_name=settings.analysis_model,
            api_key=settings.llm_api_key,
            api_base=settings.llm_api_base,
            chat_path=settings.llm_api_chat_path,
            completions_path=settings.llm_api_completions_path,
            api_mode=settings.llm_api_mode,
            timeout_seconds=settings.llm_api_timeout_seconds,
            usage_store=llm_usage_store,
        ),
        stock_knowledge_store=stock_knowledge_store,
        input_parser_agent=LLMInputParserAgent(
            model_name=settings.analysis_model,
            api_key=settings.llm_api_key,
            api_base=settings.llm_api_base,
            chat_path=settings.llm_api_chat_path,
            completions_path=settings.llm_api_completions_path,
            api_mode=settings.llm_api_mode,
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


def get_agent_matrix_manager():
    settings = get_settings()
    if not settings.agent_matrix_enabled:
        raise HTTPException(status_code=503, detail="agent matrix disabled")
    return build_agent_matrix_manager(settings)


def get_repo_ops_manager():
    settings = get_settings()
    if not settings.repo_ops_enabled:
        raise HTTPException(status_code=503, detail="repo ops disabled")
    return build_repo_ops_manager(settings)


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


def _first_non_empty(*values: object) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _normalize_connector_payload(payload: dict | None) -> dict[str, str]:
    data = payload or {}
    sender = data.get("sender") if isinstance(data.get("sender"), dict) else {}
    conversation = data.get("conversation") if isinstance(data.get("conversation"), dict) else {}
    text_payload = data.get("text") if isinstance(data.get("text"), dict) else {}
    content_payload = data.get("content") if isinstance(data.get("content"), dict) else {}
    post_type = _first_non_empty(data.get("post_type"), data.get("notice_type"), data.get("request_type"))
    message_type = _first_non_empty(data.get("message_type"), data.get("sub_type"))

    channel = _first_non_empty(
        data.get("channel"),
        data.get("source_channel"),
        data.get("platform"),
        conversation.get("channel"),
        "qq",
    ).lower()
    source = _first_non_empty(data.get("source"), f"openclaw_{channel}")
    recommender_name = _first_non_empty(
        data.get("recommender_name"),
        data.get("sender_name"),
        data.get("senderNick"),
        data.get("nickname"),
        data.get("operator"),
        data.get("from_name"),
        sender.get("name"),
        sender.get("nickname"),
    )
    sender_id = _first_non_empty(
        data.get("wechat_id"),
        data.get("sender_id"),
        data.get("senderStaffId"),
        data.get("senderId"),
        data.get("user_id"),
        data.get("from_id"),
        data.get("qq"),
        sender.get("id"),
        sender.get("user_id"),
        sender.get("qq"),
    )
    room_topic = _first_non_empty(
        data.get("room_topic"),
        data.get("group_name"),
        data.get("conversationTitle"),
        data.get("conversation_name"),
        data.get("chat_name"),
        conversation.get("name"),
    )
    conversation_id = _first_non_empty(
        data.get("group_id"),
        data.get("room_id"),
        data.get("conversation_id"),
        data.get("conversationId"),
        data.get("chat_id"),
        conversation.get("id"),
    )
    message = _first_non_empty(
        data.get("message"),
        text_payload.get("content") if isinstance(text_payload, dict) else data.get("text"),
        content_payload.get("content") if isinstance(content_payload, dict) else data.get("content"),
        data.get("body"),
        data.get("raw_message"),
    )
    event_type = _first_non_empty(
        data.get("event"),
        data.get("event_type"),
        post_type,
        data.get("message_type"),
        data.get("type"),
        "message",
    ).lower()

    if event_type == "message" and message_type:
        if message_type.lower() == "group":
            event_type = "group_message"
        elif message_type.lower() == "private":
            event_type = "private_message"
    if event_type == "message":
        conversation_type = _first_non_empty(data.get("conversationType"))
        if conversation_type == "2":
            event_type = "group_message"
        elif conversation_type == "1":
            event_type = "private_message"

    return {
        "channel": channel,
        "source": source,
        "message": message,
        "recommender_name": recommender_name or sender_id or "未知用户",
        "sender_id": sender_id,
        "room_topic": room_topic,
        "conversation_id": conversation_id,
        "event_type": event_type,
    }


def _verify_connector_token(request: Request, payload: dict | None, settings) -> None:
    expected = str(getattr(settings, "connector_shared_token", "") or "").strip()
    if not expected:
        return

    data = payload or {}
    provided = _first_non_empty(
        request.headers.get("x-connector-token"),
        request.headers.get("authorization"),
        data.get("token"),
        data.get("access_token"),
    )
    if provided.lower().startswith("bearer "):
        provided = provided[7:].strip()
    if provided != expected:
        raise HTTPException(status_code=401, detail="非法连接器令牌")


def _handle_connector_webhook(
    request: Request,
    payload: dict,
    service: FinanceAgentService,
    *,
    verify_connector_token: bool = True,
) -> dict[str, object]:
    settings = get_settings()
    if verify_connector_token:
        _verify_connector_token(request, payload, settings)
    normalized = _normalize_connector_payload(payload)

    if normalized["event_type"] not in {"", "message", "text", "group_message", "private_message", "chat_message"}:
        return {
            "ok": True,
            "action": "ignored",
            "reason": f"unsupported_event:{normalized['event_type']}",
            "channel": normalized["channel"],
        }

    message = normalized["message"]
    if not message:
        return {
            "ok": True,
            "action": "ignored",
            "reason": "empty_message",
            "channel": normalized["channel"],
        }

    if message.startswith("/"):
        handler = OpenClawCommandHandler(
            service,
            matrix_manager=get_agent_matrix_manager(),
            repo_ops_manager=get_repo_ops_manager(),
        )
        operator = f"{normalized['channel']}:{normalized['sender_id'] or normalized['recommender_name']}"
        reply_message = handler.handle(message, operator=operator)
        return {
            "ok": True,
            "action": "command",
            "channel": normalized["channel"],
            "conversation_id": normalized["conversation_id"],
            "room_topic": normalized["room_topic"],
            "reply_message": reply_message,
        }

    parsed_items = service._parse_recommendations(message, normalized["recommender_name"], None)

    records = service.ingest_message(
        message=message,
        recommender_name=normalized["recommender_name"],
        wechat_id=normalized["sender_id"],
        source=normalized["source"],
        deduplicate=True,
    )
    if records:
        stock_codes = "、".join(item.stock.stock_code for item in records[:3])
        suffix = "" if len(records) <= 3 else f" 等{len(records)}条"
        reply_message = f"已收录 {len(records)} 条推荐：{stock_codes}{suffix}"
        return {
            "ok": True,
            "action": "ingest",
            "channel": normalized["channel"],
            "conversation_id": normalized["conversation_id"],
            "room_topic": normalized["room_topic"],
            "created": len(records),
            "recommendation_ids": [item.id for item in records],
            "reply_message": reply_message,
        }

    if parsed_items:
        return {
            "ok": True,
            "action": "duplicate",
            "channel": normalized["channel"],
            "conversation_id": normalized["conversation_id"],
            "room_topic": normalized["room_topic"],
            "created": 0,
            "reply_message": "这条荐股已存在，已跳过重复入库。",
        }

    research_result = service.ingest_research_text(
        text=message,
        operator_name=normalized["recommender_name"],
        source=normalized["source"],
    )
    return {
        "ok": True,
        "action": "research",
        "channel": normalized["channel"],
        "conversation_id": normalized["conversation_id"],
        "room_topic": normalized["room_topic"],
        "saved": int(research_result.get("saved") or 0),
        "reply_message": "已收到，未识别到明确荐股，已按研究笔记归档。",
    }


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
            completions_path=settings.llm_api_completions_path,
            api_mode=settings.llm_api_mode,
            timeout_seconds=settings.llm_api_timeout_seconds,
            usage_store=llm_usage_store,
        ),
        decision_engine=LLMDecisionEngine(
            model_name=settings.analysis_model,
            api_key=settings.llm_api_key,
            api_base=settings.llm_api_base,
            chat_path=settings.llm_api_chat_path,
            completions_path=settings.llm_api_completions_path,
            api_mode=settings.llm_api_mode,
            timeout_seconds=settings.llm_api_timeout_seconds,
            usage_store=llm_usage_store,
        ),
        stock_knowledge_store=stock_knowledge_store,
        input_parser_agent=LLMInputParserAgent(
            model_name=settings.analysis_model,
            api_key=settings.llm_api_key,
            api_base=settings.llm_api_base,
            chat_path=settings.llm_api_chat_path,
            completions_path=settings.llm_api_completions_path,
            api_mode=settings.llm_api_mode,
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

    @app.middleware("http")
    async def basic_auth_middleware(request: Request, call_next):
        current_settings = get_settings()
        if current_settings.web_basic_auth_enabled and not _verify_basic_auth(request, current_settings):
            return _unauthorized_basic_auth_response()
        return await call_next(request)

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

    @app.get("/api/manual_refresh/status")
    def get_manual_refresh_status(request: Request):
        return _snapshot_manual_refresh_status(request.app)

    @app.post("/api/manual_refresh/start")
    def start_manual_refresh(request: Request):
        status = _snapshot_manual_refresh_status(request.app)
        if status.get("state") == "running":
            return {
                "ok": True,
                "already_running": True,
                "message": "后台刷新已在执行中。",
                "status": status,
            }

        worker = Thread(target=_run_manual_refresh_in_background, args=(request.app,), daemon=True)
        worker.start()
        return {
            "ok": True,
            "already_running": False,
            "message": "已启动后台刷新任务。",
            "status": _snapshot_manual_refresh_status(request.app),
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

    @app.get("/api/stocks/{stock_code}/intraday_snapshot")
    def get_stock_intraday_snapshot(
        stock_code: str,
        period: str = "1",
        adjust: str = "",
        refresh: bool = False,
        include_ticks: bool = True,
        limit: int = 240,
        tick_limit: int = 120,
        bar_since: Optional[str] = None,
        tick_since: Optional[str] = None,
        delta_only: bool = False,
        service: FinanceAgentService = Depends(get_service),
    ):
        refresh_result = {"refreshed": False, "reason": "storage_only"}
        if refresh:
            refresh_result = service.refresh_stock_realtime_context(
                stock_code=stock_code,
                period=period,
                adjust=adjust,
                include_ticks=include_ticks,
            )
        snapshot = service.get_intraday_snapshot(
            stock_code=stock_code,
            period=period,
            adjust=adjust,
            limit=limit,
            tick_limit=tick_limit,
            bar_since=bar_since,
            tick_since=tick_since,
            delta_only=delta_only,
        )
        snapshot["refresh"] = refresh_result
        return snapshot

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
        handler = OpenClawCommandHandler(
            service,
            matrix_manager=get_agent_matrix_manager(),
            repo_ops_manager=get_repo_ops_manager(),
        )
        result = handler.handle(payload.command, operator="api_user")
        return CommandResponse(result=result)

    @app.get("/api/dev/agent-matrix/roles")
    def list_agent_matrix_roles():
        manager = get_agent_matrix_manager()
        return {"items": [role.model_dump() for role in manager.build_default_matrix()]}

    @app.get("/api/dev/agent-matrix/tasks")
    def list_agent_matrix_tasks(limit: int = 20, status: str = ""):
        manager = get_agent_matrix_manager()
        items = manager.list_tasks(limit=limit, status=status)
        return {"items": [item.model_dump() for item in items]}

    @app.post("/api/dev/agent-matrix/tasks")
    def create_agent_matrix_task(payload: AgentMatrixTaskCreateRequest):
        manager = get_agent_matrix_manager()
        task = manager.create_task(
            payload.objective,
            context=payload.context,
            operator=payload.operator,
            source=payload.source,
            conversation_id=payload.conversation_id,
            branch=payload.branch,
        )
        if payload.auto_dispatch:
            task = manager.dispatch_task(task.task_id, auto_check=payload.auto_check)
        return task.model_dump()

    @app.get("/api/dev/agent-matrix/tasks/{task_id}")
    def get_agent_matrix_task(task_id: str):
        manager = get_agent_matrix_manager()
        task = manager.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="agent matrix task not found")
        return task.model_dump()

    @app.post("/api/dev/agent-matrix/tasks/{task_id}/dispatch")
    def dispatch_agent_matrix_task(task_id: str, payload: AgentMatrixTaskActionRequest):
        manager = get_agent_matrix_manager()
        try:
            task = manager.dispatch_task(task_id, auto_check=payload.auto_check)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="agent matrix task not found") from exc
        return task.model_dump()

    @app.post("/api/dev/agent-matrix/tasks/{task_id}/summary")
    def summarize_agent_matrix_task(task_id: str):
        manager = get_agent_matrix_manager()
        try:
            task = manager.summarize_task(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="agent matrix task not found") from exc
        return task.model_dump()

    @app.get("/api/dev/repo-ops/policy")
    def get_repo_ops_policy():
        manager = get_repo_ops_manager()
        return {
            "provider": manager.provider,
            "workspace": manager.workspace,
            "default_branch": manager.default_branch,
            "policy": manager.default_policy.model_dump(),
        }

    @app.get("/api/dev/repo-ops/tasks")
    def list_repo_ops_tasks(limit: int = 20, status: str = ""):
        manager = get_repo_ops_manager()
        items = manager.list_tasks(limit=limit, status=status)
        return {"items": [item.model_dump() for item in items]}

    @app.post("/api/dev/repo-ops/tasks")
    def create_repo_ops_task(payload: RepoOpsTaskCreateRequest):
        manager = get_repo_ops_manager()
        task = manager.create_task(
            payload.objective,
            context=payload.context,
            operator=payload.operator,
            source=payload.source,
            conversation_id=payload.conversation_id,
            linked_agent_task_id=payload.linked_agent_task_id,
            target_branch=payload.target_branch,
        )
        if payload.auto_plan:
            task = manager.plan_task(task.task_id)
        if payload.auto_execute:
            task = manager.execute_task(task.task_id)
        return task.model_dump()

    @app.get("/api/dev/repo-ops/tasks/{task_id}")
    def get_repo_ops_task(task_id: str):
        manager = get_repo_ops_manager()
        task = manager.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="repo ops task not found")
        return task.model_dump()

    @app.post("/api/dev/repo-ops/tasks/{task_id}/plan")
    def plan_repo_ops_task(task_id: str):
        manager = get_repo_ops_manager()
        try:
            task = manager.plan_task(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="repo ops task not found") from exc
        return task.model_dump()

    @app.post("/api/dev/repo-ops/tasks/{task_id}/execute")
    def execute_repo_ops_task(task_id: str):
        manager = get_repo_ops_manager()
        try:
            task = manager.execute_task(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="repo ops task not found") from exc
        return task.model_dump()

    @app.post("/api/dev/repo-ops/tasks/{task_id}/summary")
    def summarize_repo_ops_task(task_id: str):
        manager = get_repo_ops_manager()
        try:
            task = manager.summarize_task(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="repo ops task not found") from exc
        return task.model_dump()

    @app.post("/api/dev/repo-ops/tasks/{task_id}/approve")
    def approve_repo_ops_task(task_id: str, payload: RepoOpsTaskActionRequest):
        manager = get_repo_ops_manager()
        try:
            task = manager.approve_task(task_id, note=payload.note)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="repo ops task not found") from exc
        return task.model_dump()

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

    @app.post("/api/connectors/openclaw/webhook")
    @app.post("/api/connectors/qq/webhook")
    @app.post("/api/connectors/dingtalk/webhook")
    @app.post("/api/connectors/dingding/webhook")
    def openclaw_connector_webhook(
        request: Request,
        payload: dict,
        service: FinanceAgentService = Depends(get_service),
    ):
        return _handle_connector_webhook(request, payload, service)

    @app.post("/api/connectors/qq/official/webhook")
    async def qq_official_webhook(request: Request, service: FinanceAgentService = Depends(get_service)):
        settings = get_settings()
        if not settings.qq_official_bot_app_id or not settings.qq_official_bot_app_secret:
            raise HTTPException(status_code=503, detail="QQ 官方 Bot 未配置")

        body = await request.body()
        if not verify_qq_signature(settings.qq_official_bot_app_secret, request.headers, body):
            raise HTTPException(status_code=401, detail="QQ 官方回调签名校验失败")

        try:
            payload = parse_qq_callback_body(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="QQ 官方回调负载非法") from exc

        op_code = int(payload.get("op") or 0)
        data = payload.get("d") if isinstance(payload.get("d"), dict) else {}

        if op_code == QQ_CALLBACK_VALIDATION:
            plain_token = _first_non_empty(data.get("plain_token"))
            event_ts = _first_non_empty(data.get("event_ts"))
            if not plain_token or not event_ts:
                raise HTTPException(status_code=400, detail="QQ 官方回调验证负载缺失")
            return JSONResponse(build_validation_response(settings.qq_official_bot_app_secret, plain_token, event_ts))

        if op_code == QQ_CALLBACK_HEARTBEAT:
            seq = int(payload.get("s") or data.get("seq") or 0)
            return JSONResponse(build_heartbeat_ack(seq))

        if op_code != QQ_CALLBACK_DISPATCH:
            return JSONResponse(build_dispatch_ack(True))

        event = parse_qq_message_event(payload)
        if event is None:
            return JSONResponse(build_dispatch_ack(True))
        if event.is_bot:
            return JSONResponse(build_dispatch_ack(True))

        try:
            result = _handle_connector_webhook(
                request,
                event.to_connector_payload(),
                service,
                verify_connector_token=False,
            )
            reply_message = str(result.get("reply_message") or "").strip()
            if reply_message and event.chat_id:
                client = get_qq_official_bot_client(
                    settings.qq_official_bot_app_id,
                    settings.qq_official_bot_app_secret,
                    settings.qq_official_bot_api_base_url,
                    settings.qq_official_bot_token_url,
                    settings.qq_official_bot_timeout_seconds,
                )
                client.send_text(
                    "group" if event.chat_type == "group" else "private",
                    event.chat_id,
                    reply_message,
                    reply_to_message_id=event.message_id,
                    event_id=event.event_id,
                )
            logger.info(
                "qq official webhook handled: event=%s sender=%s action=%s",
                event.event_type,
                event.sender_id,
                result.get("action"),
            )
            return JSONResponse(build_dispatch_ack(True))
        except Exception:
            logger.exception("qq official webhook handling failed")
            return JSONResponse(build_dispatch_ack(False), status_code=502)

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
        if settings.scheduler_intraday_refresh_enabled:
            add_intraday_refresh_job(
                scheduler=scheduler,
                cron_expr=settings.scheduler_intraday_refresh_cron,
                service_factory=build_service_for_scheduler,
                limit=settings.scheduler_intraday_refresh_limit,
                min_change_percent=settings.scheduler_intraday_refresh_min_change_percent,
                force_notify=settings.scheduler_intraday_refresh_force_notify,
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
