from __future__ import annotations

from datetime import date, datetime
from unittest import TestCase

from dgq_finance_agent.agent import AgentCommandHandler
from dgq_finance_agent.evaluation import (
    DailyMarketMetrics,
    RecommendationOutcome,
    compute_recommender_reliability,
    compute_stock_quality_score,
)
from dgq_finance_agent.message_parser import MessageParser
from dgq_finance_agent.service import FinanceResearchService


class MessageParserTests(TestCase):
    def test_parse_recommendation_message(self) -> None:
        parser = MessageParser()
        message = "看好600519，逻辑是业绩超预期，估值仍有修复空间。"

        parsed = parser.parse_message(message=message, recommender_name="张三", recommend_ts=datetime(2026, 3, 1))

        self.assertEqual(1, len(parsed))
        self.assertEqual("600519", parsed[0].stock_code)
        self.assertIn("逻辑", parsed[0].extracted_logic)


class EvaluationTests(TestCase):
    def test_stock_quality_score_improves_with_better_metrics(self) -> None:
        low_metrics = DailyMarketMetrics(
            pnl_percent=-5,
            peak_pnl_percent=0,
            sharpe_ratio=-0.2,
            max_drawdown=-12,
            logic_validated=False,
            market_cap_score=40,
            elasticity_score=35,
            liquidity_score=50,
        )
        high_metrics = DailyMarketMetrics(
            pnl_percent=20,
            peak_pnl_percent=30,
            sharpe_ratio=1.2,
            max_drawdown=-3,
            logic_validated=True,
            market_cap_score=70,
            elasticity_score=75,
            liquidity_score=80,
        )

        low_score, _ = compute_stock_quality_score(low_metrics)
        high_score, _ = compute_stock_quality_score(high_metrics)

        self.assertGreater(high_score, low_score)
        self.assertGreaterEqual(high_score, 0)
        self.assertLessEqual(high_score, 100)

    def test_recommender_reliability(self) -> None:
        bad = [
            RecommendationOutcome(return_percent=-8, max_drawdown=-20, days_ago=10),
            RecommendationOutcome(return_percent=2, max_drawdown=-15, days_ago=40),
        ]
        good = [
            RecommendationOutcome(return_percent=18, max_drawdown=-5, days_ago=5),
            RecommendationOutcome(return_percent=12, max_drawdown=-3, days_ago=25),
        ]

        bad_score, _ = compute_recommender_reliability(bad)
        good_score, _ = compute_recommender_reliability(good)
        self.assertGreater(good_score, bad_score)


class ServiceAndAgentTests(TestCase):
    def test_end_to_end_agent_commands(self) -> None:
        service = FinanceResearchService()
        agent = AgentCommandHandler(service)

        add_result = agent.handle("/add 600519 业绩拐点明确 by 张三")
        self.assertIn("已录入推荐", add_result)

        recommender = service.repository.get_recommender_by_name("张三")
        self.assertIsNotNone(recommender)

        stock = service.repository.get_stock_by_code("600519")
        self.assertIsNotNone(stock)
        recommendations = service.repository.get_recommendations_for_stock(stock.id)
        self.assertEqual(1, len(recommendations))

        service.evaluate_daily(
            recommendation_id=recommendations[0].id,
            close_price=120.0,
            high_price=123.0,
            low_price=118.0,
            pnl_percent=15.0,
            max_drawdown=-4.0,
            sharpe_ratio=1.0,
            logic_validated=True,
            market_cap_score=70,
            elasticity_score=65,
            liquidity_score=75,
            daily_date=date(2026, 3, 1),
        )

        service.refresh_recommender_scores()

        status = agent.handle("/status 600519")
        who = agent.handle("/who 张三")
        top = agent.handle("/top 1")

        self.assertIn("最新评分", status)
        self.assertIn("可靠性评分", who)
        self.assertIn("TOP", top)
