from __future__ import annotations

from datetime import date
from pathlib import Path
from subprocess import CompletedProcess
from unittest import TestCase
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.agent import OpenClawCommandHandler
from app.database import Base
from app.decision_engine import AIDecision
from app.input_parser_agent import UnderstoodMessage, UnderstoodStock
from app.llm_client import LLMApiClient
from app.llm_usage_store import LLMUsageStore
from app.models import DailyPerformance
from app.models import Recommendation
from app.models import Stock
from app.models import StockPrediction
from app.notifier import AlertNotifier, OpenClawNotifier
from app.providers import IntradayBar, IntradayTrade, MarketDataProvider, MockMarketDataProvider, MockNewsDataProvider, NewsDiscoveryItem
from app.services import FinanceAgentService


class _CollectNotifier(AlertNotifier):
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def send(self, title: str, content: str) -> None:
        self.messages.append((title, content))


class _FakeInputParserAgent:
    def __init__(self, result: UnderstoodMessage | None) -> None:
        self.result = result

    def understand_message(self, message: str) -> UnderstoodMessage | None:
        return self.result


class _SearchableMarketProvider(MarketDataProvider):
    def __init__(self, mapping: dict[str, tuple[str, str]]) -> None:
        self.mapping = mapping

    def get_stock_name(self, stock_code: str) -> str:
        for code, name in self.mapping.values():
            if code == stock_code:
                return name
        return ""

    def search_stock_candidates(self, query: str, limit: int = 5) -> list[tuple[str, str]]:
        result = []
        for key, value in self.mapping.items():
            if query and query in key:
                result.append(value)
        return result[:limit]

    def get_daily_snapshot(self, stock_code: str, trading_date: date):  # pragma: no cover
        raise NotImplementedError


class _SequenceDecisionEngine:
    def __init__(self, directions: list[str]) -> None:
        self.directions = directions
        self.index = 0

    def decide(
        self,
        stock_code: str,
        stock_name: str,
        logic: str,
        score: float,
        pnl_percent: float,
        max_drawdown: float,
        memory_context: list[str],
        evidence_bundle: dict | None = None,
    ) -> AIDecision:
        direction = self.directions[min(self.index, len(self.directions) - 1)]
        self.index += 1
        return AIDecision(
            direction=direction,
            confidence=0.82,
            horizon_days=1,
            thesis=f"{stock_name} {direction}",
            invalidation_conditions="量价背离",
            risk_flags=[],
            evidence=[logic],
            raw_text="stub",
        )


class _StaticDecisionEngine:
    def __init__(self, direction: str, confidence: float = 0.8) -> None:
        self.direction = direction
        self.confidence = confidence

    def decide(
        self,
        stock_code: str,
        stock_name: str,
        logic: str,
        score: float,
        pnl_percent: float,
        max_drawdown: float,
        memory_context: list[str],
        evidence_bundle: dict | None = None,
    ) -> AIDecision:
        return AIDecision(
            direction=self.direction,
            confidence=self.confidence,
            horizon_days=1,
            thesis=f"{stock_name} {self.direction}",
            invalidation_conditions="量价背离",
            risk_flags=[],
            evidence=[logic],
            raw_text="stub",
        )


class _StableIntradayProvider:
    def get_minute_bars(
        self,
        stock_code: str,
        period: str = "1",
        adjust: str = "",
        start_datetime=None,
        end_datetime=None,
    ):
        return (
            [
                IntradayBar(
                    timestamp="2026-03-09 10:35:00",
                    open_price=10.0,
                    close_price=10.8,
                    high_price=10.9,
                    low_price=9.95,
                    volume=120000,
                    amount=1280000,
                    change_percent=8.0,
                    change_amount=0.8,
                )
            ],
            False,
        )

    def get_trade_ticks(self, stock_code: str):
        return (
            [
                IntradayTrade(
                    timestamp="10:35:03",
                    price=10.8,
                    volume_lot=120,
                    side="买盘",
                )
            ],
            False,
        )


