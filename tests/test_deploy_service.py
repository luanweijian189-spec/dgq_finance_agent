from __future__ import annotations

from datetime import date
from pathlib import Path
from subprocess import CompletedProcess
from unittest import TestCase
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.agent import OpenClawCommandHandler
from app.database import Base
from app.notifier import AlertNotifier
from app.providers import MockMarketDataProvider, MockNewsDataProvider, NewsDiscoveryItem
from app.services import FinanceAgentService


class _CollectNotifier(AlertNotifier):
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def send(self, title: str, content: str) -> None:
        self.messages.append((title, content))


class DeployServiceTests(TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.notifier = _CollectNotifier()
        self.service = FinanceAgentService(
            db=self.db,
            market_provider=MockMarketDataProvider(),
            news_provider=MockNewsDataProvider(),
            notifier=self.notifier,
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
        self.assertEqual(0, result["ignored"])
        self.assertEqual(0, result["rag_notes"])

        second = self.service.ingest_bulk_text(raw_text, default_recommender_name="群友")
        self.assertEqual(0, second["created"])
        self.assertGreaterEqual(second["duplicates"], 3)

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
