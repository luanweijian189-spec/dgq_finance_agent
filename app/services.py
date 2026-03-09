from __future__ import annotations

import csv
import json
import re
import zlib
from io import StringIO
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, desc, func, select
from sqlalchemy.orm import Session

from dgq_finance_agent.evaluation import (
    DailyMarketMetrics,
    RecommendationOutcome,
    compute_recommender_reliability,
    compute_stock_quality_score,
)
from dgq_finance_agent.message_parser import MessageParser

from .models import (
    AlertSubscription,
    DailyPerformance,
    IntradayBarRecord,
    IntradayTradeTick,
    NewsDiscoveryCandidate,
    Recommendation,
    Recommender,
    StockDailyMaintenance,
    StockPrediction,
    Stock,
)
from .analysis_agent import StockAnalysisAgent
from .decision_engine import LLMDecisionEngine
from .input_parser_agent import LLMInputParserAgent
from .llm_usage_store import LLMUsageStore
from .memory import MemoryRetriever
from .notifier import AlertNotifier
from .providers import IntradayBar, IntradayDataProvider, IntradayTrade, MarketDataProvider, NewsDataProvider, NewsDiscoveryItem
from .rag_store import ResearchNoteStore
from .stock_knowledge_store import StockKnowledgeStore


class FinanceAgentService:
    ACTIVE_RECOMMENDATION_STATUSES = {"tracking", "watchlist", "priority", "risk_alert"}
    NON_STOCK_ENTITY_EXACT = {
        "公司",
        "公司名称",
        "韩国",
        "日本",
        "美国",
        "全球",
        "中国",
        "韩国综合",
        "存储芯片",
        "电芯片",
        "燃气轮机",
        "光互连",
        "光芯片制造环节",
        "XPO",
        "LNG",
        "TIA",
        "Tower",
        "Lumentum",
        "Coherent",
        "Fabrinet",
        "MACOM",
        "Semtech",
        "MaxLinear",
        "英伟达",
        "苹果",
    }
    NON_STOCK_ENTITY_KEYWORDS = (
        "板块",
        "产业链",
        "制造环节",
        "燃气轮机",
        "存储芯片",
        "电芯片",
        "光互连",
        "方案",
        "综合",
        "客户",
    )
    NON_RECOMMENDER_PREFIX_KEYWORDS = (
        "关注",
        "建议",
        "推荐",
        "看好",
        "首推",
        "点评",
        "更新",
        "事件",
        "逻辑",
        "拐点",
        "风险",
        "板块",
        "主线",
    )
    SOURCE_QUALITY_WEIGHTS = {
        "manual_stock_research": 0.82,
        "manual_macro_research": 0.66,
        "news_discovery": 0.78,
        "news_scan": 0.8,
        "manual": 0.72,
        "manual_bulk": 0.68,
        "wechaty": 0.7,
        "wechat": 0.7,
    }
    _EXPORT_HEADER_PATTERN = re.compile(
        r"^(?P<name>[^\d:：]{1,32})\s+(?P<ts>\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)$"
    )
    _WATCHLIST_PATTERNS = (
        re.compile(r"(?:建议|积极)?(?:重点)?关注(?P<names>[\u4e00-\u9fa5A-Za-z0-9、，,和及与/\s（）()]{2,120})"),
        re.compile(r"(?:建议)?关注(?P<names>[\u4e00-\u9fa5A-Za-z0-9、，,和及与/\s]{2,80})"),
        re.compile(r"看好(?P<names>[\u4e00-\u9fa5A-Za-z0-9、，,和及与/\s]{2,80})"),
        re.compile(r"推荐(?P<names>[\u4e00-\u9fa5A-Za-z0-9、，,和及与/\s]{2,80})"),
        re.compile(r"首推(?P<names>[\u4e00-\u9fa5A-Za-z0-9、，,和及与/\s（）()]{2,120})"),
    )
    _STOPWORDS = {
        "低位补涨标的",
        "标的",
        "相关公司",
        "公司",
        "主业",
        "新业务",
        "机器人",
        "更新",
        "逻辑",
    }

    def __init__(
        self,
        db: Session,
        market_provider: MarketDataProvider,
        news_provider: NewsDataProvider,
        notifier: AlertNotifier,
        intraday_provider: Optional[IntradayDataProvider] = None,
        rag_store: Optional[ResearchNoteStore] = None,
        analysis_agent: Optional[StockAnalysisAgent] = None,
        decision_engine: Optional[LLMDecisionEngine] = None,
        input_parser_agent: Optional[LLMInputParserAgent] = None,
        stock_knowledge_store: Optional[StockKnowledgeStore] = None,
        llm_usage_store: Optional[LLMUsageStore] = None,
        memory_retriever: Optional[MemoryRetriever] = None,
        memory_retrieval_limit: int = 8,
        daily_report_dir: str = "reports/daily",
    ) -> None:
        self.db = db
        self.parser = MessageParser()
        self.market_provider = market_provider
        self.intraday_provider = intraday_provider
        self.news_provider = news_provider
        self.notifier = notifier
        self.rag_store = rag_store or ResearchNoteStore("data/research_notes.jsonl")
        self.analysis_agent = analysis_agent or StockAnalysisAgent(model_name="gpt-5.3-codex")
        self.decision_engine = decision_engine
        self.input_parser_agent = input_parser_agent
        self.stock_knowledge_store = stock_knowledge_store or StockKnowledgeStore("data/stocks")
        self.llm_usage_store = llm_usage_store or LLMUsageStore("data/llm_usage.jsonl")
        self.memory_retriever = memory_retriever or MemoryRetriever(
            research_store=self.rag_store,
            stock_knowledge_store=self.stock_knowledge_store,
            default_limit=memory_retrieval_limit,
        )
        self.memory_retrieval_limit = max(int(memory_retrieval_limit), 3)
        self.daily_report_dir = daily_report_dir
        self.last_conclusion_updates: list[str] = []

    @staticmethod
    def _clamp_score(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
        return max(lower, min(upper, float(value)))

    def _ensure_intraday_tables(self) -> None:
        bind = self.db.get_bind()
        if bind is None:
            return
        IntradayBarRecord.__table__.create(bind, checkfirst=True)
        IntradayTradeTick.__table__.create(bind, checkfirst=True)
        StockDailyMaintenance.__table__.create(bind, checkfirst=True)

    @staticmethod
    def _infer_intraday_volume_multiplier(bars: list[IntradayBar]) -> float:
        if not bars:
            return 100.0
        total_amount = sum(float(item.amount or 0.0) for item in bars)
        total_volume = sum(float(item.volume or 0.0) for item in bars)
        latest_price = float(bars[-1].close_price or 0.0)
        if total_amount <= 0 or total_volume <= 0 or latest_price <= 0:
            return 100.0
        price_if_volume_is_share = total_amount / total_volume
        price_if_volume_is_lot = total_amount / (total_volume * 100.0)
        share_diff = abs(price_if_volume_is_share - latest_price)
        lot_diff = abs(price_if_volume_is_lot - latest_price)
        return 1.0 if share_diff <= lot_diff else 100.0

    def _fetch_stock_daily_rows(self, stock_id: int) -> list[Any]:
        return self.db.execute(
            select(DailyPerformance, Recommendation, Recommender)
            .join(Recommendation, Recommendation.id == DailyPerformance.recommendation_id)
            .join(Recommender, Recommender.id == Recommendation.recommender_id)
            .where(Recommendation.stock_id == stock_id)
            .order_by(
                DailyPerformance.date.desc(),
                Recommendation.recommend_ts.desc(),
                DailyPerformance.id.desc(),
            )
        ).all()

    def _resolve_intraday_reference_price(
        self,
        stock: Stock,
        intraday_bars: list[IntradayBar],
        daily_rows: Optional[list[Any]] = None,
    ) -> tuple[float | None, str]:
        intraday_reference_price: float | None = None
        intraday_reference_source = ""
        if intraday_bars:
            intraday_day_text = str(intraday_bars[0].timestamp or "")[:10]
            try:
                intraday_day = date.fromisoformat(intraday_day_text)
            except ValueError:
                intraday_day = None

            if intraday_day is not None:
                previous_maintenance = self.db.scalar(
                    select(StockDailyMaintenance)
                    .where(
                        StockDailyMaintenance.stock_code == stock.stock_code,
                        StockDailyMaintenance.market_date < intraday_day,
                    )
                    .order_by(
                        StockDailyMaintenance.market_date.desc(),
                        StockDailyMaintenance.updated_at.desc(),
                    )
                    .limit(1)
                )
                if previous_maintenance is not None and float(previous_maintenance.latest_price or 0.0) > 0:
                    intraday_reference_price = float(previous_maintenance.latest_price)
                    intraday_reference_source = "previous_maintenance_close"

                for daily, _recommendation, _recommender in (daily_rows or self._fetch_stock_daily_rows(stock.id)):
                    if intraday_reference_price is not None:
                        break
                    if daily.date < intraday_day and float(daily.close_price or 0.0) > 0:
                        intraday_reference_price = float(daily.close_price)
                        intraday_reference_source = "previous_daily_close"
                        break

                try:
                    reference_snapshot = self.market_provider.get_daily_snapshot(
                        stock.stock_code,
                        self._previous_trade_day(intraday_day),
                    )
                    if intraday_reference_price is None and float(reference_snapshot.close_price or 0.0) > 0:
                        intraday_reference_price = float(reference_snapshot.close_price)
                        intraday_reference_source = "provider_previous_close"
                except Exception:
                    pass

        if intraday_reference_price is None and intraday_bars:
            intraday_reference_price = float(intraday_bars[0].open_price)
            intraday_reference_source = "first_open_fallback"
        return intraday_reference_price, intraday_reference_source

    def _build_intraday_maintenance_snapshot(
        self,
        stock: Stock,
        bars: list[IntradayBar],
        trades: list[IntradayTrade],
        reference_price: float | None,
        reference_source: str,
        data_source: str,
    ) -> Optional[dict[str, Any]]:
        if not bars:
            return None

        latest = bars[-1]
        trading_day_text = self._extract_intraday_trading_day(latest.timestamp)
        try:
            market_date = date.fromisoformat(trading_day_text)
        except ValueError:
            market_date = self._latest_trade_day_for_intraday()

        total_volume_lot = sum(float(item.volume or 0.0) for item in bars)
        total_amount = sum(float(item.amount or 0.0) for item in bars)
        volume_multiplier = self._infer_intraday_volume_multiplier(bars)
        total_volume_shares = total_volume_lot * volume_multiplier
        average_price = (total_amount / total_volume_shares) if total_volume_shares > 0 else float(latest.close_price)
        latest_price = float(latest.close_price)
        day_high = max(float(item.high_price or 0.0) for item in bars)
        day_low = min(float(item.low_price or 0.0) for item in bars)
        basis_price = float(reference_price or 0.0)
        change_amount = latest_price - basis_price if basis_price > 0 else 0.0
        change_percent = (change_amount / basis_price * 100.0) if basis_price > 0 else 0.0

        buy_count = 0
        sell_count = 0
        buy_volume_lot = 0.0
        sell_volume_lot = 0.0
        for item in trades:
            side = str(item.side or "")
            volume_lot = float(item.volume_lot or 0.0)
            if "买" in side or side.lower().startswith("buy"):
                buy_count += 1
                buy_volume_lot += volume_lot
            elif "卖" in side or side.lower().startswith("sell"):
                sell_count += 1
                sell_volume_lot += volume_lot

        summary_text = (
            f"{market_date.isoformat()} 盘中维护：最新价 {latest_price:.2f}，"
            f"相对基准 {change_amount:+.2f} / {change_percent:+.2f}%，"
            f"日内高低 {day_high:.2f}/{day_low:.2f}，均价 {average_price:.2f}，"
            f"成交额 {total_amount / 1e8:.2f} 亿，分钟线 {len(bars)} 条，逐笔 {len(trades)} 条。"
        )
        return {
            "stock_code": stock.stock_code,
            "stock_name": stock.stock_name or "",
            "market_date": market_date,
            "reference_price": basis_price,
            "latest_price": latest_price,
            "change_amount": change_amount,
            "change_percent": change_percent,
            "average_price": average_price,
            "high_price": day_high,
            "low_price": day_low,
            "volume_lot": total_volume_lot,
            "amount": total_amount,
            "bar_count": len(bars),
            "tick_count": len(trades),
            "buy_count": buy_count,
            "sell_count": sell_count,
            "buy_volume_lot": buy_volume_lot,
            "sell_volume_lot": sell_volume_lot,
            "latest_bar_timestamp": latest.timestamp or "",
            "latest_tick_timestamp": trades[-1].timestamp if trades else "",
            "reference_source": reference_source,
            "data_source": data_source,
            "summary_text": summary_text,
            "payload_json": json.dumps(
                {
                    "latest_bar": latest.timestamp,
                    "reference_price": basis_price,
                    "change_percent": change_percent,
                    "average_price": average_price,
                    "high_price": day_high,
                    "low_price": day_low,
                    "volume_lot": total_volume_lot,
                    "amount": total_amount,
                    "volume_multiplier": volume_multiplier,
                    "buy_count": buy_count,
                    "sell_count": sell_count,
                    "buy_volume_lot": buy_volume_lot,
                    "sell_volume_lot": sell_volume_lot,
                },
                ensure_ascii=False,
            ),
        }

    def upsert_stock_daily_maintenance(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._ensure_intraday_tables()
        row = self.db.scalar(
            select(StockDailyMaintenance).where(
                StockDailyMaintenance.stock_code == payload["stock_code"],
                StockDailyMaintenance.market_date == payload["market_date"],
            )
        )
        if row is None:
            row = StockDailyMaintenance(
                stock_code=payload["stock_code"],
                market_date=payload["market_date"],
            )
            self.db.add(row)

        for key, value in payload.items():
            setattr(row, key, value)
        self.db.commit()
        return self.get_latest_stock_daily_maintenance(payload["stock_code"]) or payload

    def get_latest_stock_daily_maintenance(self, stock_code: str) -> Optional[dict[str, Any]]:
        self._ensure_intraday_tables()
        row = self.db.scalar(
            select(StockDailyMaintenance)
            .where(StockDailyMaintenance.stock_code == stock_code)
            .order_by(StockDailyMaintenance.market_date.desc(), StockDailyMaintenance.updated_at.desc())
            .limit(1)
        )
        if row is None:
            return None
        payload = json.loads(row.payload_json or "{}")
        return {
            "stock_code": row.stock_code,
            "stock_name": row.stock_name or "",
            "market_date": row.market_date.isoformat(),
            "reference_price": float(row.reference_price or 0.0),
            "latest_price": float(row.latest_price or 0.0),
            "change_amount": float(row.change_amount or 0.0),
            "change_percent": float(row.change_percent or 0.0),
            "average_price": float(row.average_price or 0.0),
            "high_price": float(row.high_price or 0.0),
            "low_price": float(row.low_price or 0.0),
            "volume_lot": float(row.volume_lot or 0.0),
            "amount": float(row.amount or 0.0),
            "bar_count": int(row.bar_count or 0),
            "tick_count": int(row.tick_count or 0),
            "buy_count": int(row.buy_count or 0),
            "sell_count": int(row.sell_count or 0),
            "buy_volume_lot": float(row.buy_volume_lot or 0.0),
            "sell_volume_lot": float(row.sell_volume_lot or 0.0),
            "latest_bar_timestamp": row.latest_bar_timestamp or "",
            "latest_tick_timestamp": row.latest_tick_timestamp or "",
            "reference_source": row.reference_source or "",
            "data_source": row.data_source or "",
            "summary_text": row.summary_text or "",
            "payload": payload,
            "agent_intraday_analysis": str(payload.get("agent_intraday_analysis") or ""),
            "agent_intraday_analysis_ts": str(payload.get("agent_intraday_analysis_ts") or ""),
            "updated_at": row.updated_at.isoformat() if row.updated_at is not None else "",
        }

    def _save_intraday_agent_analysis(
        self,
        stock_code: str,
        market_date: date,
        analysis_text: str,
    ) -> Optional[dict[str, Any]]:
        row = self.db.scalar(
            select(StockDailyMaintenance).where(
                StockDailyMaintenance.stock_code == stock_code,
                StockDailyMaintenance.market_date == market_date,
            )
        )
        if row is None:
            return None

        try:
            payload = json.loads(row.payload_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        payload["agent_intraday_analysis"] = analysis_text
        payload["agent_intraday_analysis_ts"] = datetime.now().isoformat()
        row.payload_json = json.dumps(payload, ensure_ascii=False)
        self.db.commit()
        return self.get_latest_stock_daily_maintenance(stock_code)

    def build_intraday_agent_analysis(
        self,
        stock: Stock,
        latest_recommendation: Optional[Recommendation],
        latest_daily: Optional[dict[str, Any]],
        maintenance: Optional[dict[str, Any]],
    ) -> Optional[dict[str, Any]]:
        if maintenance is None:
            return None

        logic = ""
        if latest_recommendation is not None:
            logic = latest_recommendation.extracted_logic or latest_recommendation.original_message[:120]
        if not logic:
            logic = maintenance.get("summary_text") or f"{stock.stock_name or stock.stock_code}盘中走势跟踪"

        score = float((latest_daily or {}).get("evaluation_score") or 50.0)
        pnl_percent = float((latest_daily or {}).get("pnl_percent") or maintenance.get("change_percent") or 0.0)
        max_drawdown = float((latest_daily or {}).get("max_drawdown") or 0.0)
        if not max_drawdown:
            reference_price = float(maintenance.get("reference_price") or 0.0)
            low_price = float(maintenance.get("low_price") or 0.0)
            if reference_price > 0 and low_price > 0:
                max_drawdown = ((low_price / reference_price) - 1.0) * 100.0

        rag_context = self.memory_retriever.retrieve_for_stock(
            stock_code=stock.stock_code,
            stock_name=stock.stock_name or "",
            query_text=logic,
            limit=self.memory_retrieval_limit,
        )
        evidence_bundle = {
            "mode": "intraday_page_auto_analysis",
            "stock": {
                "stock_code": stock.stock_code,
                "stock_name": stock.stock_name or "",
                "industry": stock.industry or "",
            },
            "page_trigger": "open_stock_detail",
            "latest_intraday_maintenance": maintenance,
            "latest_daily": latest_daily or {},
            "latest_logic": logic,
        }
        analysis_text = self.analysis_agent.analyze(
            stock_code=stock.stock_code,
            stock_name=stock.stock_name or "",
            logic=logic,
            score=score,
            pnl_percent=pnl_percent,
            max_drawdown=max_drawdown,
            rag_context=rag_context,
            evidence_bundle=evidence_bundle,
        )
        market_date_text = str(maintenance.get("market_date") or "")
        if analysis_text and market_date_text:
            try:
                persisted = self._save_intraday_agent_analysis(
                    stock_code=stock.stock_code,
                    market_date=date.fromisoformat(market_date_text),
                    analysis_text=analysis_text,
                )
                return persisted or maintenance
            except ValueError:
                return maintenance
        return maintenance

    def refresh_stock_realtime_context(
        self,
        stock_code: str,
        period: str = "1",
        adjust: str = "",
        include_ticks: bool = True,
    ) -> dict[str, Any]:
        stock = self.db.scalar(select(Stock).where(Stock.stock_code == stock_code))
        if stock is None:
            return {"refreshed": False, "reason": "stock_not_found"}
        if self.intraday_provider is None:
            return {"refreshed": False, "reason": "intraday_provider_unavailable"}

        data_source = getattr(self.intraday_provider, "__class__", type(self.intraday_provider)).__name__
        incremental_start = self._resolve_intraday_incremental_start(
            stock_code=stock_code,
            period=period,
            adjust=adjust,
        )
        try:
            bars, bars_used_cache = self.intraday_provider.get_minute_bars(
                stock_code=stock_code,
                period=period,
                adjust=adjust,
                start_datetime=incremental_start,
            )
        except Exception as exc:
            return {"refreshed": False, "reason": f"bars_failed: {exc}"}

        trades: list[IntradayTrade] = []
        ticks_used_cache = False
        if include_ticks:
            try:
                trades, ticks_used_cache = self.intraday_provider.get_trade_ticks(stock_code=stock_code)
            except Exception:
                trades = []
                ticks_used_cache = False

        if bars:
            self.save_intraday_bars(
                stock_code=stock_code,
                bars=bars,
                period=period,
                adjust=adjust,
                source=data_source,
                used_cache=bars_used_cache,
            )
        if trades:
            latest_day_text = self._extract_intraday_trading_day(bars[-1].timestamp if bars else "")
            latest_day = date.fromisoformat(latest_day_text)
            self.save_intraday_ticks(
                stock_code=stock_code,
                trades=trades,
                source=data_source,
                used_cache=ticks_used_cache,
                trading_day=latest_day,
            )

        daily_rows = self._fetch_stock_daily_rows(stock.id)
        reference_price, reference_source = self._resolve_intraday_reference_price(stock, bars, daily_rows)
        maintenance = self._build_intraday_maintenance_snapshot(
            stock=stock,
            bars=bars,
            trades=trades,
            reference_price=reference_price,
            reference_source=reference_source,
            data_source=data_source,
        )
        if maintenance is not None:
            persisted = self.upsert_stock_daily_maintenance(maintenance)
            return {
                "refreshed": True,
                "used_cache": bool(bars_used_cache and (ticks_used_cache or not include_ticks)),
                "maintenance": persisted,
            }
        return {"refreshed": False, "reason": "no_intraday_data"}

    def get_intraday_snapshot(
        self,
        stock_code: str,
        period: str = "1",
        adjust: str = "",
        limit: int = 240,
        tick_limit: int = 120,
        bar_since: Optional[str] = None,
        tick_since: Optional[str] = None,
        delta_only: bool = False,
    ) -> dict[str, Any]:
        stock = self.db.scalar(select(Stock).where(Stock.stock_code == stock_code))
        all_latest_day_bars = self.list_intraday_bars_from_storage(
            stock_code=stock_code,
            period=period,
            adjust=adjust,
            limit=None,
        )
        all_latest_day_trades = self.list_intraday_ticks_from_storage(stock_code=stock_code, limit=None)
        bars = self.list_intraday_bars_from_storage(
            stock_code=stock_code,
            period=period,
            adjust=adjust,
            limit=limit,
        )
        trades = self.list_intraday_ticks_from_storage(stock_code=stock_code, limit=tick_limit)
        maintenance = self.get_latest_stock_daily_maintenance(stock_code)

        reference_price = float((maintenance or {}).get("reference_price") or 0.0)
        reference_source = str((maintenance or {}).get("reference_source") or "")
        if stock is not None and all_latest_day_bars:
            daily_rows = self._fetch_stock_daily_rows(stock.id)
            resolved_price, resolved_source = self._resolve_intraday_reference_price(stock, all_latest_day_bars, daily_rows)
            reference_price = float(resolved_price or 0.0)
            reference_source = resolved_source
            rebuilt_maintenance = self._build_intraday_maintenance_snapshot(
                stock=stock,
                bars=all_latest_day_bars,
                trades=all_latest_day_trades,
                reference_price=reference_price,
                reference_source=reference_source,
                data_source=str((maintenance or {}).get("data_source") or "storage_snapshot"),
            )
            if rebuilt_maintenance is not None:
                latest_bar_timestamp = str(rebuilt_maintenance.get("latest_bar_timestamp") or "")
                maintenance_market_date = str((maintenance or {}).get("market_date") or "")
                rebuilt_market_date = rebuilt_maintenance["market_date"].isoformat()
                maintenance_reference_price = float((maintenance or {}).get("reference_price") or 0.0)
                maintenance_reference_source = str((maintenance or {}).get("reference_source") or "")
                maintenance_latest_bar_timestamp = str((maintenance or {}).get("latest_bar_timestamp") or "")
                needs_refresh = (
                    maintenance is None
                    or maintenance_market_date != rebuilt_market_date
                    or abs(maintenance_reference_price - reference_price) > 1e-6
                    or maintenance_reference_source != reference_source
                    or maintenance_latest_bar_timestamp != latest_bar_timestamp
                )
                if needs_refresh:
                    maintenance = self.upsert_stock_daily_maintenance(rebuilt_maintenance)
                elif maintenance is None:
                    maintenance = rebuilt_maintenance

        returned_bars = self._filter_intraday_bars_since(bars, bar_since) if delta_only else bars
        returned_trades = self._filter_intraday_trades_since(trades, tick_since) if delta_only else trades
        summary = self.get_intraday_storage_summary(stock_code, period=period, adjust=adjust)
        summary.update(
            {
                "bar_cursor": (bars[-1].timestamp if bars else ""),
                "tick_cursor": (trades[-1].timestamp if trades else ""),
                "query_mode": "delta" if delta_only else "full",
                "returned_bar_count": len(returned_bars),
                "returned_tick_count": len(returned_trades),
                "session_slot_count": 240,
            }
        )

        return {
            "stock_code": stock_code,
            "stock_name": stock.stock_name if stock is not None else "",
            "bars": [item.__dict__ for item in returned_bars],
            "trades": [item.__dict__ for item in returned_trades],
            "reference_price": reference_price,
            "reference_source": reference_source,
            "maintenance": maintenance,
            "intraday_summary": summary,
        }

    @staticmethod
    def _parse_intraday_timestamp_text(value: str) -> Optional[datetime]:
        text = str(value or "").strip()
        if not text:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return None

    @staticmethod
    def _parse_intraday_tick_timestamp_text(value: str) -> Optional[datetime]:
        text = str(value or "").strip()
        if not text:
            return None
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                parsed = datetime.strptime(text, fmt)
                return parsed
            except ValueError:
                continue
        return None

    def _resolve_intraday_incremental_start(
        self,
        stock_code: str,
        period: str,
        adjust: str,
    ) -> Optional[datetime]:
        latest_bar = self.db.scalar(
            select(IntradayBarRecord)
            .where(
                IntradayBarRecord.stock_code == stock_code,
                IntradayBarRecord.period == period,
                IntradayBarRecord.adjust == adjust,
            )
            .order_by(IntradayBarRecord.trading_day.desc(), IntradayBarRecord.timestamp.desc())
            .limit(1)
        )
        if latest_bar is None:
            return None
        latest_dt = self._parse_intraday_timestamp_text(latest_bar.timestamp)
        if latest_dt is None:
            return None
        latest_trade_day = self._latest_trade_day_for_intraday()
        if latest_dt.date() != latest_trade_day:
            return None
        try:
            period_minutes = max(int(period or "1"), 1)
        except ValueError:
            period_minutes = 1
        return latest_dt - timedelta(minutes=period_minutes)

    def _filter_intraday_bars_since(self, bars: list[IntradayBar], bar_since: Optional[str]) -> list[IntradayBar]:
        since_dt = self._parse_intraday_timestamp_text(str(bar_since or ""))
        if since_dt is None:
            return bars
        return [item for item in bars if (self._parse_intraday_timestamp_text(item.timestamp) or datetime.min) > since_dt]

    def _filter_intraday_trades_since(self, trades: list[IntradayTrade], tick_since: Optional[str]) -> list[IntradayTrade]:
        since_dt = self._parse_intraday_tick_timestamp_text(str(tick_since or ""))
        if since_dt is None:
            return trades
        return [item for item in trades if (self._parse_intraday_tick_timestamp_text(item.timestamp) or datetime.min) > since_dt]

    @staticmethod
    def _build_intraday_refresh_highlight(
        before: Optional[dict[str, Any]],
        after: dict[str, Any],
        min_change_percent: float,
    ) -> Optional[str]:
        stock_code = str(after.get("stock_code") or "")
        stock_name = str(after.get("stock_name") or stock_code)
        latest_price = float(after.get("latest_price") or 0.0)
        change_percent = float(after.get("change_percent") or 0.0)
        latest_bar_timestamp = str(after.get("latest_bar_timestamp") or "")

        if before is None:
            return (
                f"{stock_code} {stock_name} 初始化快照 {latest_price:.2f}"
                f"（{change_percent:+.2f}%）@{latest_bar_timestamp or 'latest'}"
            )

        previous_price = float(before.get("latest_price") or 0.0)
        previous_change_percent = float(before.get("change_percent") or 0.0)
        delta_price = latest_price - previous_price
        delta_change_percent = change_percent - previous_change_percent
        threshold_price = max(abs(previous_price) * float(min_change_percent) / 100.0, 0.01)

        if abs(delta_change_percent) < float(min_change_percent) and abs(delta_price) < threshold_price:
            return None

        direction_text = "拉升" if delta_change_percent >= 0 else "回落"
        return (
            f"{stock_code} {stock_name} {latest_price:.2f}（{change_percent:+.2f}%），"
            f"较上轮{direction_text} {delta_change_percent:+.2f}pct / {delta_price:+.2f} 元"
        )

    def run_intraday_refresh_cycle(
        self,
        limit: int = 12,
        period: str = "1",
        adjust: str = "",
        include_ticks: bool = True,
        min_change_percent: float = 0.8,
        force_notify: bool = False,
    ) -> dict[str, Any]:
        candidates = self.list_intraday_sync_candidates(limit=limit)
        items: list[dict[str, Any]] = []
        highlights: list[str] = []
        success_count = 0

        for candidate in candidates:
            stock_code = str(candidate.get("stock_code") or "")
            stock_name = str(candidate.get("stock_name") or "")
            before = self.get_latest_stock_daily_maintenance(stock_code)
            try:
                result = self.refresh_stock_realtime_context(
                    stock_code=stock_code,
                    period=period,
                    adjust=adjust,
                    include_ticks=include_ticks,
                )
            except Exception as exc:
                items.append(
                    {
                        "stock_code": stock_code,
                        "stock_name": stock_name,
                        "ok": False,
                        "message": str(exc),
                    }
                )
                continue

            maintenance = result.get("maintenance") if isinstance(result, dict) else None
            if result.get("refreshed") and maintenance:
                success_count += 1
                highlight = self._build_intraday_refresh_highlight(before, maintenance, min_change_percent)
                if highlight:
                    highlights.append(highlight)
                items.append(
                    {
                        "stock_code": stock_code,
                        "stock_name": stock_name or maintenance.get("stock_name") or "",
                        "ok": True,
                        "latest_price": float(maintenance.get("latest_price") or 0.0),
                        "change_percent": float(maintenance.get("change_percent") or 0.0),
                        "latest_bar_timestamp": str(maintenance.get("latest_bar_timestamp") or ""),
                    }
                )
                continue

            items.append(
                {
                    "stock_code": stock_code,
                    "stock_name": stock_name,
                    "ok": False,
                    "message": str(result.get("reason") or "unknown"),
                }
            )

        should_notify = bool(highlights) or bool(force_notify)
        if should_notify and candidates:
            content = (
                f"本轮刷新 {len(candidates)} 只，成功 {success_count} 只，"
                f"显著变化 {len(highlights)} 只。"
            )
            if highlights:
                content = f"{content}{'；'.join(highlights[:6])}"
                if len(highlights) > 6:
                    content = f"{content}；其余 {len(highlights) - 6} 只请到页面查看"
            else:
                content = f"{content}本轮未发现超过阈值的显著变化。"
            self.notifier.send(
                title=f"盘中刷新 {datetime.now().strftime('%H:%M')}",
                content=content,
            )

        return {
            "total": len(candidates),
            "success_count": success_count,
            "failed_count": len(candidates) - success_count,
            "highlight_count": len(highlights),
            "items": items,
            "highlights": highlights,
        }

    @staticmethod
    def _next_trade_day(day: date) -> date:
        next_day = day
        while True:
            next_day = date.fromordinal(next_day.toordinal() + 1)
            if next_day.weekday() < 5:
                return next_day

    @staticmethod
    def _previous_trade_day(day: date) -> date:
        previous_day = day
        while True:
            previous_day = date.fromordinal(previous_day.toordinal() - 1)
            if previous_day.weekday() < 5:
                return previous_day

    @staticmethod
    def _evaluate_direction_result(direction: str, pnl_percent: float) -> tuple[str, str]:
        if direction == "up":
            if pnl_percent > 0.8:
                return "hit", "上涨预测命中"
            if pnl_percent < -0.8:
                return "miss", "上涨预测反向"
            return "neutral", "上涨预测但实际震荡"
        if direction == "down":
            if pnl_percent < -0.8:
                return "hit", "下跌预测命中"
            if pnl_percent > 0.8:
                return "miss", "下跌预测反向"
            return "neutral", "下跌预测但实际震荡"
        if abs(pnl_percent) <= 1.0:
            return "hit", "震荡预测命中"
        return "miss", "震荡预测偏差较大"

    def _find_stocks_in_text(self, text: str) -> list[Stock]:
        stocks: list[Stock] = []
        seen_ids: set[int] = set()

        for code in self.parser.extract_stock_codes(text):
            stock = self.db.scalar(select(Stock).where(Stock.stock_code == code))
            if stock and stock.id not in seen_ids:
                stocks.append(stock)
                seen_ids.add(stock.id)

        for name in self._extract_name_candidates(text):
            stock = self.db.scalar(select(Stock).where(Stock.stock_name == name))
            if stock and stock.id not in seen_ids:
                stocks.append(stock)
                seen_ids.add(stock.id)

        all_stocks = self.db.scalars(select(Stock)).all()
        for stock in all_stocks:
            if stock.id in seen_ids:
                continue
            if stock.stock_name and stock.stock_name in text:
                stocks.append(stock)
                seen_ids.add(stock.id)

        return stocks

    def _get_or_create_system_recommender(self) -> Recommender:
        return self._get_or_create_recommender(name="系统新闻发现", wechat_id="system_news")

    def _parse_ts(self, value: str) -> Optional[datetime]:
        text = (value or "").strip()
        if not text:
            return None
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d %H:%M",
            "%Y-%m-%d",
            "%Y/%m/%d",
        ):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return None

    def _name_to_virtual_code(self, stock_name: str) -> str:
        checksum = zlib.crc32(stock_name.encode("utf-8")) % 1000000
        return f"NAME_{checksum:06d}"

    def _extract_name_candidates(self, text: str) -> list[str]:
        candidates: list[str] = []

        title_hit = re.search(r"】\s*([\u4e00-\u9fa5]{2,12})更新", text)
        if title_hit:
            candidates.append(title_hit.group(1).strip())

        for pattern in self._WATCHLIST_PATTERNS:
            for match in pattern.finditer(text):
                raw_names = match.group("names")
                for part in re.split(r"[、，,和及与/]", raw_names):
                    name = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", part).strip()
                    name = re.sub(r"等.+$", "", name).strip()
                    if not name or len(name) < 2 or len(name) > 12:
                        continue
                    if any(stop in name for stop in self._STOPWORDS):
                        continue
                    if name in self._STOPWORDS:
                        continue
                    candidates.append(name)

        unique_names: list[str] = []
        seen: set[str] = set()
        for name in candidates:
            if name in seen:
                continue
            seen.add(name)
            unique_names.append(name)
        return unique_names

    @staticmethod
    def _normalize_stock_name(value: str) -> str:
        return re.sub(r"[\s\u3000（）()【】\[\]：:、,，。·]", "", (value or "")).strip()

    def _is_non_stock_entity(self, stock_name: str) -> bool:
        name = self._normalize_stock_name(stock_name)
        if not name:
            return True
        if name in self.NON_STOCK_ENTITY_EXACT:
            return True
        if any(keyword in name for keyword in self.NON_STOCK_ENTITY_KEYWORDS):
            return True
        if re.fullmatch(r"[A-Za-z]{2,12}", name):
            return True
        return False

    def _search_stock_candidates_by_name(self, stock_name: str, limit: int = 5) -> list[tuple[str, str]]:
        normalized = self._normalize_stock_name(stock_name)
        if not normalized:
            return []

        exact_rows = self.db.scalars(select(Stock).where(Stock.stock_name == normalized)).all()
        exact = [(row.stock_code, row.stock_name or normalized) for row in exact_rows]
        if exact:
            return exact[:limit]

        partial_rows = self.db.scalars(select(Stock).where(Stock.stock_name.contains(normalized))).all()
        partial = [(row.stock_code, row.stock_name or normalized) for row in partial_rows]
        if partial:
            return partial[:limit]

        if hasattr(self.market_provider, "search_stock_candidates"):
            try:
                provider_rows = self.market_provider.search_stock_candidates(normalized, limit=limit)
            except Exception:
                provider_rows = []
            cleaned: list[tuple[str, str]] = []
            for code, name in provider_rows:
                plain_code = re.sub(r"^(?:sh|sz)\.", "", str(code or ""), flags=re.I)
                normalized_name = self._normalize_stock_name(name)
                if re.fullmatch(r"\d{6}", plain_code) and normalized_name:
                    cleaned.append((plain_code, normalized_name))
            if cleaned:
                return cleaned[:limit]
        return []

    def _resolve_stock_identity(self, stock_code: str, stock_name: str) -> tuple[str, str, str]:
        code = (stock_code or "").strip()
        name = self._normalize_stock_name(stock_name)

        if code and not re.fullmatch(r"\d{6}", code):
            if not name and re.search(r"[\u4e00-\u9fa5A-Za-z]", code):
                name = self._normalize_stock_name(code)
            code = ""

        if name and self._is_non_stock_entity(name):
            return "", "", "ignored"

        if code and re.fullmatch(r"\d{6}", code):
            existing = self.db.scalar(select(Stock).where(Stock.stock_code == code))
            if existing is not None:
                resolved_name = existing.stock_name or name
                if not name or self._normalize_stock_name(resolved_name) == name:
                    return code, resolved_name, "tracking"

            if hasattr(self.market_provider, "get_stock_name"):
                try:
                    provider_name = self._normalize_stock_name(self.market_provider.get_stock_name(code))
                except Exception:
                    provider_name = ""
                if provider_name:
                    if not name or provider_name == name:
                        return code, provider_name, "tracking"

        if name:
            candidates = self._search_stock_candidates_by_name(name, limit=5)
            if candidates:
                for candidate_code, candidate_name in candidates:
                    if self._normalize_stock_name(candidate_name) == name:
                        return candidate_code, candidate_name, "tracking"
                candidate_code, candidate_name = candidates[0]
                return candidate_code, candidate_name, "tracking"

        if code and re.fullmatch(r"\d{6}", code):
            return code, name, "tracking"
        if name:
            return self._name_to_virtual_code(name), name, "pending_mapping"
        return "", "", "ignored"

    def _parse_name_only_recommendations(
        self,
        message: str,
        recommender_name: str,
        recommend_ts: Optional[datetime],
    ) -> list[dict[str, Any]]:
        names = self._extract_name_candidates(message)
        if not names:
            return []

        timestamp = recommend_ts or datetime.now()
        extracted_logic = self.parser.extract_logic(message)
        rows: list[dict[str, Any]] = []
        for name in names:
            resolved_code, resolved_name, status = self._resolve_stock_identity("", name)
            if status == "ignored" or not resolved_code:
                continue
            rows.append(
                {
                    "stock_code": resolved_code,
                    "stock_name": resolved_name,
                    "recommender_name": recommender_name,
                    "recommend_ts": timestamp,
                    "extracted_logic": extracted_logic,
                    "original_message": message,
                    "status": status,
                }
            )
        return rows

    @staticmethod
    def _has_candidate_recommendation_intent(message: str) -> bool:
        text = (message or "").strip()
        if not text:
            return False
        patterns = (
            r"建议(?:重点)?关注",
            r"积极关注",
            r"重点关注",
            r"首推",
            r"看好",
            r"推荐",
        )
        return any(re.search(pattern, text) for pattern in patterns)

    def _parse_candidate_recommendations_from_research(
        self,
        message: str,
        recommender_name: str,
        recommend_ts: Optional[datetime],
        understood_stocks: Optional[list[Any]] = None,
        logic: str = "",
    ) -> list[dict[str, Any]]:
        if not self._has_candidate_recommendation_intent(message):
            return []

        timestamp = recommend_ts or datetime.now()
        extracted_logic = logic or self.parser.extract_logic(message)
        candidate_names: list[str] = []
        for item in understood_stocks or []:
            name = self._normalize_stock_name(getattr(item, "stock_name", "") or "")
            if name:
                candidate_names.append(name)

        resolved_preview: list[tuple[str, str, str]] = []
        for name in candidate_names:
            resolved_preview.append(self._resolve_stock_identity("", name))
        if not any(status != "ignored" and code for code, _, status in resolved_preview):
            candidate_names.extend(self._extract_name_candidates(message))

        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for name in candidate_names:
            resolved_code, resolved_name, status = self._resolve_stock_identity("", name)
            if status == "ignored" or not resolved_code:
                continue
            dedup_key = f"{resolved_code}:{resolved_name}"
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            rows.append(
                {
                    "stock_code": resolved_code,
                    "stock_name": resolved_name,
                    "recommender_name": recommender_name,
                    "recommend_ts": timestamp,
                    "extracted_logic": extracted_logic,
                    "original_message": message,
                    "status": status,
                }
            )
        return rows

    def _looks_like_recommender_name(self, value: str) -> bool:
        text = (value or "").strip()
        if not text:
            return False
        if any(keyword in text for keyword in self.NON_RECOMMENDER_PREFIX_KEYWORDS):
            return False
        if len(text) > 12:
            return False
        if re.search(r"[0-9A-Za-z]{4,}", text):
            return False
        return True

    def _parse_recommendations(
        self,
        message: str,
        recommender_name: str,
        recommend_ts: Optional[datetime],
    ) -> list[Any]:
        llm_parsed = self._parse_recommendations_by_llm(message, recommender_name, recommend_ts)
        if llm_parsed is not None:
            return llm_parsed

        parsed = self.parser.parse_message(message, recommender_name, recommend_ts)
        if parsed:
            return parsed

        stock_codes = self.parser.extract_stock_codes(message)
        if not stock_codes:
            return self._parse_name_only_recommendations(message, recommender_name, recommend_ts)

        extracted_logic = self.parser.extract_logic(message)
        timestamp = recommend_ts or datetime.now()
        return [
            {
                "stock_code": code,
                "stock_name": "",
                "recommender_name": recommender_name,
                "recommend_ts": timestamp,
                "extracted_logic": extracted_logic,
                "original_message": message,
                "status": "tracking",
            }
            for code in stock_codes
        ]

    def _parse_recommendations_by_llm(
        self,
        message: str,
        recommender_name: str,
        recommend_ts: Optional[datetime],
    ) -> Optional[list[dict[str, Any]]]:
        if self.input_parser_agent is None:
            return None

        understood = self.input_parser_agent.understand_message(message)
        if understood is None:
            return None

        effective_confidence = understood.confidence
        if (
            effective_confidence < 0.35
            and understood.stocks
            and understood.message_type in {"recommendation", "tracking_update", "research"}
        ):
            effective_confidence = 0.55

        if effective_confidence < 0.35:
            return None

        if understood.message_type in {"research", "macro", "noise"}:
            return self._parse_candidate_recommendations_from_research(
                message=message,
                recommender_name=recommender_name,
                recommend_ts=recommend_ts,
                understood_stocks=understood.stocks,
                logic=understood.logic_summary,
            )

        if understood.message_type not in {"recommendation", "tracking_update"}:
            return None
        if not understood.stocks:
            return []

        timestamp = recommend_ts or datetime.now()
        logic = understood.logic_summary or self.parser.extract_logic(message)
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in understood.stocks:
            stock_code, stock_name, status = self._resolve_stock_identity(item.stock_code or "", item.stock_name or "")
            if status == "ignored" or not stock_code:
                continue

            dedup_key = f"{stock_code}:{stock_name}"
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            rows.append(
                {
                    "stock_code": stock_code,
                    "stock_name": stock_name,
                    "recommender_name": recommender_name,
                    "recommend_ts": timestamp,
                    "extracted_logic": logic,
                    "original_message": message,
                    "status": status,
                }
            )
        return rows

    def _parsed_field(self, parsed_item: Any, field: str) -> Any:
        if isinstance(parsed_item, dict):
            return parsed_item[field]
        return getattr(parsed_item, field)

    def _is_duplicate_recommendation(
        self,
        stock_id: int,
        recommender_id: int,
        message: str,
        recommend_ts: datetime,
    ) -> bool:
        existing = self.db.scalar(
            select(Recommendation.id).where(
                Recommendation.stock_id == stock_id,
                Recommendation.recommender_id == recommender_id,
                Recommendation.original_message == message,
                func.date(Recommendation.recommend_ts) == recommend_ts.date(),
            )
        )
        return existing is not None

    def _extract_json_records(self, raw_text: str, default_recommender_name: str) -> list[dict[str, Any]]:
        text = (raw_text or "").strip()
        if not text:
            return []
        if not (text.startswith("{") or text.startswith("[")):
            return []
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return []

        if isinstance(payload, dict):
            payload = [payload]
        if not isinstance(payload, list):
            return []

        records: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            message = str(item.get("message") or item.get("content") or item.get("text") or "").strip()
            if not message:
                continue
            recommender_name = str(
                item.get("recommender_name")
                or item.get("sender")
                or item.get("name")
                or default_recommender_name
            ).strip()
            recommend_ts = self._parse_ts(str(item.get("recommend_ts") or item.get("time") or item.get("ts") or ""))
            records.append(
                {
                    "message": message,
                    "recommender_name": recommender_name or default_recommender_name,
                    "recommend_ts": recommend_ts,
                }
            )
        return records

    def _extract_csv_records(self, raw_text: str, default_recommender_name: str) -> list[dict[str, Any]]:
        text = (raw_text or "").strip()
        if not text:
            return []
        first_line = text.splitlines()[0].lower()
        if not any(key in first_line for key in ("message", "content", "text")):
            return []

        try:
            reader = csv.DictReader(StringIO(text))
        except Exception:
            return []

        records: list[dict[str, Any]] = []
        for row in reader:
            message = str(row.get("message") or row.get("content") or row.get("text") or "").strip()
            if not message:
                continue
            recommender_name = str(
                row.get("recommender_name") or row.get("sender") or row.get("name") or default_recommender_name
            ).strip()
            recommend_ts = self._parse_ts(str(row.get("recommend_ts") or row.get("time") or row.get("ts") or ""))
            records.append(
                {
                    "message": message,
                    "recommender_name": recommender_name or default_recommender_name,
                    "recommend_ts": recommend_ts,
                }
            )
        return records

    def _extract_free_text_records(self, raw_text: str, default_recommender_name: str) -> list[dict[str, Any]]:
        cleaned_lines = [line.strip().lstrip("-•") for line in (raw_text or "").splitlines()]
        lines = [line for line in cleaned_lines if line]
        records: list[dict[str, Any]] = []

        i = 0
        while i < len(lines):
            line = lines[i]

            export_header = self._EXPORT_HEADER_PATTERN.match(line)
            if export_header and i + 1 < len(lines):
                next_line = lines[i + 1]
                records.append(
                    {
                        "message": next_line,
                        "recommender_name": export_header.group("name").strip(),
                        "recommend_ts": self._parse_ts(export_header.group("ts")),
                    }
                )
                i += 2
                continue

            room_topic = ""
            body = line
            if line.startswith("[") and "]" in line:
                room_topic = line[1 : line.find("]")].strip()
                body = line[line.find("]") + 1 :].strip()

            recommender_name = default_recommender_name
            message = body
            recommend_ts = None

            if "：" in body or ":" in body:
                separator = "：" if "：" in body else ":"
                left, right = body.split(separator, 1)
                left = left.strip()
                right = right.strip()
                if (
                    0 < len(left) <= 32
                    and not re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", left)
                    and self._looks_like_recommender_name(left)
                ):
                    recommender_name = left
                    message = right

            records.append(
                {
                    "message": message,
                    "recommender_name": recommender_name,
                    "recommend_ts": recommend_ts,
                    "room_topic": room_topic,
                }
            )
            i += 1

        return records

    def _extract_bulk_records(self, raw_text: str, default_recommender_name: str) -> list[dict[str, Any]]:
        records = self._extract_json_records(raw_text, default_recommender_name)
        if records:
            return records

        records = self._extract_csv_records(raw_text, default_recommender_name)
        if records:
            return records

        return self._extract_free_text_records(raw_text, default_recommender_name)

    def _save_research_note(self, text: str, recommender_name: str, source: str, recommend_ts: Optional[datetime]) -> None:
        if len(text.strip()) < 8:
            return
        self.rag_store.add_note(
            text=text,
            source=source,
            recommender_name=recommender_name,
            ts=recommend_ts,
        )

    def _build_stock_daily_analysis(self, recommendation: Recommendation, daily: Optional[DailyPerformance]) -> str:
        stock = recommendation.stock
        logic = recommendation.extracted_logic or recommendation.original_message[:120]
        rag_context = self.memory_retriever.retrieve_for_stock(
            stock_code=stock.stock_code,
            stock_name=stock.stock_name or "",
            query_text=logic,
            limit=self.memory_retrieval_limit,
        )
        evidence_bundle = self._build_llm_evidence_bundle(recommendation, daily, rag_context)

        if daily is None:
            return self.analysis_agent.analyze(
                stock_code=stock.stock_code,
                stock_name=stock.stock_name or "",
                logic=logic,
                score=45.0,
                pnl_percent=0.0,
                max_drawdown=-5.0,
                rag_context=rag_context,
                evidence_bundle=evidence_bundle,
            )

        return self.analysis_agent.analyze(
            stock_code=stock.stock_code,
            stock_name=stock.stock_name or "",
            logic=logic,
            score=float(daily.evaluation_score),
            pnl_percent=float(daily.pnl_percent),
            max_drawdown=float(daily.max_drawdown),
            rag_context=rag_context,
            evidence_bundle=evidence_bundle,
        )

    def _build_llm_evidence_bundle(
        self,
        recommendation: Recommendation,
        daily: Optional[DailyPerformance],
        rag_context: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        stock = recommendation.stock
        rag_items = list(rag_context or [])[:6]
        stock_entries = self.stock_knowledge_store.search_entries(
            stock_code=stock.stock_code,
            stock_name=stock.stock_name or "",
            limit=6,
        )
        macro_notes = self.get_recent_macro_research(limit=4)
        recent_recommendations = self.db.execute(
            select(Recommendation, Recommender)
            .join(Recommender, Recommender.id == Recommendation.recommender_id)
            .where(Recommendation.stock_id == stock.id)
            .order_by(Recommendation.recommend_ts.desc(), Recommendation.id.desc())
            .limit(4)
        ).all()
        prediction = self._latest_prediction(stock.stock_code)
        latest_intraday_maintenance = self.get_latest_stock_daily_maintenance(stock.stock_code)
        source_quality = self._build_source_quality_summary(
            stock_entries=stock_entries,
            macro_notes=macro_notes,
            recent_recommendations=recent_recommendations,
        )

        return {
            "stock": {
                "stock_code": stock.stock_code,
                "stock_name": stock.stock_name or "",
                "status": recommendation.status,
                "industry": stock.industry or "",
            },
            "latest_market": {
                "market_date": daily.date.isoformat() if daily is not None else "",
                "score": float(daily.evaluation_score) if daily is not None else 0.0,
                "pnl_percent": float(daily.pnl_percent) if daily is not None else 0.0,
                "max_drawdown": float(daily.max_drawdown) if daily is not None else 0.0,
                "logic_validated": bool(daily.logic_validated) if daily is not None else False,
            },
            "latest_intraday_maintenance": latest_intraday_maintenance,
            "source_summary": {
                "stock_knowledge_count": len(stock_entries),
                "macro_note_count": len(macro_notes),
                "rag_snippet_count": len(rag_items),
                "recommendation_count": len(recent_recommendations),
            },
            "source_quality": source_quality,
            "recent_recommendations": [
                {
                    "recommender_name": recommender.name,
                    "recommend_ts": recommendation_row.recommend_ts.isoformat(),
                    "source": recommendation_row.source,
                    "status": recommendation_row.status,
                    "logic": (recommendation_row.extracted_logic or "")[:160],
                }
                for recommendation_row, recommender in recent_recommendations
            ],
            "stock_knowledge": [
                {
                    "ts": str(item.get("ts") or ""),
                    "entry_type": str(item.get("entry_type") or ""),
                    "source": str(item.get("source") or ""),
                    "content": str(item.get("content") or "")[:180],
                }
                for item in stock_entries
            ],
            "macro_notes": [
                {
                    "ts": str(item.get("ts") or ""),
                    "source": str(item.get("source") or ""),
                    "text": str(item.get("text") or "")[:180],
                }
                for item in macro_notes
            ],
            "memory_snippets": rag_items,
            "latest_prediction": (
                {
                    "prediction_date": prediction.prediction_date.isoformat(),
                    "direction": prediction.direction,
                    "confidence": float(prediction.confidence),
                    "review_result": prediction.review_result,
                }
                if prediction is not None
                else None
            ),
        }

    def _source_quality_weight(self, source: str) -> float:
        normalized = (source or "").strip().lower()
        if not normalized:
            return 0.55
        for prefix, weight in self.SOURCE_QUALITY_WEIGHTS.items():
            if normalized.startswith(prefix):
                return weight
        return 0.6

    def _build_source_quality_summary(
        self,
        stock_entries: list[dict[str, Any]],
        macro_notes: list[dict[str, Any]],
        recent_recommendations: list[Any],
    ) -> dict[str, Any]:
        buckets: dict[str, dict[str, Any]] = {}

        def add_source(source: str, content: str = "") -> None:
            key = (source or "unknown").strip() or "unknown"
            bucket = buckets.setdefault(
                key,
                {
                    "source": key,
                    "count": 0,
                    "weight": self._source_quality_weight(key),
                    "sample": "",
                },
            )
            bucket["count"] += 1
            if content and not bucket["sample"]:
                bucket["sample"] = content[:80]

        for item in stock_entries:
            add_source(str(item.get("source") or item.get("entry_type") or "stock_memory"), str(item.get("content") or ""))
        for item in macro_notes:
            add_source(str(item.get("source") or "macro_research"), str(item.get("text") or ""))
        for recommendation, _recommender in recent_recommendations:
            add_source(str(recommendation.source or "manual"), str(recommendation.extracted_logic or recommendation.original_message or ""))

        rows: list[dict[str, Any]] = []
        weighted_score_sum = 0.0
        count_sum = 0
        for bucket in buckets.values():
            weighted_score = float(bucket["count"]) * float(bucket["weight"])
            rows.append(
                {
                    "source": bucket["source"],
                    "count": int(bucket["count"]),
                    "weight": float(bucket["weight"]),
                    "weighted_score": round(weighted_score, 3),
                    "sample": bucket["sample"],
                }
            )
            weighted_score_sum += weighted_score
            count_sum += int(bucket["count"])

        rows.sort(key=lambda item: (item["weighted_score"], item["count"]), reverse=True)
        return {
            "source_count": len(rows),
            "evidence_count": count_sum,
            "weighted_score": round(weighted_score_sum, 3),
            "avg_weight": round((weighted_score_sum / count_sum), 3) if count_sum else 0.0,
            "top_sources": rows[:6],
        }

    def _build_prediction_review_payload(
        self,
        prediction: StockPrediction,
        latest_daily: DailyPerformance,
    ) -> dict[str, Any]:
        pnl_percent = float(latest_daily.pnl_percent)
        max_drawdown = float(latest_daily.max_drawdown)
        review_tags: list[str] = [str(prediction.review_result or "pending")]

        if pnl_percent >= 3.0:
            review_tags.append("strong_up_move")
        elif pnl_percent <= -3.0:
            review_tags.append("strong_down_move")
        else:
            review_tags.append("small_move")

        if abs(max_drawdown) >= 5.0:
            review_tags.append("high_drawdown")
        if float(prediction.confidence) >= 0.7:
            review_tags.append("high_confidence")
        if prediction.direction == "up" and pnl_percent > 0:
            review_tags.append("direction_confirmed")
        elif prediction.direction == "down" and pnl_percent < 0:
            review_tags.append("direction_confirmed")
        elif prediction.direction == "sideways" and abs(pnl_percent) <= 1.0:
            review_tags.append("range_confirmed")
        else:
            review_tags.append("direction_conflicted")

        return {
            "prediction_date": prediction.prediction_date.isoformat(),
            "direction": prediction.direction,
            "confidence": float(prediction.confidence),
            "actual_pnl_percent": pnl_percent,
            "max_drawdown": max_drawdown,
            "review_result": prediction.review_result,
            "review_summary": prediction.review_notes,
            "review_tags": review_tags,
        }

    @staticmethod
    def _compose_tracking_notes(score_breakdown: str, ai_analysis: str) -> str:
        score_text = (score_breakdown or "").strip()
        ai_text = (ai_analysis or "").strip()
        if score_text.startswith("【SQS】") or "【AI】" in score_text:
            if ai_text and ai_text not in score_text:
                return f"{score_text}\n【AI】{ai_text}"
            return score_text
        if score_text and ai_text:
            return f"【SQS】{score_text}\n【AI】{ai_text}"
        return ai_text or score_text

    @staticmethod
    def _split_tracking_notes(notes: str) -> dict[str, str]:
        text = (notes or "").strip()
        if not text:
            return {"sqs_breakdown": "", "ai_analysis": ""}
        if "【AI】" in text:
            left, right = text.split("【AI】", 1)
            sqs = left.replace("【SQS】", "").strip()
            return {"sqs_breakdown": sqs, "ai_analysis": right.strip()}
        return {"sqs_breakdown": text, "ai_analysis": ""}

    def _upsert_news_candidate(self, item: NewsDiscoveryItem) -> NewsDiscoveryCandidate:
        existing = self.db.scalar(
            select(NewsDiscoveryCandidate).where(
                NewsDiscoveryCandidate.stock_code == item.stock_code,
                NewsDiscoveryCandidate.headline == item.headline,
                NewsDiscoveryCandidate.source_url == item.source_url,
            )
        )
        now = datetime.utcnow()
        if existing is None:
            candidate = NewsDiscoveryCandidate(
                stock_code=item.stock_code,
                stock_name=item.stock_name,
                headline=item.headline[:255],
                summary=item.summary,
                source_site=item.source_site,
                source_url=item.source_url,
                event_type=item.event_type,
                discovery_score=float(item.discovery_score),
                status="candidate",
                discovered_at=now,
                last_seen_at=now,
            )
            self.db.add(candidate)
            self.db.flush()
            return candidate

        existing.last_seen_at = now
        if float(item.discovery_score) > float(existing.discovery_score):
            existing.discovery_score = float(item.discovery_score)
        if item.summary and len(item.summary) > len(existing.summary or ""):
            existing.summary = item.summary
        if item.stock_name and not existing.stock_name:
            existing.stock_name = item.stock_name
        return existing

    def run_news_discovery_scan(
        self,
        trading_date: Optional[date] = None,
        min_score: float = 2.5,
        auto_promote_min_score: float = 3.8,
        auto_promote: bool = False,
        limit: int = 40,
    ) -> dict[str, Any]:
        current_date = trading_date or date.today()
        discovered = self.news_provider.discover_candidate_stocks(current_date, limit=limit)
        saved = 0
        promoted = 0
        updated_tracking = 0

        tracked_codes = {
            code
            for code in self.db.scalars(
                select(Stock.stock_code)
                .join(Recommendation, Recommendation.stock_id == Stock.id)
                .where(Recommendation.status.in_(self.ACTIVE_RECOMMENDATION_STATUSES))
            ).all()
        }

        for item in discovered:
            if float(item.discovery_score) < float(min_score):
                continue

            candidate = self._upsert_news_candidate(item)
            saved += 1

            if item.stock_code in tracked_codes:
                self.stock_knowledge_store.append_entry(
                    stock_code=item.stock_code,
                    stock_name=item.stock_name,
                    source="news_discovery",
                    operator="system",
                    entry_type="news",
                    content=f"{item.headline} | {item.summary[:140]}",
                )
                updated_tracking += 1

            should_promote = auto_promote and float(item.discovery_score) >= float(auto_promote_min_score)
            if should_promote and candidate.status == "candidate":
                recommendation = self.promote_news_candidate(candidate.id, operator="system", commit=False)
                if recommendation is not None:
                    promoted += 1

        self.db.commit()
        return {
            "scan_date": current_date.isoformat(),
            "raw_discovered": len(discovered),
            "saved_candidates": saved,
            "promoted": promoted,
            "updated_tracking": updated_tracking,
            "min_score": min_score,
            "auto_promote": auto_promote,
            "auto_promote_min_score": auto_promote_min_score,
        }

    def list_news_candidates(self, limit: int = 50, status: str = "candidate") -> list[dict[str, Any]]:
        stmt = select(NewsDiscoveryCandidate)
        if status and status != "all":
            stmt = stmt.where(NewsDiscoveryCandidate.status == status)
        rows = self.db.scalars(stmt.order_by(desc(NewsDiscoveryCandidate.last_seen_at)).limit(limit)).all()
        return [
            {
                "id": row.id,
                "stock_code": row.stock_code,
                "stock_name": row.stock_name,
                "headline": row.headline,
                "summary": row.summary,
                "source_site": row.source_site,
                "source_url": row.source_url,
                "event_type": row.event_type,
                "discovery_score": float(row.discovery_score),
                "status": row.status,
                "discovered_at": row.discovered_at.isoformat() if row.discovered_at else "",
                "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else "",
                "promoted_recommendation_id": row.promoted_recommendation_id,
            }
            for row in rows
        ]

    def promote_news_candidate(
        self,
        candidate_id: int,
        operator: str = "system",
        commit: bool = True,
    ) -> Optional[Recommendation]:
        candidate = self.db.get(NewsDiscoveryCandidate, candidate_id)
        if candidate is None:
            return None
        if candidate.status == "promoted" and candidate.promoted_recommendation_id:
            existing = self.db.get(Recommendation, candidate.promoted_recommendation_id)
            return existing

        stock = self._get_or_create_stock(candidate.stock_code, stock_name=candidate.stock_name)
        recommender = self._get_or_create_system_recommender()
        recommendation = Recommendation(
            stock_id=stock.id,
            recommender_id=recommender.id,
            recommend_ts=datetime.utcnow(),
            original_message=f"[新闻发现]{candidate.headline}",
            extracted_logic=f"{candidate.event_type}:{candidate.summary[:180]}",
            source="news_scan",
            status="tracking",
        )
        self.db.add(recommendation)
        self.db.flush()

        candidate.status = "promoted"
        candidate.promoted_recommendation_id = recommendation.id

        self.stock_knowledge_store.append_entry(
            stock_code=stock.stock_code,
            stock_name=stock.stock_name or "",
            source="news_discovery",
            operator=operator,
            entry_type="promotion",
            content=f"{candidate.headline} | score={float(candidate.discovery_score):.2f}",
        )

        if commit:
            self.db.commit()
            self.db.refresh(recommendation)
        return recommendation

    def _latest_prediction(self, stock_code: str, on_or_before: Optional[date] = None) -> Optional[StockPrediction]:
        stmt = select(StockPrediction).where(StockPrediction.stock_code == stock_code)
        if on_or_before is not None:
            stmt = stmt.where(StockPrediction.prediction_date <= on_or_before)
        return self.db.scalar(stmt.order_by(desc(StockPrediction.prediction_date)).limit(1))

    def _upsert_ai_prediction(
        self,
        recommendation: Recommendation,
        daily: DailyPerformance,
        prediction_date: date,
    ) -> Optional[StockPrediction]:
        if self.decision_engine is None:
            return None

        stock = recommendation.stock
        logic = recommendation.extracted_logic or recommendation.original_message[:120]
        memory_context = self.memory_retriever.retrieve_for_stock(
            stock_code=stock.stock_code,
            stock_name=stock.stock_name or "",
            query_text=logic,
            limit=self.memory_retrieval_limit,
        )
        decision = self.decision_engine.decide(
            stock_code=stock.stock_code,
            stock_name=stock.stock_name or "",
            logic=logic,
            score=float(daily.evaluation_score),
            pnl_percent=float(daily.pnl_percent),
            max_drawdown=float(daily.max_drawdown),
            memory_context=memory_context,
            evidence_bundle=self._build_llm_evidence_bundle(recommendation, daily, memory_context),
        )

        prediction = self.db.scalar(
            select(StockPrediction).where(
                StockPrediction.stock_code == stock.stock_code,
                StockPrediction.prediction_date == prediction_date,
            )
        )
        if prediction is None:
            prediction = StockPrediction(
                stock_code=stock.stock_code,
                stock_name=stock.stock_name or "",
                prediction_date=prediction_date,
            )
            self.db.add(prediction)

        prediction.stock_name = stock.stock_name or prediction.stock_name
        prediction.horizon_days = decision.horizon_days
        prediction.direction = decision.direction
        prediction.confidence = decision.confidence
        prediction.thesis = decision.thesis
        prediction.invalidation_conditions = decision.invalidation_conditions
        prediction.risk_flags = json.dumps(decision.risk_flags, ensure_ascii=False)
        prediction.evidence = json.dumps(decision.evidence, ensure_ascii=False)
        prediction.predicted_by = "llm" if "llm_unavailable" not in decision.risk_flags else "fallback"
        prediction.updated_at = datetime.utcnow()
        return prediction

    def _capture_tracking_snapshot(self) -> dict[str, dict[str, Any]]:
        rows = self.get_stock_pool_tracking(limit=2000)
        snapshot: dict[str, dict[str, Any]] = {}
        for row in rows:
            prediction = self._latest_prediction(str(row.get("stock_code") or ""))
            snapshot[str(row.get("stock_code") or "")] = {
                "stock_code": str(row.get("stock_code") or ""),
                "stock_name": str(row.get("stock_name") or ""),
                "status": str(row.get("status") or ""),
                "score": float(row.get("latest_score") or 0.0),
                "market_date": row.get("latest_market_date"),
                "direction": prediction.direction if prediction is not None else "",
                "confidence": float(prediction.confidence) if prediction is not None else 0.0,
            }
        return snapshot

    def _derive_recommendation_status(
        self,
        recommendation: Recommendation,
        daily: DailyPerformance,
        prediction: Optional[StockPrediction],
    ) -> tuple[str, str]:
        if recommendation.status == "pending_mapping":
            return "pending_mapping", "待映射真实股票代码，暂不纳入自动状态机"

        score = float(daily.evaluation_score)
        pnl_percent = float(daily.pnl_percent)
        direction = prediction.direction if prediction is not None else "sideways"
        confidence = float(prediction.confidence) if prediction is not None else 0.0
        logic_validated = bool(daily.logic_validated)

        if score < 35 and direction == "down" and confidence >= 0.55:
            return "archived", "评分偏低且AI偏空，建议退出自动重点跟踪"
        if score < 48 or pnl_percent <= -6.0 or direction == "down":
            return "risk_alert", "评分/收益转弱，进入风险观察区"
        if score >= 78 and direction == "up" and confidence >= 0.60 and logic_validated:
            return "priority", "逻辑与方向共振，提升为重点观察"
        if score < 62 or direction == "sideways":
            return "watchlist", "继续观察，等待更强催化或更明确方向"
        return "tracking", "维持常规跟踪"

    @staticmethod
    def _direction_label(direction: str) -> str:
        mapping = {
            "up": "看涨",
            "down": "看跌",
            "sideways": "震荡",
            "": "暂无",
        }
        return mapping.get((direction or "").strip().lower(), direction or "暂无")

    @staticmethod
    def _recommender_signal_label(score: float) -> str:
        if score >= 80:
            return "高胜率"
        if score >= 60:
            return "稳定输出"
        if score >= 40:
            return "持续观察"
        return "高风险"

    @staticmethod
    def _opportunity_signal_label(score: float) -> str:
        if score >= 80:
            return "重点机会"
        if score >= 65:
            return "优先跟踪"
        if score >= 50:
            return "继续观察"
        return "谨慎"

    @staticmethod
    def _status_bonus(status: str) -> float:
        mapping = {
            "priority": 8.0,
            "tracking": 4.0,
            "watchlist": -2.0,
            "risk_alert": -12.0,
            "archived": -18.0,
            "pending_mapping": -25.0,
        }
        return mapping.get((status or "").strip().lower(), 0.0)

    def _prediction_opportunity_score(self, prediction: Optional[StockPrediction]) -> float:
        if prediction is None:
            return 45.0

        confidence = self._clamp_score(float(prediction.confidence) * 100.0)
        direction = (prediction.direction or "sideways").strip().lower()
        if direction == "up":
            return 60.0 + confidence * 0.4
        if direction == "down":
            return max(5.0, 35.0 - confidence * 0.3)
        return 40.0 + confidence * 0.2

    def _build_opportunity_snapshot(
        self,
        latest_score: float,
        recommender_score: float,
        status: str,
        prediction: Optional[StockPrediction],
    ) -> dict[str, Any]:
        prediction_score = self._prediction_opportunity_score(prediction)
        raw_score = (
            float(latest_score) * 0.55
            + float(recommender_score) * 0.20
            + float(prediction_score) * 0.25
            + self._status_bonus(status)
        )
        final_score = self._clamp_score(raw_score)
        direction = prediction.direction if prediction is not None else ""
        confidence = float(prediction.confidence) if prediction is not None else 0.0
        return {
            "score": final_score,
            "signal": self._opportunity_signal_label(final_score),
            "prediction_direction": direction,
            "prediction_direction_label": self._direction_label(direction),
            "prediction_confidence": confidence,
            "prediction_date": prediction.prediction_date.isoformat() if prediction is not None else "",
            "reason": (
                f"SQS {float(latest_score):.1f} + 荐股人可靠度 {float(recommender_score):.1f} + "
                f"AI{self._direction_label(direction)}({confidence:.2f})"
            ),
        }

    def _build_conclusion_updates(
        self,
        before: dict[str, dict[str, Any]],
        after: dict[str, dict[str, Any]],
    ) -> list[str]:
        updates: list[str] = []
        for stock_code, current in after.items():
            previous = before.get(stock_code)
            stock_name = current.get("stock_name") or stock_code
            current_status = str(current.get("status") or "")
            current_score = float(current.get("score") or 0.0)
            current_direction = str(current.get("direction") or "")
            current_confidence = float(current.get("confidence") or 0.0)

            if previous is None:
                if current_status == "tracking":
                    updates.append(
                        f"{stock_name} 新进入跟踪池，当前评分 {current_score:.1f}，"
                        f"AI结论 {self._direction_label(current_direction)}"
                    )
                continue

            previous_status = str(previous.get("status") or "")
            previous_score = float(previous.get("score") or 0.0)
            previous_direction = str(previous.get("direction") or "")

            if previous_status != current_status:
                updates.append(f"{stock_name} 状态由 {previous_status} 变为 {current_status}")

            score_delta = current_score - previous_score
            if abs(score_delta) >= 8.0:
                direction_text = "上升" if score_delta > 0 else "下降"
                updates.append(
                    f"{stock_name} 评分{direction_text} {abs(score_delta):.1f} 分，当前 {current_score:.1f}"
                )

            if (
                current_direction
                and previous_direction != current_direction
                and current_confidence >= 0.45
            ):
                updates.append(
                    f"{stock_name} AI方向由 {self._direction_label(previous_direction)}"
                    f" 调整为 {self._direction_label(current_direction)}"
                    f"（置信度 {current_confidence:.2f}）"
                )

        unique_updates: list[str] = []
        seen: set[str] = set()
        for item in updates:
            if item in seen:
                continue
            seen.add(item)
            unique_updates.append(item)
        return unique_updates[:10]

    def get_last_conclusion_updates(self) -> list[str]:
        return list(self.last_conclusion_updates)

    def push_conclusion_updates(self, trading_date: date, updates: list[str]) -> None:
        if not updates:
            return
        content = "；".join(updates[:6])
        if len(updates) > 6:
            content = f"{content}；其余 {len(updates) - 6} 条请到页面查看"
        self.notifier.send(
            title=f"结论更新 {trading_date.isoformat()}",
            content=content,
        )

    def _review_predictions(self, current_date: date) -> None:
        pending = self.db.scalars(
            select(StockPrediction).where(
                StockPrediction.prediction_date == current_date,
                StockPrediction.review_result == "pending",
            )
        ).all()
        if not pending:
            return

        for item in pending:
            latest_daily = self.db.scalar(
                select(DailyPerformance)
                .join(Recommendation, Recommendation.id == DailyPerformance.recommendation_id)
                .join(Stock, Stock.id == Recommendation.stock_id)
                .where(
                    Stock.stock_code == item.stock_code,
                    DailyPerformance.date == current_date,
                )
                .order_by(desc(DailyPerformance.id))
                .limit(1)
            )
            if latest_daily is None:
                continue

            result, notes = self._evaluate_direction_result(item.direction, float(latest_daily.pnl_percent))
            item.actual_pnl_percent = float(latest_daily.pnl_percent)
            item.review_result = result
            item.review_notes = notes
            item.reviewed_at = datetime.utcnow()
            payload = self._build_prediction_review_payload(item, latest_daily)
            payload["review_summary"] = notes
            item.review_notes = json.dumps(payload, ensure_ascii=False)

    def cleanup_invalid_market_data(self) -> dict[str, int]:
        deleted_daily = 0
        deleted_predictions = 0

        daily_rows = self.db.scalars(select(DailyPerformance)).all()
        for row in daily_rows:
            if row.date.weekday() >= 5:
                self.db.delete(row)
                deleted_daily += 1

        prediction_rows = self.db.scalars(select(StockPrediction)).all()
        for row in prediction_rows:
            if row.prediction_date.weekday() >= 5:
                self.db.delete(row)
                deleted_predictions += 1

        if deleted_daily or deleted_predictions:
            self.db.commit()

        return {
            "deleted_daily": deleted_daily,
            "deleted_predictions": deleted_predictions,
        }

    def generate_daily_tracking_file(self, trading_date: date) -> str:
        report_dir = Path(self.daily_report_dir)
        report_dir.mkdir(parents=True, exist_ok=True)
        file_path = report_dir / f"{trading_date.isoformat()}.md"

        latest_reco_subquery = (
            select(
                Recommendation.stock_id.label("stock_id"),
                func.max(Recommendation.id).label("latest_recommendation_id"),
            )
            .group_by(Recommendation.stock_id)
            .subquery()
        )

        recommendations = self.db.scalars(
            select(Recommendation)
            .join(
                latest_reco_subquery,
                Recommendation.id == latest_reco_subquery.c.latest_recommendation_id,
            )
            .order_by(desc(Recommendation.id))
        ).all()

        lines: list[str] = []
        lines.append(f"# 股票池每日追踪 {trading_date.isoformat()}")
        lines.append("")

        if not recommendations:
            lines.append("暂无推荐记录")
        else:
            for recommendation in recommendations:
                stock = recommendation.stock
                recommender = recommendation.recommender
                daily = self.db.scalar(
                    select(DailyPerformance)
                    .where(
                        DailyPerformance.recommendation_id == recommendation.id,
                        DailyPerformance.date == trading_date,
                    )
                    .limit(1)
                )
                previous = self.db.scalar(
                    select(DailyPerformance)
                    .where(
                        DailyPerformance.recommendation_id == recommendation.id,
                        DailyPerformance.date < trading_date,
                    )
                    .order_by(desc(DailyPerformance.date))
                    .limit(1)
                )

                ai_analysis = self._build_stock_daily_analysis(recommendation, daily)
                prediction = self._latest_prediction(stock.stock_code)
                correction = "建议继续跟踪原逻辑"
                if daily and previous and daily.evaluation_score < previous.evaluation_score - 8:
                    correction = "评分下滑明显，建议修正逻辑假设或控制仓位"
                if recommendation.status == "pending_mapping":
                    correction = "待补充真实股票代码后再纳入自动行情评估"

                lines.extend(
                    [
                        f"## {stock.stock_code} {stock.stock_name or '-'}",
                        f"- 荐股人: {recommender.name}",
                        f"- 状态: {recommendation.status}",
                        f"- 首次接收: {recommendation.recommend_ts}",
                        f"- 原始逻辑: {recommendation.extracted_logic or recommendation.original_message}",
                        (
                            f"- 当日评分: {float(daily.evaluation_score):.1f} | 收益: {float(daily.pnl_percent):.2f}% | "
                            f"回撤: {float(daily.max_drawdown):.2f}%"
                            if daily
                            else "- 当日评分: 暂无（未进入行情评估）"
                        ),
                        f"- 智能分析: {ai_analysis}",
                        (
                            f"- AI次日预测: 方向={prediction.direction} | 置信度={prediction.confidence:.2f} | "
                            f"目标日={prediction.prediction_date} | 失效条件={prediction.invalidation_conditions}"
                            if prediction
                            else "- AI次日预测: 暂无"
                        ),
                        f"- 纠偏建议: {correction}",
                        "",
                    ]
                )

        file_path.write_text("\n".join(lines), encoding="utf-8")
        return str(file_path)

    def _get_or_create_stock(self, stock_code: str, stock_name: str = "") -> Stock:
        stmt = select(Stock).where(Stock.stock_code == stock_code)
        stock = self.db.scalar(stmt)
        if stock:
            if stock_name and not stock.stock_name:
                stock.stock_name = stock_name
            return stock
        stock = Stock(stock_code=stock_code, stock_name=stock_name)
        self.db.add(stock)
        self.db.flush()
        return stock

    def _get_or_create_recommender(self, name: str, wechat_id: str = "") -> Recommender:
        stmt = select(Recommender).where(func.lower(Recommender.name) == name.lower())
        recommender = self.db.scalar(stmt)
        if recommender:
            if wechat_id and not recommender.wechat_id:
                recommender.wechat_id = wechat_id
            return recommender
        recommender = Recommender(name=name, wechat_id=wechat_id)
        self.db.add(recommender)
        self.db.flush()
        return recommender

    def ingest_message(
        self,
        message: str,
        recommender_name: str,
        wechat_id: str = "",
        recommend_ts: Optional[datetime] = None,
        source: str = "wechat",
        deduplicate: bool = False,
    ) -> list[Recommendation]:
        parsed = self._parse_recommendations(message, recommender_name, recommend_ts)
        created: list[Recommendation] = []
        for item in parsed:
            stock_code = self._parsed_field(item, "stock_code")
            parsed_recommender_name = self._parsed_field(item, "recommender_name")
            parsed_recommend_ts = self._parsed_field(item, "recommend_ts")
            parsed_original_message = self._parsed_field(item, "original_message")
            parsed_logic = self._parsed_field(item, "extracted_logic")
            parsed_stock_name = ""
            parsed_status = "tracking"
            if isinstance(item, dict):
                parsed_stock_name = item.get("stock_name", "")
                parsed_status = item.get("status", "tracking")

            stock = self._get_or_create_stock(stock_code, stock_name=parsed_stock_name)
            recommender = self._get_or_create_recommender(parsed_recommender_name, wechat_id)

            if deduplicate and self._is_duplicate_recommendation(
                stock_id=stock.id,
                recommender_id=recommender.id,
                message=parsed_original_message,
                recommend_ts=parsed_recommend_ts,
            ):
                continue

            recommendation = Recommendation(
                stock_id=stock.id,
                recommender_id=recommender.id,
                recommend_ts=parsed_recommend_ts,
                initial_price=None,
                original_message=parsed_original_message,
                extracted_logic=parsed_logic,
                status=parsed_status,
                source=source,
            )
            self.db.add(recommendation)
            created.append(recommendation)
        self.db.commit()

        for item in created:
            self.stock_knowledge_store.append_entry(
                stock_code=item.stock.stock_code,
                stock_name=item.stock.stock_name or "",
                source=source,
                operator=item.recommender.name,
                entry_type="recommendation",
                content=item.original_message,
                ts=item.recommend_ts,
            )
        return created

    def ingest_bulk_text(
        self,
        raw_text: str,
        default_recommender_name: str = "群友",
        source: str = "manual_bulk",
    ) -> dict[str, Any]:
        records = self._extract_bulk_records(raw_text, default_recommender_name)
        result = {
            "total_records": len(records),
            "created": 0,
            "duplicates": 0,
            "ignored": 0,
            "rag_notes": 0,
            "recommendation_ids": [],
        }

        for item in records:
            message = str(item.get("message") or "").strip()
            recommender_name = str(item.get("recommender_name") or default_recommender_name).strip()
            recommend_ts = item.get("recommend_ts")

            if not message:
                result["ignored"] += 1
                continue

            parsed = self._parse_recommendations(message, recommender_name, recommend_ts)
            if not parsed:
                self._save_research_note(message, recommender_name, source, recommend_ts)
                result["rag_notes"] += 1
                result["ignored"] += 1
                continue

            created = self.ingest_message(
                message=message,
                recommender_name=recommender_name,
                recommend_ts=recommend_ts,
                source=source,
                deduplicate=True,
            )

            if created:
                result["created"] += len(created)
                result["recommendation_ids"].extend(item.id for item in created)
            else:
                result["duplicates"] += len(parsed)

        return result

    def ingest_research_text(
        self,
        text: str,
        operator_name: str = "研究员",
        source: str = "manual_research",
    ) -> dict[str, Any]:
        return self.ingest_stock_research_text(text=text, operator_name=operator_name, source=source)

    def ingest_stock_research_text(
        self,
        text: str,
        operator_name: str = "研究员",
        source: str = "manual_stock_research",
    ) -> dict[str, Any]:
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        if not lines:
            return {"saved": 0, "linked_stocks": 0, "linked_entries": 0}

        saved = 0
        linked_entries = 0
        linked_stock_codes: set[str] = set()
        for line in lines:
            self._save_research_note(line, operator_name, source, None)
            linked_stocks = self._find_stocks_in_text(line)
            for stock in linked_stocks:
                self.stock_knowledge_store.append_entry(
                    stock_code=stock.stock_code,
                    stock_name=stock.stock_name or "",
                    source=source,
                    operator=operator_name,
                    entry_type="stock_research",
                    content=line,
                )
                linked_entries += 1
                linked_stock_codes.add(stock.stock_code)
            saved += 1
        return {
            "saved": saved,
            "linked_stocks": len(linked_stock_codes),
            "linked_entries": linked_entries,
        }

    def ingest_macro_research_text(
        self,
        text: str,
        operator_name: str = "研究员",
        source: str = "manual_macro_research",
    ) -> dict[str, Any]:
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        if not lines:
            return {"saved": 0}

        saved = 0
        for line in lines:
            self._save_research_note(line, operator_name, source, None)
            saved += 1
        return {"saved": saved}

    def add_manual_recommendation(
        self,
        stock_code: str,
        logic: str,
        recommender_name: str,
        stock_name: str = "",
        wechat_id: str = "",
        recommend_ts: Optional[datetime] = None,
    ) -> Recommendation:
        stock = self._get_or_create_stock(stock_code, stock_name=stock_name)
        recommender = self._get_or_create_recommender(recommender_name, wechat_id)
        recommendation = Recommendation(
            stock_id=stock.id,
            recommender_id=recommender.id,
            recommend_ts=recommend_ts or datetime.utcnow(),
            original_message=logic,
            extracted_logic=logic,
            source="manual",
        )
        self.db.add(recommendation)
        self.db.commit()
        self.db.refresh(recommendation)
        return recommendation

    def evaluate_recommendation(
        self,
        recommendation_id: int,
        close_price: float,
        high_price: float,
        low_price: float,
        pnl_percent: float,
        max_drawdown: float,
        sharpe_ratio: float,
        logic_validated: bool,
        market_cap_score: float,
        elasticity_score: float,
        liquidity_score: float,
        daily_date: date,
        notes: str = "",
    ) -> DailyPerformance:
        recommendation = self.db.get(Recommendation, recommendation_id)
        if recommendation is None:
            raise ValueError("recommendation not found")

        metrics = DailyMarketMetrics(
            pnl_percent=pnl_percent,
            peak_pnl_percent=max(pnl_percent, 0.0),
            sharpe_ratio=sharpe_ratio,
            max_drawdown=max_drawdown,
            logic_validated=logic_validated,
            market_cap_score=market_cap_score,
            elasticity_score=elasticity_score,
            liquidity_score=liquidity_score,
        )
        score, analysis = compute_stock_quality_score(metrics)

        existing = self.db.scalar(
            select(DailyPerformance).where(
                DailyPerformance.recommendation_id == recommendation_id,
                DailyPerformance.date == daily_date,
            )
        )
        if existing is None:
            daily = DailyPerformance(
                recommendation_id=recommendation_id,
                date=daily_date,
                close_price=close_price,
                high_price=high_price,
                low_price=low_price,
                pnl_percent=pnl_percent,
                max_drawdown=max_drawdown,
                evaluation_score=score,
                sharpe_ratio=sharpe_ratio,
                logic_validated=logic_validated,
                market_cap_score=market_cap_score,
                elasticity_score=elasticity_score,
                liquidity_score=liquidity_score,
                notes=f"{analysis} {notes}".strip(),
            )
            self.db.add(daily)
        else:
            daily = existing
            daily.close_price = close_price
            daily.high_price = high_price
            daily.low_price = low_price
            daily.pnl_percent = pnl_percent
            daily.max_drawdown = max_drawdown
            daily.evaluation_score = score
            daily.sharpe_ratio = sharpe_ratio
            daily.logic_validated = logic_validated
            daily.market_cap_score = market_cap_score
            daily.elasticity_score = elasticity_score
            daily.liquidity_score = liquidity_score
            daily.notes = f"{analysis} {notes}".strip()

        self.db.commit()
        self.db.refresh(daily)
        self._trigger_alert_if_needed(recommendation.stock.stock_code, daily)
        return daily

    def evaluate_all_recommendations(self, trading_date: Optional[date] = None) -> int:
        requested_date = trading_date or date.today()
        before_snapshot = self._capture_tracking_snapshot()
        recommendations = self.db.scalars(
            select(Recommendation).where(Recommendation.status.in_(self.ACTIVE_RECOMMENDATION_STATUSES))
        ).all()
        count = 0
        effective_market_date: Optional[date] = None
        for recommendation in recommendations:
            if not recommendation.stock.stock_name and hasattr(self.market_provider, "get_stock_name"):
                try:
                    stock_name = self.market_provider.get_stock_name(recommendation.stock.stock_code)
                    if stock_name:
                        recommendation.stock.stock_name = stock_name
                        self.db.commit()
                except Exception:
                    pass

            snapshot = self.market_provider.get_daily_snapshot(recommendation.stock.stock_code, requested_date)
            market_date = snapshot.snapshot_date
            effective_market_date = market_date if effective_market_date is None else max(effective_market_date, market_date)
            try:
                logic_validated = self.news_provider.validate_recommendation_logic(
                    recommendation.stock.stock_code,
                    recommendation.extracted_logic,
                    market_date,
                )
            except Exception:
                logic_validated = False
            self.evaluate_recommendation(
                recommendation_id=recommendation.id,
                close_price=snapshot.close_price,
                high_price=snapshot.high_price,
                low_price=snapshot.low_price,
                pnl_percent=snapshot.pnl_percent,
                max_drawdown=snapshot.max_drawdown,
                sharpe_ratio=snapshot.sharpe_ratio,
                logic_validated=logic_validated,
                market_cap_score=snapshot.market_cap_score,
                elasticity_score=snapshot.elasticity_score,
                liquidity_score=snapshot.liquidity_score,
                daily_date=market_date,
            )
            latest_daily = self.db.scalar(
                select(DailyPerformance)
                .where(
                    DailyPerformance.recommendation_id == recommendation.id,
                    DailyPerformance.date == market_date,
                )
                .limit(1)
            )
            if latest_daily is not None:
                ai_analysis = self._build_stock_daily_analysis(recommendation, latest_daily)
                latest_daily.notes = self._compose_tracking_notes(latest_daily.notes, ai_analysis)
                prediction = self._upsert_ai_prediction(
                    recommendation=recommendation,
                    daily=latest_daily,
                    prediction_date=self._next_trade_day(market_date),
                )
                next_status, status_reason = self._derive_recommendation_status(
                    recommendation=recommendation,
                    daily=latest_daily,
                    prediction=prediction,
                )
                recommendation.status = next_status
                latest_daily.notes = self._compose_tracking_notes(
                    latest_daily.notes,
                    f"状态机结论：{status_reason}",
                )
            count += 1
        self.db.commit()
        if effective_market_date is not None:
            self._review_predictions(effective_market_date)
            self.db.commit()
        self.refresh_recommender_scores()
        report_date = effective_market_date or requested_date
        self.generate_daily_tracking_file(report_date)
        after_snapshot = self._capture_tracking_snapshot()
        self.last_conclusion_updates = self._build_conclusion_updates(before_snapshot, after_snapshot)
        self.push_conclusion_updates(report_date, self.last_conclusion_updates)
        self.push_daily_report(report_date)
        return count

    def get_stock_pool_tracking(self, limit: int = 200) -> List[Dict[str, Any]]:
        first_seen_subquery = (
            select(
                Recommendation.stock_id.label("stock_id"),
                func.min(Recommendation.recommend_ts).label("first_seen"),
            )
            .group_by(Recommendation.stock_id)
            .subquery()
        )

        latest_recommendation_subquery = (
            select(
                Recommendation.stock_id.label("stock_id"),
                func.max(Recommendation.id).label("latest_recommendation_id"),
            )
            .group_by(Recommendation.stock_id)
            .subquery()
        )

        latest_daily_subquery = (
            select(
                DailyPerformance.recommendation_id,
                func.max(DailyPerformance.date).label("latest_date"),
            )
            .group_by(DailyPerformance.recommendation_id)
            .subquery()
        )

        rows = self.db.execute(
            select(Stock, Recommendation, Recommender, DailyPerformance, first_seen_subquery.c.first_seen)
            .join(first_seen_subquery, first_seen_subquery.c.stock_id == Stock.id)
            .join(
                latest_recommendation_subquery,
                latest_recommendation_subquery.c.stock_id == Stock.id,
            )
            .join(Recommendation, Recommendation.id == latest_recommendation_subquery.c.latest_recommendation_id)
            .join(Recommender, Recommender.id == Recommendation.recommender_id)
            .join(
                latest_daily_subquery,
                latest_daily_subquery.c.recommendation_id == Recommendation.id,
                isouter=True,
            )
            .join(
                DailyPerformance,
                (DailyPerformance.recommendation_id == Recommendation.id)
                & (DailyPerformance.date == latest_daily_subquery.c.latest_date),
                isouter=True,
            )
            .order_by(first_seen_subquery.c.first_seen.desc())
            .limit(limit)
        ).all()

        result: List[Dict[str, Any]] = []
        for row in rows:
            stock: Stock = row[0]
            recommendation: Recommendation = row[1]
            recommender: Recommender = row[2]
            latest_daily: Optional[DailyPerformance] = row[3]
            first_seen: datetime = row[4]
            parsed_notes = self._split_tracking_notes(latest_daily.notes if latest_daily else "")
            prediction = self._latest_prediction(stock.stock_code)
            opportunity = self._build_opportunity_snapshot(
                latest_score=float(latest_daily.evaluation_score) if latest_daily else 0.0,
                recommender_score=float(recommender.reliability_score),
                status=recommendation.status,
                prediction=prediction,
            )
            result.append(
                {
                    "stock_code": stock.stock_code,
                    "stock_name": stock.stock_name or "",
                    "recommender_name": recommender.name,
                    "recommender_score": float(recommender.reliability_score),
                    "first_seen": first_seen,
                    "status": recommendation.status,
                    "logic": recommendation.extracted_logic,
                    "latest_date": latest_daily.date if latest_daily else None,
                    "latest_market_date": latest_daily.date if latest_daily else None,
                    "latest_score": float(latest_daily.evaluation_score) if latest_daily else 0.0,
                    "latest_pnl": float(latest_daily.pnl_percent) if latest_daily else 0.0,
                    "latest_notes": latest_daily.notes if latest_daily else "",
                    "latest_sqs_breakdown": parsed_notes["sqs_breakdown"],
                    "latest_ai_analysis": parsed_notes["ai_analysis"],
                    "prediction_direction": opportunity["prediction_direction"],
                    "prediction_direction_label": opportunity["prediction_direction_label"],
                    "prediction_confidence": opportunity["prediction_confidence"],
                    "prediction_date": opportunity["prediction_date"],
                    "opportunity_score": opportunity["score"],
                    "opportunity_signal": opportunity["signal"],
                    "opportunity_reason": opportunity["reason"],
                }
            )
        return result

    def list_opportunity_stocks(self, limit: int = 20) -> List[Dict[str, Any]]:
        rows = self.get_stock_pool_tracking(limit=max(limit * 4, 50))
        ranked = sorted(
            rows,
            key=lambda item: (
                float(item.get("opportunity_score") or 0.0),
                float(item.get("latest_score") or 0.0),
                float(item.get("prediction_confidence") or 0.0),
                float(item.get("recommender_score") or 0.0),
            ),
            reverse=True,
        )
        result: List[Dict[str, Any]] = []
        for index, row in enumerate(ranked[:limit], start=1):
            item = dict(row)
            item["rank"] = index
            result.append(item)
        return result

    def get_daily_tracking_records(self, limit: int = 500) -> List[Dict[str, Any]]:
        rows = self.db.execute(
            select(DailyPerformance, Recommendation, Stock, Recommender)
            .join(Recommendation, Recommendation.id == DailyPerformance.recommendation_id)
            .join(Stock, Stock.id == Recommendation.stock_id)
            .join(Recommender, Recommender.id == Recommendation.recommender_id)
            .order_by(
                DailyPerformance.date.desc(),
                Recommendation.recommend_ts.desc(),
                DailyPerformance.id.desc(),
            )
            .limit(max(limit * 6, 100))
        ).all()

        grouped: dict[tuple[date, str], dict[str, Any]] = {}
        ordered_keys: list[tuple[date, str]] = []
        for daily, recommendation, stock, recommender in rows:
            key = (daily.date, stock.stock_code)
            parsed_notes = self._split_tracking_notes(daily.notes)
            if key not in grouped:
                grouped[key] = {
                    "date": daily.date,
                    "market_date": daily.date,
                    "stock_code": stock.stock_code,
                    "stock_name": stock.stock_name or "",
                    "recommender_name": recommender.name,
                    "recommender_names": [recommender.name],
                    "evaluation_score": float(daily.evaluation_score),
                    "pnl_percent": float(daily.pnl_percent),
                    "logic": recommendation.extracted_logic,
                    "logic_versions": [recommendation.extracted_logic] if recommendation.extracted_logic else [],
                    "daily_notes": daily.notes,
                    "daily_sqs_breakdown": parsed_notes["sqs_breakdown"],
                    "daily_ai_analysis": parsed_notes["ai_analysis"],
                    "duplicate_count": 1,
                }
                ordered_keys.append(key)
            else:
                grouped[key]["duplicate_count"] += 1
                if recommender.name not in grouped[key]["recommender_names"]:
                    grouped[key]["recommender_names"].append(recommender.name)
                if recommendation.extracted_logic and recommendation.extracted_logic not in grouped[key]["logic_versions"]:
                    grouped[key]["logic_versions"].append(recommendation.extracted_logic)

            grouped[key]["recommender_name"] = " / ".join(grouped[key]["recommender_names"])

            if len(ordered_keys) >= limit and key not in ordered_keys[:limit]:
                break

        return [grouped[key] for key in ordered_keys[:limit]]

    def get_recent_macro_research(self, limit: int = 10) -> List[Dict[str, Any]]:
        return self.rag_store.list_recent(limit=limit, source_prefix="manual_macro_research")

    def get_recent_stock_research_feed(self, limit: int = 10) -> List[Dict[str, Any]]:
        return self.stock_knowledge_store.list_recent_entries(
            limit=limit,
            entry_types={"stock_research", "research", "promotion", "recommendation"},
        )

    def get_llm_usage_summary(self, hours: int = 24) -> dict[str, Any]:
        return self.llm_usage_store.summarize_recent(hours=hours)

    @staticmethod
    def _latest_trade_day_for_intraday(base_day: Optional[date] = None) -> date:
        trading_day = base_day or date.today()
        while trading_day.weekday() >= 5:
            trading_day = date.fromordinal(trading_day.toordinal() - 1)
        return trading_day

    @staticmethod
    def _extract_intraday_trading_day(timestamp_text: str, fallback_day: Optional[date] = None) -> str:
        text = (timestamp_text or "").strip()
        if len(text) >= 10 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", text[:10]):
            return text[:10]
        return FinanceAgentService._latest_trade_day_for_intraday(fallback_day).isoformat()

    def save_intraday_bars(
        self,
        stock_code: str,
        bars: list[IntradayBar],
        period: str = "1",
        adjust: str = "",
        source: str = "akshare",
        used_cache: bool = False,
    ) -> dict[str, Any]:
        self._ensure_intraday_tables()
        stock = self.db.scalar(select(Stock).where(Stock.stock_code == stock_code))
        stock_name = stock.stock_name if stock is not None else ""

        existing_rows = self.db.scalars(
            select(IntradayBarRecord).where(
                IntradayBarRecord.stock_code == stock_code,
                IntradayBarRecord.period == period,
                IntradayBarRecord.adjust == adjust,
            )
        ).all()
        existing_by_timestamp = {row.timestamp: row for row in existing_rows}

        inserted = 0
        updated = 0
        latest_timestamp = ""
        for item in bars:
            if not item.timestamp:
                continue
            trading_day = self._extract_intraday_trading_day(item.timestamp)
            row = existing_by_timestamp.get(item.timestamp)
            if row is None:
                row = IntradayBarRecord(
                    stock_code=stock_code,
                    stock_name=stock_name,
                    period=period,
                    adjust=adjust,
                    timestamp=item.timestamp,
                )
                self.db.add(row)
                inserted += 1
            else:
                updated += 1

            row.stock_name = stock_name
            row.trading_day = trading_day
            row.open_price = float(item.open_price)
            row.close_price = float(item.close_price)
            row.high_price = float(item.high_price)
            row.low_price = float(item.low_price)
            row.volume = float(item.volume)
            row.amount = float(item.amount)
            row.amplitude = float(item.amplitude)
            row.change_percent = float(item.change_percent)
            row.change_amount = float(item.change_amount)
            row.turnover_rate = float(item.turnover_rate)
            row.source = source
            row.used_cache = bool(used_cache)
            latest_timestamp = max(latest_timestamp, item.timestamp)

        self.db.commit()
        return {
            "saved": inserted + updated,
            "inserted": inserted,
            "updated": updated,
            "latest_timestamp": latest_timestamp,
        }

    def save_intraday_ticks(
        self,
        stock_code: str,
        trades: list[IntradayTrade],
        source: str = "akshare",
        used_cache: bool = False,
        trading_day: Optional[date] = None,
    ) -> dict[str, Any]:
        self._ensure_intraday_tables()
        effective_day = self._latest_trade_day_for_intraday(trading_day)
        trading_day_text = effective_day.isoformat()
        stock = self.db.scalar(select(Stock).where(Stock.stock_code == stock_code))
        stock_name = stock.stock_name if stock is not None else ""

        self.db.execute(
            delete(IntradayTradeTick).where(
                IntradayTradeTick.stock_code == stock_code,
                IntradayTradeTick.trading_day == trading_day_text,
            )
        )

        latest_timestamp = ""
        saved = 0
        for index, item in enumerate(trades):
            row = IntradayTradeTick(
                stock_code=stock_code,
                stock_name=stock_name,
                trading_day=trading_day_text,
                row_index=index,
                timestamp=item.timestamp,
                price=float(item.price),
                volume_lot=float(item.volume_lot),
                side=item.side or "",
                source=source,
                used_cache=bool(used_cache),
            )
            self.db.add(row)
            saved += 1
            latest_timestamp = max(latest_timestamp, item.timestamp)

        self.db.commit()
        return {
            "saved": saved,
            "latest_timestamp": latest_timestamp,
            "trading_day": trading_day_text,
        }

    def list_intraday_bars_from_storage(
        self,
        stock_code: str,
        period: str = "1",
        adjust: str = "",
        limit: Optional[int] = 240,
    ) -> list[IntradayBar]:
        self._ensure_intraday_tables()
        latest_trading_day = self.db.scalar(
            select(IntradayBarRecord.trading_day)
            .where(
                IntradayBarRecord.stock_code == stock_code,
                IntradayBarRecord.period == period,
                IntradayBarRecord.adjust == adjust,
            )
            .order_by(IntradayBarRecord.trading_day.desc())
            .limit(1)
        )
        if latest_trading_day is None:
            return []

        stmt = (
            select(IntradayBarRecord)
            .where(
                IntradayBarRecord.stock_code == stock_code,
                IntradayBarRecord.period == period,
                IntradayBarRecord.adjust == adjust,
                IntradayBarRecord.trading_day == latest_trading_day,
            )
            .order_by(IntradayBarRecord.timestamp.desc())
        )
        if limit is not None:
            stmt = stmt.limit(max(limit, 1))
        rows = self.db.scalars(
            stmt
        ).all()
        rows = list(reversed(rows))
        return [
            IntradayBar(
                timestamp=row.timestamp,
                open_price=float(row.open_price),
                close_price=float(row.close_price),
                high_price=float(row.high_price),
                low_price=float(row.low_price),
                volume=float(row.volume),
                amount=float(row.amount),
                amplitude=float(row.amplitude),
                change_percent=float(row.change_percent),
                change_amount=float(row.change_amount),
                turnover_rate=float(row.turnover_rate),
            )
            for row in rows
        ]

    def list_intraday_ticks_from_storage(
        self,
        stock_code: str,
        limit: Optional[int] = 120,
    ) -> list[IntradayTrade]:
        self._ensure_intraday_tables()
        latest_day = self.db.scalar(
            select(IntradayTradeTick.trading_day)
            .where(IntradayTradeTick.stock_code == stock_code)
            .order_by(IntradayTradeTick.trading_day.desc())
            .limit(1)
        )
        if latest_day is None:
            return []

        stmt = (
            select(IntradayTradeTick)
            .where(
                IntradayTradeTick.stock_code == stock_code,
                IntradayTradeTick.trading_day == latest_day,
            )
            .order_by(IntradayTradeTick.row_index.asc())
        )
        if limit is not None:
            stmt = stmt.limit(max(limit, 1))

        rows = self.db.scalars(stmt).all()
        return [
            IntradayTrade(
                timestamp=row.timestamp,
                price=float(row.price),
                volume_lot=float(row.volume_lot),
                side=row.side or "",
            )
            for row in rows
        ]

    def get_intraday_storage_summary(self, stock_code: str, period: str = "1", adjust: str = "") -> dict[str, Any]:
        self._ensure_intraday_tables()
        latest_trading_day = self.db.scalar(
            select(IntradayBarRecord.trading_day)
            .where(
                IntradayBarRecord.stock_code == stock_code,
                IntradayBarRecord.period == period,
                IntradayBarRecord.adjust == adjust,
            )
            .order_by(IntradayBarRecord.trading_day.desc())
            .limit(1)
        )
        latest_bar = self.db.scalar(
            select(IntradayBarRecord)
            .where(
                IntradayBarRecord.stock_code == stock_code,
                IntradayBarRecord.period == period,
                IntradayBarRecord.adjust == adjust,
                IntradayBarRecord.trading_day == latest_trading_day,
            )
            .order_by(IntradayBarRecord.timestamp.desc())
            .limit(1)
        )
        latest_tick = self.db.scalar(
            select(IntradayTradeTick)
            .where(IntradayTradeTick.stock_code == stock_code)
            .order_by(IntradayTradeTick.trading_day.desc(), IntradayTradeTick.row_index.desc())
            .limit(1)
        )
        bar_count = self.db.scalar(
            select(func.count()).select_from(IntradayBarRecord).where(
                IntradayBarRecord.stock_code == stock_code,
                IntradayBarRecord.period == period,
                IntradayBarRecord.adjust == adjust,
                IntradayBarRecord.trading_day == latest_trading_day,
            )
        )
        tick_count = self.db.scalar(
            select(func.count()).select_from(IntradayTradeTick).where(
                IntradayTradeTick.stock_code == stock_code,
                IntradayTradeTick.trading_day == (latest_bar.trading_day if latest_bar is not None else None),
            )
        )
        return {
            "bar_count": int(bar_count or 0),
            "tick_count": int(tick_count or 0),
            "latest_bar_timestamp": latest_bar.timestamp if latest_bar is not None else "",
            "latest_tick_timestamp": latest_tick.timestamp if latest_tick is not None else "",
            "latest_trading_day": latest_bar.trading_day if latest_bar is not None else "",
            "period": period,
            "adjust": adjust,
        }

    def list_intraday_sync_candidates(self, limit: int = 20, statuses: Optional[list[str]] = None) -> list[dict[str, str]]:
        active_statuses = statuses or sorted(self.ACTIVE_RECOMMENDATION_STATUSES)
        rows = self.db.execute(
            select(Stock.stock_code, Stock.stock_name)
            .join(Recommendation, Recommendation.stock_id == Stock.id)
            .where(Recommendation.status.in_(active_statuses))
            .distinct()
            .order_by(Stock.stock_code.asc())
            .limit(max(limit, 1))
        ).all()
        return [{"stock_code": code, "stock_name": name or ""} for code, name in rows]

    def get_stock_detail(self, stock_code: str) -> Optional[Dict[str, Any]]:
        stock = self.db.scalar(select(Stock).where(Stock.stock_code == stock_code))
        if stock is None:
            return None

        realtime_refresh: dict[str, Any] = {"refreshed": False, "reason": "not_run"}
        try:
            realtime_refresh = self.refresh_stock_realtime_context(stock_code=stock.stock_code)
        except Exception as exc:
            realtime_refresh = {"refreshed": False, "reason": f"refresh_failed: {exc}"}

        recommendations = self.db.execute(
            select(Recommendation, Recommender)
            .join(Recommender, Recommender.id == Recommendation.recommender_id)
            .where(Recommendation.stock_id == stock.id)
            .order_by(Recommendation.recommend_ts.desc(), Recommendation.id.desc())
        ).all()

        daily_rows = self._fetch_stock_daily_rows(stock.id)

        daily_history: list[Dict[str, Any]] = []
        grouped_daily: dict[date, dict[str, Any]] = {}
        for daily, recommendation, recommender in daily_rows:
            parsed_notes = self._split_tracking_notes(daily.notes)
            item = grouped_daily.get(daily.date)
            if item is None:
                item = {
                    "market_date": daily.date,
                    "close_price": float(daily.close_price),
                    "evaluation_score": float(daily.evaluation_score),
                    "pnl_percent": float(daily.pnl_percent),
                    "max_drawdown": float(daily.max_drawdown),
                    "recommender_names": [recommender.name],
                    "logic_versions": [recommendation.extracted_logic] if recommendation.extracted_logic else [],
                    "sqs_breakdown": parsed_notes["sqs_breakdown"],
                    "ai_analysis": parsed_notes["ai_analysis"],
                    "raw_notes": daily.notes,
                    "duplicate_count": 1,
                }
                grouped_daily[daily.date] = item
                daily_history.append(item)
            else:
                item["duplicate_count"] += 1
                if recommender.name not in item["recommender_names"]:
                    item["recommender_names"].append(recommender.name)
                if recommendation.extracted_logic and recommendation.extracted_logic not in item["logic_versions"]:
                    item["logic_versions"].append(recommendation.extracted_logic)

        latest_recommendation = recommendations[0][0] if recommendations else None
        latest_daily = daily_history[0] if daily_history else None
        prediction = self._latest_prediction(stock_code)

        prediction_payload = None
        if prediction is not None:
            try:
                risk_flags = json.loads(prediction.risk_flags or "[]")
            except json.JSONDecodeError:
                risk_flags = []
            try:
                evidence = json.loads(prediction.evidence or "[]")
            except json.JSONDecodeError:
                evidence = []
            review_tags: list[str] = []
            review_summary = prediction.review_notes
            try:
                review_payload = json.loads(prediction.review_notes or "{}")
                if isinstance(review_payload, dict):
                    review_tags = [str(item) for item in review_payload.get("review_tags") or []]
                    review_summary = str(review_payload.get("review_summary") or review_summary)
            except json.JSONDecodeError:
                review_payload = {}
            prediction_payload = {
                "prediction_date": prediction.prediction_date,
                "direction": prediction.direction,
                "confidence": float(prediction.confidence),
                "thesis": prediction.thesis,
                "invalidation_conditions": prediction.invalidation_conditions,
                "risk_flags": risk_flags,
                "evidence": evidence,
                "predicted_by": prediction.predicted_by,
                "review_result": prediction.review_result,
                "review_summary": review_summary,
                "review_tags": review_tags,
                "actual_pnl_percent": prediction.actual_pnl_percent,
            }

        latest_source_quality = self._build_source_quality_summary(
            stock_entries=self.stock_knowledge_store.search_entries(
                stock_code=stock.stock_code,
                stock_name=stock.stock_name or "",
                limit=12,
            ),
            macro_notes=self.get_recent_macro_research(limit=6),
            recent_recommendations=recommendations[:6],
        )

        intraday_bars = self.list_intraday_bars_from_storage(stock.stock_code, limit=240)
        intraday_ticks = self.list_intraday_ticks_from_storage(stock.stock_code, limit=120)
        intraday_ticks_full = self.list_intraday_ticks_from_storage(stock.stock_code, limit=None)
        intraday_reference_price, intraday_reference_source = self._resolve_intraday_reference_price(
            stock,
            intraday_bars,
            daily_rows,
        )
        latest_intraday_maintenance = self.get_latest_stock_daily_maintenance(stock.stock_code)
        if intraday_bars:
            rebuilt_maintenance = self._build_intraday_maintenance_snapshot(
                stock=stock,
                bars=intraday_bars,
                trades=intraday_ticks_full,
                reference_price=intraday_reference_price,
                reference_source=intraday_reference_source,
                data_source=str((latest_intraday_maintenance or {}).get("data_source") or "storage_snapshot"),
            )
            if rebuilt_maintenance is not None:
                rebuilt_market_date = rebuilt_maintenance["market_date"].isoformat()
                latest_intraday_maintenance_market_date = str((latest_intraday_maintenance or {}).get("market_date") or "")
                latest_intraday_maintenance_reference_price = float((latest_intraday_maintenance or {}).get("reference_price") or 0.0)
                latest_intraday_maintenance_reference_source = str((latest_intraday_maintenance or {}).get("reference_source") or "")
                latest_intraday_maintenance_latest_bar_timestamp = str((latest_intraday_maintenance or {}).get("latest_bar_timestamp") or "")
                rebuilt_latest_bar_timestamp = str(rebuilt_maintenance.get("latest_bar_timestamp") or "")
                if (
                    latest_intraday_maintenance is None
                    or latest_intraday_maintenance_market_date != rebuilt_market_date
                    or abs(latest_intraday_maintenance_reference_price - float(intraday_reference_price or 0.0)) > 1e-6
                    or latest_intraday_maintenance_reference_source != intraday_reference_source
                    or latest_intraday_maintenance_latest_bar_timestamp != rebuilt_latest_bar_timestamp
                ):
                    latest_intraday_maintenance = self.upsert_stock_daily_maintenance(rebuilt_maintenance)
                else:
                    latest_intraday_maintenance = latest_intraday_maintenance or rebuilt_maintenance
        latest_intraday_maintenance = self.build_intraday_agent_analysis(
            stock=stock,
            latest_recommendation=latest_recommendation,
            latest_daily=latest_daily,
            maintenance=latest_intraday_maintenance,
        )

        return {
            "stock_code": stock.stock_code,
            "stock_name": stock.stock_name or "",
            "industry": stock.industry or "",
            "first_seen": recommendations[-1][0].recommend_ts if recommendations else None,
            "latest_recommend_ts": latest_recommendation.recommend_ts if latest_recommendation else None,
            "latest_logic": latest_recommendation.extracted_logic if latest_recommendation else "",
            "latest_status": latest_recommendation.status if latest_recommendation else "",
            "contributors": list(dict.fromkeys(item[1].name for item in recommendations)),
            "recommendations": [
                {
                    "id": recommendation.id,
                    "recommend_ts": recommendation.recommend_ts,
                    "recommender_name": recommender.name,
                    "source": recommendation.source,
                    "status": recommendation.status,
                    "logic": recommendation.extracted_logic,
                    "original_message": recommendation.original_message,
                }
                for recommendation, recommender in recommendations
            ],
            "latest_daily": latest_daily,
            "daily_history": daily_history[:20],
            "knowledge_entries": self.stock_knowledge_store.search_entries(
                stock_code=stock.stock_code,
                stock_name=stock.stock_name or "",
                limit=12,
            ),
            "source_quality_summary": latest_source_quality,
            "related_notes": self.rag_store.search_with_metadata(
                stock_code=stock.stock_code,
                stock_name=stock.stock_name or "",
                limit=8,
            ),
            "recent_macro_notes": self.get_recent_macro_research(limit=6),
            "prediction": prediction_payload,
            "intraday_summary": self.get_intraday_storage_summary(stock.stock_code),
            "intraday_refresh": realtime_refresh,
            "stock_daily_maintenance": latest_intraday_maintenance,
            "intraday_agent_analysis": (latest_intraday_maintenance or {}).get("agent_intraday_analysis", ""),
            "intraday_reference_price": intraday_reference_price,
            "intraday_reference_source": intraday_reference_source,
            "intraday_bars": [
                {
                    "timestamp": item.timestamp,
                    "open_price": item.open_price,
                    "close_price": item.close_price,
                    "high_price": item.high_price,
                    "low_price": item.low_price,
                    "volume": item.volume,
                    "amount": item.amount,
                    "amplitude": item.amplitude,
                    "change_percent": item.change_percent,
                    "change_amount": item.change_amount,
                    "turnover_rate": item.turnover_rate,
                }
                for item in intraday_bars
            ],
            "intraday_ticks": [
                {
                    "timestamp": item.timestamp,
                    "price": item.price,
                    "volume_lot": item.volume_lot,
                    "side": item.side,
                }
                for item in intraday_ticks
            ],
        }

    def refresh_recommender_scores(self) -> None:
        recommenders = self.db.scalars(select(Recommender)).all()
        current_date = date.today()

        for recommender in recommenders:
            outcomes: list[RecommendationOutcome] = []
            recommendations = self.db.scalars(
                select(Recommendation).where(Recommendation.recommender_id == recommender.id)
            ).all()
            for recommendation in recommendations:
                latest_daily = self.db.scalar(
                    select(DailyPerformance)
                    .where(DailyPerformance.recommendation_id == recommendation.id)
                    .order_by(desc(DailyPerformance.date))
                    .limit(1)
                )
                if latest_daily is None:
                    continue
                outcomes.append(
                    RecommendationOutcome(
                        return_percent=latest_daily.pnl_percent,
                        max_drawdown=latest_daily.max_drawdown,
                        days_ago=max((current_date - recommendation.recommend_ts.date()).days, 0),
                    )
                )

            score, _ = compute_recommender_reliability(outcomes)
            recommender.reliability_score = score
        self.db.commit()

    def subscribe_alert(self, stock_code: str, subscriber: str) -> AlertSubscription:
        exists = self.db.scalar(
            select(AlertSubscription).where(
                AlertSubscription.stock_code == stock_code,
                AlertSubscription.subscriber == subscriber,
            )
        )
        if exists:
            exists.is_active = True
            self.db.commit()
            self.db.refresh(exists)
            return exists

        alert = AlertSubscription(stock_code=stock_code, subscriber=subscriber, is_active=True)
        self.db.add(alert)
        self.db.commit()
        self.db.refresh(alert)
        return alert

    def list_top_stocks(self, limit: int = 10, reverse: bool = True) -> list[tuple[Stock, DailyPerformance]]:
        latest_subquery = (
            select(
                DailyPerformance.recommendation_id,
                func.max(DailyPerformance.date).label("latest_date"),
            )
            .group_by(DailyPerformance.recommendation_id)
            .subquery()
        )

        rows = self.db.execute(
            select(Stock, DailyPerformance)
            .join(Recommendation, Recommendation.stock_id == Stock.id)
            .join(
                latest_subquery,
                latest_subquery.c.recommendation_id == Recommendation.id,
            )
            .join(
                DailyPerformance,
                (DailyPerformance.recommendation_id == Recommendation.id)
                & (DailyPerformance.date == latest_subquery.c.latest_date),
            )
            .order_by(DailyPerformance.evaluation_score.desc() if reverse else DailyPerformance.evaluation_score.asc())
            .limit(limit)
        ).all()
        return [(row[0], row[1]) for row in rows]

    def get_stock_status(self, stock_code: str) -> str:
        stock = self.db.scalar(select(Stock).where(Stock.stock_code == stock_code))
        if stock is None:
            return f"未找到股票 {stock_code}"
        recommendation = self.db.scalar(
            select(Recommendation).where(Recommendation.stock_id == stock.id).order_by(desc(Recommendation.id)).limit(1)
        )
        if recommendation is None:
            return f"{stock_code} 尚无推荐记录"
        latest_daily = self.db.scalar(
            select(DailyPerformance)
            .where(DailyPerformance.recommendation_id == recommendation.id)
            .order_by(desc(DailyPerformance.date))
            .limit(1)
        )
        if latest_daily is None:
            return f"{stock_code} 已在池中，但暂无日评估数据"

        prediction = self._latest_prediction(stock_code)
        prediction_text = "暂无AI预测"
        if prediction is not None:
            prediction_text = (
                f"AI预测[{prediction.prediction_date}] {prediction.direction}"
                f"(conf={prediction.confidence:.2f})"
            )
        return (
            f"{stock_code} 最新评分 {latest_daily.evaluation_score:.1f}，"
            f"当前收益 {latest_daily.pnl_percent:.2f}% ，"
            f"最大回撤 {latest_daily.max_drawdown:.2f}% 。{prediction_text}"
        )

    def get_recommender_status(self, name: str) -> str:
        recommender = self.db.scalar(
            select(Recommender).where(func.lower(Recommender.name) == name.lower())
        )
        if recommender is None:
            return f"未找到荐股人 {name}"
        count = self.db.scalar(
            select(func.count(Recommendation.id)).where(Recommendation.recommender_id == recommender.id)
        )
        return (
            f"{recommender.name} 可靠性评分 {recommender.reliability_score:.1f}，"
            f"历史推荐 {count} 条。"
        )

    def get_dashboard_metrics(self) -> dict[str, float]:
        stock_pool_size = self.db.scalar(select(func.count(Stock.id))) or 0
        recommender_count = self.db.scalar(select(func.count(Recommender.id))) or 0
        avg_stock_score = self.db.scalar(select(func.avg(DailyPerformance.evaluation_score))) or 0.0
        avg_reliability_score = self.db.scalar(select(func.avg(Recommender.reliability_score))) or 0.0
        return {
            "stock_pool_size": int(stock_pool_size),
            "recommender_count": int(recommender_count),
            "avg_stock_score": float(avg_stock_score),
            "avg_reliability_score": float(avg_reliability_score),
        }

    def get_recommender_list(self) -> List[Dict[str, Any]]:
        rows = self.db.execute(
            select(Recommender, func.count(Recommendation.id))
            .join(Recommendation, Recommendation.recommender_id == Recommender.id, isouter=True)
            .group_by(Recommender.id)
            .order_by(Recommender.reliability_score.desc(), func.count(Recommendation.id).desc(), Recommender.name.asc())
        ).all()
        result: List[Dict[str, Any]] = []
        for index, row in enumerate(rows, start=1):
            score = float(row[0].reliability_score)
            result.append(
                {
                    "rank": index,
                    "name": row[0].name,
                    "reliability_score": score,
                    "recommendation_count": int(row[1]),
                    "signal_label": self._recommender_signal_label(score),
                }
            )
        return result

    def push_daily_report(self, trading_date: date) -> None:
        ranked_rows = self.list_opportunity_stocks(limit=3)
        if not ranked_rows:
            return
        detail = "；".join(
            (
                f"{row['stock_code']}({row['opportunity_score']:.1f}/"
                f"{row['prediction_direction_label'] or '暂无AI'}/"
                f"{row['latest_pnl']:.1f}%)"
            )
            for row in ranked_rows
        )
        metrics = self.get_dashboard_metrics()
        self.notifier.send(
            title=f"每日战报 {trading_date.isoformat()}",
            content=(
                f"股票池{metrics['stock_pool_size']}只，"
                f"平均评分{metrics['avg_stock_score']:.1f}，"
                f"本轮机会排序TOP: {detail}"
            ),
        )

    def _trigger_alert_if_needed(self, stock_code: str, daily: DailyPerformance) -> None:
        if daily.evaluation_score < 40 and daily.pnl_percent < -8:
            subscriptions = self.db.scalars(
                select(AlertSubscription).where(
                    AlertSubscription.stock_code == stock_code,
                    AlertSubscription.is_active.is_(True),
                )
            ).all()
            for subscription in subscriptions:
                self.notifier.send(
                    title=f"风险告警 {stock_code}",
                    content=(
                        f"订阅用户:{subscription.subscriber}，"
                        f"评分{daily.evaluation_score:.1f}，"
                        f"收益{daily.pnl_percent:.1f}% ，建议关注风险。"
                    ),
                )
