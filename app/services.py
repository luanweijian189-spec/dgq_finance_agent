from __future__ import annotations

import csv
import json
import re
import zlib
from io import StringIO
from pathlib import Path
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from dgq_finance_agent.evaluation import (
    DailyMarketMetrics,
    RecommendationOutcome,
    compute_recommender_reliability,
    compute_stock_quality_score,
)
from dgq_finance_agent.message_parser import MessageParser

from .models import AlertSubscription, DailyPerformance, Recommendation, Recommender, Stock
from .analysis_agent import StockAnalysisAgent
from .notifier import AlertNotifier
from .providers import MarketDataProvider, NewsDataProvider
from .rag_store import ResearchNoteStore
from .stock_knowledge_store import StockKnowledgeStore


class FinanceAgentService:
    _EXPORT_HEADER_PATTERN = re.compile(
        r"^(?P<name>[^\d:：]{1,32})\s+(?P<ts>\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)$"
    )
    _WATCHLIST_PATTERNS = (
        re.compile(r"(?:建议)?关注(?P<names>[\u4e00-\u9fa5A-Za-z0-9、，,和及与/\s]{2,80})"),
        re.compile(r"看好(?P<names>[\u4e00-\u9fa5A-Za-z0-9、，,和及与/\s]{2,80})"),
        re.compile(r"推荐(?P<names>[\u4e00-\u9fa5A-Za-z0-9、，,和及与/\s]{2,80})"),
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
        rag_store: Optional[ResearchNoteStore] = None,
        analysis_agent: Optional[StockAnalysisAgent] = None,
        stock_knowledge_store: Optional[StockKnowledgeStore] = None,
        daily_report_dir: str = "reports/daily",
    ) -> None:
        self.db = db
        self.parser = MessageParser()
        self.market_provider = market_provider
        self.news_provider = news_provider
        self.notifier = notifier
        self.rag_store = rag_store or ResearchNoteStore("data/research_notes.jsonl")
        self.analysis_agent = analysis_agent or StockAnalysisAgent(model_name="rule")
        self.stock_knowledge_store = stock_knowledge_store or StockKnowledgeStore("data/stocks")
        self.daily_report_dir = daily_report_dir

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
        return [
            {
                "stock_code": self._name_to_virtual_code(name),
                "stock_name": name,
                "recommender_name": recommender_name,
                "recommend_ts": timestamp,
                "extracted_logic": extracted_logic,
                "original_message": message,
                "status": "pending_mapping",
            }
            for name in names
        ]

    def _parse_recommendations(
        self,
        message: str,
        recommender_name: str,
        recommend_ts: Optional[datetime],
    ) -> list[Any]:
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
                if 0 < len(left) <= 32 and not re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", left):
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
        rag_notes = self.rag_store.search(stock.stock_code, stock.stock_name, limit=5)
        stock_file_notes = self.stock_knowledge_store.search(stock.stock_code, stock.stock_name, limit=6)
        rag_context = [f"{item.ts.date()} {item.text[:80]}" for item in rag_notes]
        rag_context.extend(stock_file_notes)
        logic = recommendation.extracted_logic or recommendation.original_message[:120]

        if daily is None:
            return self.analysis_agent.analyze(
                stock_code=stock.stock_code,
                stock_name=stock.stock_name or "",
                logic=logic,
                score=45.0,
                pnl_percent=0.0,
                max_drawdown=-5.0,
                rag_context=rag_context,
            )

        return self.analysis_agent.analyze(
            stock_code=stock.stock_code,
            stock_name=stock.stock_name or "",
            logic=logic,
            score=float(daily.evaluation_score),
            pnl_percent=float(daily.pnl_percent),
            max_drawdown=float(daily.max_drawdown),
            rag_context=rag_context,
        )

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
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        if not lines:
            return {"saved": 0}

        saved = 0
        for line in lines:
            self._save_research_note(line, operator_name, source, None)
            linked_stocks = self._find_stocks_in_text(line)
            for stock in linked_stocks:
                self.stock_knowledge_store.append_entry(
                    stock_code=stock.stock_code,
                    stock_name=stock.stock_name or "",
                    source=source,
                    operator=operator_name,
                    entry_type="research",
                    content=line,
                )
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
        current_date = trading_date or date.today()
        recommendations = self.db.scalars(
            select(Recommendation).where(Recommendation.status == "tracking")
        ).all()
        count = 0
        for recommendation in recommendations:
            if not recommendation.stock.stock_name and hasattr(self.market_provider, "get_stock_name"):
                try:
                    stock_name = self.market_provider.get_stock_name(recommendation.stock.stock_code)
                    if stock_name:
                        recommendation.stock.stock_name = stock_name
                        self.db.commit()
                except Exception:
                    pass

            snapshot = self.market_provider.get_daily_snapshot(recommendation.stock.stock_code, current_date)
            try:
                logic_validated = self.news_provider.validate_recommendation_logic(
                    recommendation.stock.stock_code,
                    recommendation.extracted_logic,
                    current_date,
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
                daily_date=current_date,
            )
            count += 1
        self.refresh_recommender_scores()
        self.generate_daily_tracking_file(current_date)
        self.push_daily_report(current_date)
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

        first_recommendation_subquery = (
            select(
                Recommendation.stock_id.label("stock_id"),
                func.min(Recommendation.id).label("first_recommendation_id"),
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
                first_recommendation_subquery,
                first_recommendation_subquery.c.stock_id == Stock.id,
            )
            .join(Recommendation, Recommendation.id == first_recommendation_subquery.c.first_recommendation_id)
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
            result.append(
                {
                    "stock_code": stock.stock_code,
                    "stock_name": stock.stock_name or "",
                    "recommender_name": recommender.name,
                    "first_seen": first_seen,
                    "status": recommendation.status,
                    "logic": recommendation.extracted_logic,
                    "latest_date": latest_daily.date if latest_daily else None,
                    "latest_score": float(latest_daily.evaluation_score) if latest_daily else 0.0,
                    "latest_pnl": float(latest_daily.pnl_percent) if latest_daily else 0.0,
                    "latest_notes": latest_daily.notes if latest_daily else "",
                }
            )
        return result

    def get_daily_tracking_records(self, limit: int = 500) -> List[Dict[str, Any]]:
        rows = self.db.execute(
            select(DailyPerformance, Recommendation, Stock, Recommender)
            .join(Recommendation, Recommendation.id == DailyPerformance.recommendation_id)
            .join(Stock, Stock.id == Recommendation.stock_id)
            .join(Recommender, Recommender.id == Recommendation.recommender_id)
            .order_by(DailyPerformance.date.desc(), DailyPerformance.id.desc())
            .limit(limit)
        ).all()

        return [
            {
                "date": row[0].date,
                "stock_code": row[2].stock_code,
                "stock_name": row[2].stock_name or "",
                "recommender_name": row[3].name,
                "evaluation_score": float(row[0].evaluation_score),
                "pnl_percent": float(row[0].pnl_percent),
                "logic": row[1].extracted_logic,
                "daily_notes": row[0].notes,
            }
            for row in rows
        ]

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
        return (
            f"{stock_code} 最新评分 {latest_daily.evaluation_score:.1f}，"
            f"当前收益 {latest_daily.pnl_percent:.2f}% ，"
            f"最大回撤 {latest_daily.max_drawdown:.2f}% 。"
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
            .order_by(Recommender.reliability_score.desc())
        ).all()
        return [
            {
                "name": row[0].name,
                "reliability_score": float(row[0].reliability_score),
                "recommendation_count": int(row[1]),
            }
            for row in rows
        ]

    def push_daily_report(self, trading_date: date) -> None:
        top = self.list_top_stocks(limit=3, reverse=True)
        if not top:
            return
        detail = "；".join(
            f"{stock.stock_code}({daily.evaluation_score:.1f}/{daily.pnl_percent:.1f}%)"
            for stock, daily in top
        )
        metrics = self.get_dashboard_metrics()
        self.notifier.send(
            title=f"每日战报 {trading_date.isoformat()}",
            content=(
                f"股票池{metrics['stock_pool_size']}只，"
                f"平均评分{metrics['avg_stock_score']:.1f}，"
                f"TOP: {detail}"
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