class DeployServiceTests(TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.notifier = _CollectNotifier()
        self.usage_store = LLMUsageStore("tests/.tmp_llm_usage.jsonl")
        self.service = FinanceAgentService(
            db=self.db,
            market_provider=MockMarketDataProvider(),
            news_provider=MockNewsDataProvider(),
            notifier=self.notifier,
            llm_usage_store=self.usage_store,
            daily_report_dir="tests/.tmp_reports",
        )

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()
        tmp_dir = Path("tests/.tmp_reports")
        if tmp_dir.exists():
            for file in tmp_dir.glob("*.md"):
                file.unlink()
            tmp_dir.rmdir()
        usage_file = Path("tests/.tmp_llm_usage.jsonl")
        if usage_file.exists():
            usage_file.unlink()

    def test_full_flow_with_command_and_daily_evaluation(self) -> None:
        handler = OpenClawCommandHandler(self.service)
        self.service.add_manual_recommendation(
            stock_code="600519",
            stock_name="贵州茅台",
            logic="业绩持续增长",
            recommender_name="张三",
        )

        eval_count = self.service.evaluate_all_recommendations(trading_date=date(2026, 3, 1))
        self.assertEqual(1, eval_count)

        top = handler.handle("/top 1", operator="u1")
        status = handler.handle("/status 600519", operator="u1")
        self.assertIn("TOP 1", top)
        self.assertIn("最新评分", status)

        tracking_rows = self.service.get_stock_pool_tracking(limit=20)
        self.assertEqual(1, len(tracking_rows))
        self.assertEqual("600519", tracking_rows[0]["stock_code"])
        self.assertEqual("贵州茅台", tracking_rows[0]["stock_name"])

        daily_rows = self.service.get_daily_tracking_records(limit=20)
        self.assertEqual(1, len(daily_rows))
        self.assertIn("逻辑", daily_rows[0]["daily_notes"])

    def test_alert_subscription_command(self) -> None:
        handler = OpenClawCommandHandler(self.service)
        handler.handle("/add 600519 业绩持续增长 by 张三", operator="u1")
        alert_result = handler.handle("/alert on 600519", operator="u1")
        self.assertIn("已订阅", alert_result)

    def test_loop_commands_dispatch(self) -> None:
        handler = OpenClawCommandHandler(self.service)
        with patch("app.agent.subprocess.run") as mocked_run:
            mocked_run.return_value = CompletedProcess(
                args=["bash", "scripts/copilot_hybrid_loop.sh", "summary"],
                returncode=0,
                stdout="loop summary ok\n",
                stderr="",
            )
            result = handler.handle("/loop summary", operator="u1")
            self.assertIn("loop summary ok", result)

            init_result = handler.handle("/loop init 修复回归并自测", operator="u1")
            self.assertIn("loop summary ok", init_result)
            self.assertGreaterEqual(mocked_run.call_count, 2)

    def test_loop_command_failure_message(self) -> None:
        handler = OpenClawCommandHandler(self.service)
        with patch("app.agent.subprocess.run") as mocked_run:
            mocked_run.return_value = CompletedProcess(
                args=["bash", "scripts/copilot_hybrid_loop.sh", "check"],
                returncode=1,
                stdout="",
                stderr="boom",
            )
            result = handler.handle("/loop check", operator="u1")
            self.assertIn("loop命令执行失败", result)

    def test_bulk_import_supports_multiple_formats_and_dedup(self) -> None:
        raw_text = """
张三：600519 看好，逻辑是高端白酒复苏
李四 2026-03-01 09:31
000001 推荐，逻辑是估值修复
{"sender":"王五","content":"300750 看好，逻辑是出海","time":"2026-03-01 10:20"}
""".strip()

        result = self.service.ingest_bulk_text(raw_text, default_recommender_name="群友")
        self.assertEqual(3, result["created"])
        self.assertEqual(0, result["duplicates"])

        second = self.service.ingest_bulk_text(raw_text, default_recommender_name="群友")
        self.assertEqual(0, second["created"])
        self.assertGreaterEqual(second["duplicates"], 3)

    def test_intraday_refresh_cycle_pushes_highlight_message(self) -> None:
        self.service = FinanceAgentService(
            db=self.db,
            market_provider=MockMarketDataProvider(),
            news_provider=MockNewsDataProvider(),
            intraday_provider=_StableIntradayProvider(),
            notifier=self.notifier,
            llm_usage_store=self.usage_store,
            daily_report_dir="tests/.tmp_reports",
        )
        self.service.add_manual_recommendation(
            stock_code="002384",
            stock_name="东山精密",
            logic="消费电子修复",
            recommender_name="张三",
        )

        result = self.service.run_intraday_refresh_cycle(limit=5, min_change_percent=0.5)

        self.assertEqual(1, result["success_count"])
        self.assertEqual(1, result["highlight_count"])
        self.assertTrue(any("盘中刷新" in title for title, _ in self.notifier.messages))

    def test_openclaw_notifier_uses_official_cli_shape(self) -> None:
        notifier = OpenClawNotifier(
            command="openclaw",
            profile="dev",
            channel="qq",
            recipient="123456",
            timeout_seconds=15,
        )

        with patch("app.notifier.subprocess.run") as mocked_run:
            mocked_run.return_value = CompletedProcess(
                args=["openclaw"],
                returncode=0,
                stdout="ok",
                stderr="",
            )

            notifier.send("测试标题", "测试内容")

        called_args = mocked_run.call_args[0][0]
        self.assertEqual("openclaw", called_args[0])
        self.assertIn("--dev", called_args)
        self.assertIn("--channel", called_args)
        self.assertIn("qq", called_args)
        self.assertIn("--deliver", called_args)
        self.assertIn("--to", called_args)
        self.assertIn("123456", called_args)

    def test_bulk_import_csv(self) -> None:
        csv_text = """message,recommender_name,recommend_ts
600519 看好 逻辑是业绩改善,张三,2026-03-01 10:00
300750 推荐 逻辑是出海,李四,2026-03-01 10:10
"""
        result = self.service.ingest_bulk_text(csv_text, default_recommender_name="群友")
        self.assertEqual(2, result["created"])

    def test_bulk_import_long_message_name_only(self) -> None:
        raw_text = """【华福汽车&机器人】三联锻造更新20260301

[玫瑰]#新业务： 基于原有工艺优势以及缺电大趋势，切入燃气机叶片业务。
[爱心]缺电逻辑目前演绎2个多月，相关公司股价创新高，强烈建议关注三联锻造、威孚高科等低位补涨标的。

于鹏亮15145103157"""

        result = self.service.ingest_bulk_text(raw_text, default_recommender_name="群友")
        self.assertGreaterEqual(result["created"], 2)

        tracking_rows = self.service.get_stock_pool_tracking(limit=20)
        stock_names = {row["stock_name"] for row in tracking_rows}
        self.assertIn("三联锻造", stock_names)
        self.assertIn("威孚高科", stock_names)

    def test_non_recommendation_text_saved_as_rag(self) -> None:
        raw_text = "宏观点评：本周流动性边际改善，但未给出明确个股推荐。"
        result = self.service.ingest_bulk_text(raw_text, default_recommender_name="研究员A")
        self.assertEqual(0, result["created"])
        self.assertEqual(1, result["rag_notes"])

    def test_llm_input_parser_extracts_tracking_update(self) -> None:
        self.service.input_parser_agent = _FakeInputParserAgent(
            UnderstoodMessage(
                message_type="tracking_update",
                logic_summary="东山精密消费电子复苏逻辑继续成立，关注下周订单验证。",
                confidence=0.92,
                stocks=[UnderstoodStock(stock_code="002384", stock_name="东山精密")],
                raw_text="{}",
            )
        )

        result = self.service.ingest_bulk_text(
            "继续跟踪东山精密，消费电子复苏逻辑还在，重点看下周订单验证。",
            default_recommender_name="研究员A",
        )
        self.assertEqual(1, result["created"])

        tracking_rows = self.service.get_stock_pool_tracking(limit=20)
        self.assertEqual("002384", tracking_rows[0]["stock_code"])

    def test_llm_stock_name_uses_market_dictionary_mapping(self) -> None:
        self.service.market_provider = _SearchableMarketProvider({"科新机电": ("300092", "科新机电")})
        self.service.input_parser_agent = _FakeInputParserAgent(
            UnderstoodMessage(
                message_type="recommendation",
                logic_summary="燃机辅机配套受益东方电气订单放量。",
                confidence=0.93,
                stocks=[UnderstoodStock(stock_code="", stock_name="科新机电")],
                raw_text="{}",
            )
        )

        result = self.service.ingest_bulk_text("科新机电受益燃机产业链放量。", default_recommender_name="研究员A")
        self.assertEqual(1, result["created"])
        tracking_rows = self.service.get_stock_pool_tracking(limit=20)
        self.assertEqual("300092", tracking_rows[0]["stock_code"])
        self.assertEqual("科新机电", tracking_rows[0]["stock_name"])

    def test_llm_wrong_code_is_corrected_by_stock_name_mapping(self) -> None:
        self.service.add_manual_recommendation(
            stock_code="300092",
            stock_name="科新机电",
            logic="占位",
            recommender_name="系统",
        )
        self.service.input_parser_agent = _FakeInputParserAgent(
            UnderstoodMessage(
                message_type="recommendation",
                logic_summary="燃机链订单边际改善。",
                confidence=0.91,
                stocks=[UnderstoodStock(stock_code="600848", stock_name="科新机电")],
                raw_text="{}",
            )
        )

        result = self.service.ingest_bulk_text("科新机电订单改善。", default_recommender_name="研究员A")
        self.assertEqual(1, result["created"])
        rows = self.service.get_stock_pool_tracking(limit=20)
        self.assertTrue(any(row["stock_code"] == "300092" and row["stock_name"] == "科新机电" for row in rows))
        self.assertFalse(any(row["stock_code"] == "600848" and row["stock_name"] == "科新机电" for row in rows))

    def test_non_stock_entities_are_filtered_from_llm_result(self) -> None:
        self.service.input_parser_agent = _FakeInputParserAgent(
            UnderstoodMessage(
                message_type="recommendation",
                logic_summary="光通信产业链关注。",
                confidence=0.9,
                stocks=[
                    UnderstoodStock(stock_code="", stock_name="电芯片"),
                    UnderstoodStock(stock_code="", stock_name="Tower"),
                    UnderstoodStock(stock_code="", stock_name="存储芯片"),
                ],
                raw_text="{}",
            )
        )

        result = self.service.ingest_bulk_text("关注电芯片与Tower方向。", default_recommender_name="研究员A")
        self.assertEqual(0, result["created"])
        self.assertEqual(1, result["ignored"])

    def test_low_confidence_llm_result_can_still_use_stock_names(self) -> None:
        self.service.market_provider = _SearchableMarketProvider(
            {
                "科新机电": ("300092", "科新机电"),
                "泰豪科技": ("600590", "泰豪科技"),
                "长源东谷": ("603950", "长源东谷"),
            }
        )
        self.service.input_parser_agent = _FakeInputParserAgent(
            UnderstoodMessage(
                message_type="tracking_update",
                logic_summary="燃机链边际改善。",
                confidence=0.0,
                stocks=[
                    UnderstoodStock(stock_code="600848", stock_name="科新机电"),
                    UnderstoodStock(stock_code="600590", stock_name="泰豪科技"),
                    UnderstoodStock(stock_code="300181", stock_name="长源东谷"),
                ],
                raw_text="{}",
            )
        )

        message = "燃气轮机板块近期有哪些边际变化：1、科新机电；2、泰豪科技；3、长源东谷。"
        result = self.service.ingest_bulk_text(message, default_recommender_name="研究员A")
        self.assertEqual(3, result["created"])
        rows = self.service.get_stock_pool_tracking(limit=20)
        codes = {row["stock_code"] for row in rows}
        self.assertTrue({"300092", "600590", "603950"}.issubset(codes))
        self.assertFalse(any(row["stock_code"] in {"600848", "300181"} for row in rows))

    def test_research_message_extracts_candidate_recommendations(self) -> None:
        self.service.add_manual_recommendation(
            stock_code="002515",
            stock_name="金字火腿",
            logic="占位",
            recommender_name="系统",
        )
        self.service.add_manual_recommendation(
            stock_code="688981",
            stock_name="中芯国际",
            logic="占位",
            recommender_name="系统",
        )
        self.service.input_parser_agent = _FakeInputParserAgent(
            UnderstoodMessage(
                message_type="research",
                logic_summary="电芯片国产替代空间大，建议关注核心受益标的。",
                confidence=0.92,
                stocks=[
                    UnderstoodStock(stock_code="", stock_name="TIA"),
                    UnderstoodStock(stock_code="", stock_name="金字火腿"),
                    UnderstoodStock(stock_code="", stock_name="Tower"),
                    UnderstoodStock(stock_code="", stock_name="中芯国际"),
                ],
                raw_text="{}",
            )
        )

        message = "建议重点关注：TIA/Driver：优迅股份、中晟微电子（金字火腿参股）；代工与制造：Tower/中芯国际等。"
        result = self.service.ingest_bulk_text(message, default_recommender_name="研究员A")
        self.assertEqual(2, result["created"])
        rows = self.service.get_stock_pool_tracking(limit=20)
        codes = {row["stock_code"] for row in rows}
        self.assertIn("002515", codes)
        self.assertIn("688981", codes)
        self.assertFalse(any(row["stock_name"] in {"TIA", "Tower"} for row in rows))

    def test_research_message_supports_alias_mapping_on_first_push(self) -> None:
        self.service.market_provider = _SearchableMarketProvider(
            {
                "长川科技": ("300604", "长川科技"),
                "华峰测控": ("688200", "华峰测控"),
                "精智达": ("688627", "精智达"),
                "强一股份": ("688809", "强一股份"),
            }
        )
        self.service.input_parser_agent = _FakeInputParserAgent(
            UnderstoodMessage(
                message_type="research",
                logic_summary="去日化主线下，测试机与探针卡方向值得重点关注。",
                confidence=0.91,
                stocks=[
                    UnderstoodStock(stock_code="", stock_name="长川"),
                    UnderstoodStock(stock_code="", stock_name="华峰"),
                    UnderstoodStock(stock_code="", stock_name="精智达"),
                    UnderstoodStock(stock_code="", stock_name="强一股份"),
                ],
                raw_text="{}",
            )
        )

        message = "建议积极关注半导体设备的去日化主线；首推测试机（长川、华峰、精智达），以及探针卡（强一股份）。"
        result = self.service.ingest_bulk_text(message, default_recommender_name="研究员A")
        self.assertEqual(4, result["created"])
        rows = self.service.get_stock_pool_tracking(limit=20)
        codes = {row["stock_code"] for row in rows}
        self.assertTrue({"300604", "688200", "688627", "688809"}.issubset(codes))

    def test_research_unknown_entity_keeps_pending_mapping_status(self) -> None:
        self.service.add_manual_recommendation(
            stock_code="002515",
            stock_name="金字火腿",
            logic="占位",
            recommender_name="系统",
        )
        self.service.input_parser_agent = _FakeInputParserAgent(
            UnderstoodMessage(
                message_type="research",
                logic_summary="建议关注电芯片核心受益标的。",
                confidence=0.91,
                stocks=[
                    UnderstoodStock(stock_code="", stock_name="中晟微电子"),
                    UnderstoodStock(stock_code="", stock_name="金字火腿"),
                ],
                raw_text="{}",
            )
        )

        message = "建议重点关注：优迅股份、中晟微电子（金字火腿参股）。"
        result = self.service.ingest_bulk_text(message, default_recommender_name="研究员A")
        self.assertEqual(2, result["created"])

        joined_rows = self.db.execute(
            select(Recommendation, Stock)
            .join(Stock, Stock.id == Recommendation.stock_id)
            .order_by(Recommendation.id.asc())
        ).all()
        self.assertTrue(
            any(
                recommendation.status == "pending_mapping" and stock.stock_name == "中晟微电子"
                for recommendation, stock in joined_rows
            )
        )
        self.assertFalse(
            any(
                recommendation.status in self.service.ACTIVE_RECOMMENDATION_STATUSES and stock.stock_name == "中晟微电子"
                for recommendation, stock in joined_rows
            )
        )

    def test_generate_daily_tracking_file(self) -> None:
        self.service.add_manual_recommendation(
            stock_code="600519",
            stock_name="贵州茅台",
            logic="业绩持续增长",
            recommender_name="张三",
        )
        self.service.evaluate_all_recommendations(trading_date=date(2026, 3, 1))
        path = self.service.generate_daily_tracking_file(date(2026, 3, 1))
        content = Path(path).read_text(encoding="utf-8")
        self.assertIn("股票池每日追踪 2026-03-01", content)
        self.assertIn("智能分析", content)
        self.assertIn("纠偏建议", content)

    def test_daily_tracking_records_are_grouped_by_stock_and_date(self) -> None:
        self.service.add_manual_recommendation(
            stock_code="002384",
            stock_name="东山精密",
            logic="消费电子复苏",
            recommender_name="李四",
        )
        self.service.add_manual_recommendation(
            stock_code="002384",
            stock_name="东山精密",
            logic="继续跟踪订单验证",
            recommender_name="研究员A",
        )

        self.service.evaluate_all_recommendations(trading_date=date(2026, 3, 6))
        rows = self.service.get_daily_tracking_records(limit=20)

        self.assertEqual(1, len(rows))
        self.assertEqual("002384", rows[0]["stock_code"])
        self.assertIn("李四", rows[0]["recommender_name"])
        self.assertIn("研究员A", rows[0]["recommender_name"])
        self.assertEqual(2, rows[0]["duplicate_count"])

    def test_opportunity_ranking_prioritizes_upside_signal(self) -> None:
        self.service.decision_engine = _SequenceDecisionEngine(["up", "down"])
        self.service.add_manual_recommendation(
            stock_code="002384",
            stock_name="东山精密",
            logic="消费电子复苏",
            recommender_name="李四",
        )
        self.service.add_manual_recommendation(
            stock_code="002436",
            stock_name="兴森科技",
            logic="博弈修复",
            recommender_name="研究员A",
        )

        self.service.evaluate_all_recommendations(trading_date=date(2026, 3, 6))

        ranked = self.service.list_opportunity_stocks(limit=10)
        self.assertEqual(2, len(ranked))
        self.assertEqual(1, ranked[0]["rank"])
        self.assertEqual("up", ranked[0]["prediction_direction"])
        self.assertGreaterEqual(ranked[0]["opportunity_score"], ranked[1]["opportunity_score"])

    def test_recommender_list_contains_rank_and_signal(self) -> None:
        recommendation_a = self.service.add_manual_recommendation(
            stock_code="002384",
            stock_name="东山精密",
            logic="消费电子复苏",
            recommender_name="李四",
        )
        recommendation_b = self.service.add_manual_recommendation(
            stock_code="002436",
            stock_name="兴森科技",
            logic="短期反弹",
            recommender_name="研究员A",
        )

        self.service.evaluate_recommendation(
            recommendation_id=recommendation_a.id,
            close_price=20.0,
            high_price=20.8,
            low_price=19.9,
            pnl_percent=12.0,
            max_drawdown=-1.2,
            sharpe_ratio=1.1,
            logic_validated=True,
            market_cap_score=78,
            elasticity_score=80,
            liquidity_score=82,
            daily_date=date(2026, 3, 6),
        )
        self.service.evaluate_recommendation(
            recommendation_id=recommendation_b.id,
            close_price=15.0,
            high_price=15.2,
            low_price=14.0,
            pnl_percent=-8.0,
            max_drawdown=-7.5,
            sharpe_ratio=-0.4,
            logic_validated=False,
            market_cap_score=48,
            elasticity_score=45,
            liquidity_score=50,
            daily_date=date(2026, 3, 6),
        )

        self.service.refresh_recommender_scores()
        rows = self.service.get_recommender_list()

        self.assertEqual(1, rows[0]["rank"])
        self.assertIn("signal_label", rows[0])
        self.assertGreater(rows[0]["reliability_score"], rows[1]["reliability_score"])

    def test_research_feeds_and_stock_detail(self) -> None:
        self.service.add_manual_recommendation(
            stock_code="002384",
            stock_name="东山精密",
            logic="消费电子复苏",
            recommender_name="李四",
        )
        stock_result = self.service.ingest_stock_research_text(
            "东山精密：下周重点看订单验证。",
            operator_name="研究员A",
        )
        macro_result = self.service.ingest_macro_research_text(
            "宏观观察：风险偏好仍需等待进一步修复。",
            operator_name="研究员B",
        )
        self.service.evaluate_all_recommendations(trading_date=date(2026, 3, 6))

        self.assertEqual(1, stock_result["saved"])
        self.assertEqual(1, macro_result["saved"])
        self.assertGreaterEqual(len(self.service.get_recent_stock_research_feed(limit=5)), 1)
        self.assertGreaterEqual(len(self.service.get_recent_macro_research(limit=5)), 1)

        detail = self.service.get_stock_detail("002384")
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual("东山精密", detail["stock_name"])
        self.assertGreaterEqual(len(detail["daily_history"]), 1)
        self.assertGreaterEqual(len(detail["knowledge_entries"]), 1)

    def test_evaluation_generates_conclusion_update_notifications(self) -> None:
        self.service.decision_engine = _SequenceDecisionEngine(["up", "down"])
        self.service.add_manual_recommendation(
            stock_code="002384",
            stock_name="东山精密",
            logic="消费电子复苏",
            recommender_name="李四",
        )

        self.service.evaluate_all_recommendations(trading_date=date(2026, 3, 6))
        first_updates = self.service.get_last_conclusion_updates()
        self.assertTrue(any("东山精密" in item for item in first_updates))

        self.service.evaluate_all_recommendations(trading_date=date(2026, 3, 7))
        second_updates = self.service.get_last_conclusion_updates()
        self.assertTrue(any("AI方向由 看涨 调整为 看跌" in item for item in second_updates))

        conclusion_messages = [msg for msg in self.notifier.messages if msg[0].startswith("结论更新")]
        self.assertGreaterEqual(len(conclusion_messages), 2)

    def test_llm_client_records_usage_summary(self) -> None:
        class _MockResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {
                        "prompt_tokens": 11,
                        "completion_tokens": 7,
                        "total_tokens": 18,
                    },
                }

        with patch("app.llm_client.requests.post", return_value=_MockResponse()):
            client = LLMApiClient(
                api_base="https://api.example.com/v1",
                api_key="test-key",
                model="demo-model",
                usage_store=self.usage_store,
                feature_name="analysis_agent",
            )
            result = client.chat(messages=[{"role": "user", "content": "hello"}], temperature=0)

        self.assertEqual("ok", result)
        summary = self.usage_store.summarize_recent(hours=24)
        self.assertEqual(1, summary["total_calls"])
        self.assertEqual(18, summary["total_tokens"])
        self.assertEqual("analysis_agent", summary["top_features"][0]["feature_name"])

    def test_status_machine_marks_priority_for_strong_setup(self) -> None:
        self.service.decision_engine = _StaticDecisionEngine(direction="up", confidence=0.82)
        recommendation = self.service.add_manual_recommendation(
            stock_code="002384",
            stock_name="东山精密",
            logic="订单恢复，景气回升",
            recommender_name="李四",
        )
        self.service.evaluate_recommendation(
            recommendation_id=recommendation.id,
            close_price=20.0,
            high_price=20.8,
            low_price=19.8,
            pnl_percent=9.5,
            max_drawdown=-1.8,
            sharpe_ratio=1.3,
            logic_validated=True,
            market_cap_score=78,
            elasticity_score=76,
            liquidity_score=82,
            daily_date=date(2026, 3, 6),
        )
        latest_daily = self.service.db.query(DailyPerformance).order_by(DailyPerformance.id.desc()).first()
        prediction = self.service.decision_engine.decide(
            stock_code="002384",
            stock_name="东山精密",
            logic="订单恢复，景气回升",
            score=float(latest_daily.evaluation_score),
            pnl_percent=float(latest_daily.pnl_percent),
            max_drawdown=float(latest_daily.max_drawdown),
            memory_context=[],
        )
        status, reason = self.service._derive_recommendation_status(recommendation, latest_daily, prediction)
        self.assertEqual("priority", status)
        self.assertIn("重点观察", reason)

    def test_status_machine_marks_risk_alert_for_weak_setup(self) -> None:
        self.service.decision_engine = _StaticDecisionEngine(direction="down", confidence=0.75)
        recommendation = self.service.add_manual_recommendation(
            stock_code="002436",
            stock_name="兴森科技",
            logic="短期博弈订单改善",
            recommender_name="研究员A",
        )
        self.service.evaluate_recommendation(
            recommendation_id=recommendation.id,
            close_price=15.0,
            high_price=15.3,
            low_price=14.4,
            pnl_percent=-6.4,
            max_drawdown=-5.8,
            sharpe_ratio=-0.35,
            logic_validated=False,
            market_cap_score=56,
            elasticity_score=52,
            liquidity_score=58,
            daily_date=date(2026, 3, 6),
        )
        latest_daily = self.service.db.query(DailyPerformance).order_by(DailyPerformance.id.desc()).first()
        prediction = self.service.decision_engine.decide(
            stock_code="002436",
            stock_name="兴森科技",
            logic="短期博弈订单改善",
            score=float(latest_daily.evaluation_score),
            pnl_percent=float(latest_daily.pnl_percent),
            max_drawdown=float(latest_daily.max_drawdown),
            memory_context=[],
        )
        status, reason = self.service._derive_recommendation_status(recommendation, latest_daily, prediction)
        self.assertEqual("risk_alert", status)
        self.assertIn("风险观察", reason)

    def test_llm_evidence_bundle_contains_multi_source_inputs(self) -> None:
        recommendation = self.service.add_manual_recommendation(
            stock_code="002384",
            stock_name="东山精密",
            logic="消费电子复苏，关注订单验证",
            recommender_name="李四",
        )
        self.service.ingest_stock_research_text(
            "东山精密：订单验证窗口临近，重点看下周客户排产。",
            operator_name="研究员A",
        )
        self.service.ingest_macro_research_text(
            "宏观观察：风险偏好改善，消费电子链条情绪回暖。",
            operator_name="研究员B",
        )
        daily = self.service.evaluate_recommendation(
            recommendation_id=recommendation.id,
            close_price=21.0,
            high_price=21.5,
            low_price=20.6,
            pnl_percent=3.8,
            max_drawdown=-2.2,
            sharpe_ratio=0.9,
            logic_validated=True,
            market_cap_score=68,
            elasticity_score=65,
            liquidity_score=70,
            daily_date=date(2026, 3, 6),
        )
        bundle = self.service._build_llm_evidence_bundle(recommendation, daily, ["2026-03-06 [news] 订单验证预期升温"])

        self.assertEqual("002384", bundle["stock"]["stock_code"])
        self.assertGreaterEqual(bundle["source_summary"]["stock_knowledge_count"], 1)
        self.assertGreaterEqual(bundle["source_summary"]["macro_note_count"], 1)
        self.assertGreaterEqual(bundle["source_summary"]["rag_snippet_count"], 1)
        self.assertGreaterEqual(len(bundle["recent_recommendations"]), 1)
        self.assertGreaterEqual(bundle["source_quality"]["source_count"], 2)

    def test_prediction_review_payload_contains_structured_tags(self) -> None:
        prediction = StockPrediction(
            stock_code="002384",
            stock_name="东山精密",
            prediction_date=date(2026, 3, 7),
            direction="up",
            confidence=0.82,
            review_result="hit",
            review_notes="上涨预测命中",
        )
        daily = DailyPerformance(
            recommendation_id=1,
            date=date(2026, 3, 7),
            close_price=20.0,
            high_price=20.8,
            low_price=19.9,
            pnl_percent=4.2,
            max_drawdown=-2.1,
            evaluation_score=78.0,
            sharpe_ratio=1.1,
            logic_validated=True,
            market_cap_score=70,
            elasticity_score=72,
            liquidity_score=74,
            notes="",
        )

        payload = self.service._build_prediction_review_payload(prediction, daily)
        self.assertEqual("hit", payload["review_result"])
        self.assertIn("strong_up_move", payload["review_tags"])
        self.assertIn("direction_confirmed", payload["review_tags"])

    def test_discover_command_scan_and_promote(self) -> None:
        handler = OpenClawCommandHandler(self.service)
        with patch.object(self.service.news_provider, "discover_candidate_stocks") as mocked_discovery:
            mocked_discovery.return_value = [
                NewsDiscoveryItem(
                    stock_code="002436",
                    stock_name="兴森科技",
                    headline="兴森科技(002436)订单增长",
                    summary="订单增长且景气改善",
                    source_site="https://www.stcn.com",
                    source_url="https://www.stcn.com/article/1",
                    event_type="order",
                    discovery_score=4.2,
                )
            ]
            result = handler.handle("/discover scan", operator="u1")
            self.assertIn("扫描完成", result)

        rows = self.service.list_news_candidates(limit=10, status="candidate")
        self.assertEqual(1, len(rows))
        promote_result = handler.handle(f"/discover promote {rows[0]['id']}", operator="u1")
        self.assertIn("已晋升到跟踪池", promote_result)

    def test_news_scan_api(self) -> None:
        with patch.object(self.service.news_provider, "discover_candidate_stocks") as mocked_discovery:
            mocked_discovery.return_value = [
                NewsDiscoveryItem(
                    stock_code="300750",
                    stock_name="宁德时代",
                    headline="宁德时代(300750)新签大单",
                    summary="新签订单",
                    source_site="https://finance.sina.com.cn",
                    source_url="https://finance.sina.com.cn/article/2",
                    event_type="order",
                    discovery_score=3.9,
                )
            ]
            payload = self.service.run_news_discovery_scan(min_score=2.5, auto_promote=False, limit=20)
            self.assertEqual(1, payload["saved_candidates"])
            items = self.service.list_news_candidates(limit=10, status="candidate")
            self.assertGreaterEqual(len(items), 1)
