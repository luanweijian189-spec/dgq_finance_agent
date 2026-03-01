from __future__ import annotations

from datetime import date, datetime

from .evaluation import (
    DailyMarketMetrics,
    RecommendationOutcome,
    compute_recommender_reliability,
    compute_stock_quality_score,
)
from .message_parser import MessageParser
from .models import DailyPerformance, Recommendation
from .repository import InMemoryRepository


class FinanceResearchService:
    def __init__(self, repository: InMemoryRepository | None = None, parser: MessageParser | None = None) -> None:
        self.repository = repository or InMemoryRepository()
        self.parser = parser or MessageParser()

    def ingest_message(
        self,
        message: str,
        recommender_name: str,
        wechat_id: str = "",
        recommend_ts: datetime | None = None,
    ) -> list[Recommendation]:
        parsed_recommendations = self.parser.parse_message(
            message=message,
            recommender_name=recommender_name,
            recommend_ts=recommend_ts,
        )

        created: list[Recommendation] = []
        for parsed in parsed_recommendations:
            stock = self.repository.upsert_stock(parsed.stock_code)
            recommender = self.repository.upsert_recommender(parsed.recommender_name, wechat_id=wechat_id)
            recommendation = self.repository.add_recommendation(
                stock_id=stock.id,
                recommender_id=recommender.id,
                original_message=parsed.original_message,
                extracted_logic=parsed.extracted_logic,
                recommend_ts=parsed.recommend_ts,
            )
            created.append(recommendation)
        return created

    def add_manual_recommendation(
        self,
        stock_code: str,
        logic: str,
        recommender_name: str,
        wechat_id: str = "",
        recommend_ts: datetime | None = None,
    ) -> Recommendation:
        stock = self.repository.upsert_stock(stock_code)
        recommender = self.repository.upsert_recommender(recommender_name, wechat_id=wechat_id)
        return self.repository.add_recommendation(
            stock_id=stock.id,
            recommender_id=recommender.id,
            original_message=logic,
            extracted_logic=logic,
            recommend_ts=recommend_ts,
        )

    def evaluate_daily(
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
        daily_date: date | None = None,
        notes: str = "",
    ) -> DailyPerformance:
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
        merged_notes = analysis if not notes else f"{analysis} {notes}"

        return self.repository.add_daily_performance(
            recommendation_id=recommendation_id,
            close_price=close_price,
            high_price=high_price,
            low_price=low_price,
            pnl_percent=pnl_percent,
            max_drawdown=max_drawdown,
            evaluation_score=score,
            date=daily_date or date.today(),
            sharpe_ratio=sharpe_ratio,
            notes=merged_notes,
            extra={
                "logic_validated": logic_validated,
                "market_cap_score": market_cap_score,
                "elasticity_score": elasticity_score,
                "liquidity_score": liquidity_score,
            },
        )

    def refresh_recommender_scores(self) -> dict[str, float]:
        now = date.today()
        result: dict[str, float] = {}

        for recommender in self.repository.iter_recommenders():
            recommendations = self.repository.get_recommendations_for_recommender(recommender.id)
            outcomes: list[RecommendationOutcome] = []
            for recommendation in recommendations:
                latest_daily = self.repository.latest_daily_for_recommendation(recommendation.id)
                if latest_daily is None:
                    continue
                days_ago = max((now - recommendation.recommend_ts.date()).days, 0)
                outcomes.append(
                    RecommendationOutcome(
                        return_percent=latest_daily.pnl_percent,
                        max_drawdown=latest_daily.max_drawdown,
                        days_ago=days_ago,
                    )
                )

            reliability_score, _ = compute_recommender_reliability(outcomes)
            self.repository.update_recommender_score(recommender.id, reliability_score)
            result[recommender.name] = reliability_score

        return result
