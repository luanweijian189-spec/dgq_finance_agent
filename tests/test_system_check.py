from __future__ import annotations

from datetime import date
from pathlib import Path
import shutil
import sys
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.main import app, get_intraday_provider, get_service
from app.database import Base
from app.models import Recommendation, Recommender, Stock
from app.notifier import AlertNotifier
from app.providers import (
    AkshareIntradayDataProvider,
    BaostockMarketDataProvider,
    IntradayBar,
    IntradayTrade,
    MockMarketDataProvider,
    MockNewsDataProvider,
    ProviderError,
    SiteWhitelistNewsDataProvider,
    build_intraday_provider,
    build_market_provider,
    build_news_provider,
)
from app.services import FinanceAgentService


class _CollectNotifier(AlertNotifier):
    def send(self, title: str, content: str) -> None:
        return None


class _FakeFrame:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    @property
    def empty(self) -> bool:
        return not self._rows

    def to_dict(self, orient: str = "records") -> list[dict]:
        if orient != "records":
            raise ValueError("only records supported")
        return list(self._rows)


class SystemCheckTests(TestCase):
    def setUp(self) -> None:
        self._tmp_intraday_dir = Path("tests/.tmp_intraday")
        shutil.rmtree(self._tmp_intraday_dir, ignore_errors=True)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp_intraday_dir, ignore_errors=True)

    def test_provider_factory(self) -> None:
        self.assertIsInstance(build_market_provider("baostock"), BaostockMarketDataProvider)
        self.assertIsInstance(build_intraday_provider("akshare"), AkshareIntradayDataProvider)
        with self.assertRaises(ProviderError):
            build_market_provider("mock")
        self.assertIsInstance(
            build_news_provider("sites", news_site_whitelist="https://www.eastmoney.com"),
            SiteWhitelistNewsDataProvider,
        )
        with self.assertRaises(ProviderError):
            build_news_provider("mock")

    def test_sites_provider_semantic_match(self) -> None:
        provider = SiteWhitelistNewsDataProvider(sites=["https://www.stcn.com"], timeout=3)

        homepage_html = """
        <html><body>
        <a href="/article/1">兴森科技订单增长，PCB景气回暖</a>
        <a href="/article/2">市场综述：指数震荡分化</a>
        </body></html>
        """
        article_html = """
        <html><body>
        兴森科技（002436）公告显示订单增长，景气改善，行业需求回暖。
        </body></html>
        """

        def fake_fetch(url: str) -> str:
            if url.endswith("/article/1"):
                return article_html
            return homepage_html

        with patch.object(provider, "_fetch_site_text", side_effect=fake_fetch):
            result = provider.validate_recommendation_logic(
                stock_code="002436",
                logic_text="兴森科技订单增长，景气回暖",
                trading_date=date(2026, 3, 1),
            )
        self.assertTrue(result)

    def test_sites_provider_discovery(self) -> None:
        provider = SiteWhitelistNewsDataProvider(sites=["https://www.stcn.com"], timeout=3)
        homepage_html = """
        <html><body>
        <a href="/article/1">兴森科技(002436)订单增长，景气改善</a>
        </body></html>
        """
        article_html = """
        <html><body>
        兴森科技(002436)公告显示订单增长，项目持续兑现。
        </body></html>
        """

        def fake_fetch(url: str) -> str:
            if url.endswith("/article/1"):
                return article_html
            return homepage_html

        with patch.object(provider, "_fetch_site_text", side_effect=fake_fetch):
            items = provider.discover_candidate_stocks(trading_date=date(2026, 3, 1), limit=10)

        self.assertGreaterEqual(len(items), 1)
        self.assertEqual("002436", items[0].stock_code)
        self.assertGreater(items[0].discovery_score, 1.8)

    def test_akshare_intraday_provider_supports_cache_fallback(self) -> None:
        fake_akshare = type(
            "FakeAkshare",
            (),
            {
                "stock_zh_a_hist_min_em": staticmethod(
                    lambda **kwargs: _FakeFrame(
                        [
                            {
                                "时间": "2026-03-08 09:31:00",
                                "开盘": 10.1,
                                "收盘": 10.2,
                                "最高": 10.3,
                                "最低": 10.0,
                                "成交量": 1200,
                                "成交额": 122400,
                                "振幅": 2.1,
                                "涨跌幅": 0.5,
                                "涨跌额": 0.05,
                                "换手率": 0.2,
                            }
                        ]
                    )
                ),
                "stock_intraday_em": staticmethod(
                    lambda **kwargs: _FakeFrame(
                        [
                            {
                                "时间": "09:31:03",
                                "成交价": 10.22,
                                "手数": 35,
                                "买卖盘性质": "买盘",
                            }
                        ]
                    )
                ),
            },
        )

        with patch.dict(sys.modules, {"akshare": fake_akshare}):
            provider = build_intraday_provider(
                "akshare",
                cache_dir=str(self._tmp_intraday_dir),
                request_interval_seconds=0,
                max_retries=0,
            )
            bars, used_cache = provider.get_minute_bars("002436", period="1")
            trades, trade_used_cache = provider.get_trade_ticks("002436")

        self.assertFalse(used_cache)
        self.assertFalse(trade_used_cache)
        self.assertEqual(1, len(bars))
        self.assertEqual(1, len(trades))
        self.assertEqual("2026-03-08 09:31:00", bars[0].timestamp)
        self.assertEqual(10.22, trades[0].price)

        failing_akshare = type(
            "FailingAkshare",
            (),
            {
                "stock_zh_a_hist_min_em": staticmethod(lambda **kwargs: (_ for _ in ()).throw(RuntimeError("blocked"))),
                "stock_intraday_em": staticmethod(lambda **kwargs: (_ for _ in ()).throw(RuntimeError("blocked"))),
            },
        )

        with patch.dict(sys.modules, {"akshare": failing_akshare}):
            provider = build_intraday_provider(
                "akshare",
                cache_dir=str(self._tmp_intraday_dir),
                request_interval_seconds=0,
                max_retries=0,
            )
            bars, used_cache = provider.get_minute_bars("002436", period="1")
            trades, trade_used_cache = provider.get_trade_ticks("002436")

        self.assertTrue(used_cache)
        self.assertTrue(trade_used_cache)
        self.assertEqual(1, len(bars))
        self.assertEqual(1, len(trades))

    def test_intraday_endpoint(self) -> None:
        class _StubIntradayProvider:
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
                            timestamp="2026-03-08 09:31:00",
                            open_price=10.1,
                            close_price=10.2,
                            high_price=10.3,
                            low_price=10.0,
                            volume=1200,
                            amount=122400,
                        )
                    ],
                    False,
                )

            def get_trade_ticks(self, stock_code: str):
                return ([IntradayTrade(timestamp="09:31:03", price=10.22, volume_lot=35, side="买盘")], False)

        app.dependency_overrides[get_intraday_provider] = lambda: _StubIntradayProvider()
        try:
            client = TestClient(app)
            response = client.get("/api/intraday/002436?period=1")
            self.assertEqual(200, response.status_code)
            payload = response.json()
            self.assertEqual("002436", payload["stock_code"])
            self.assertEqual(1, len(payload["bars"]))

            tick_response = client.get("/api/intraday/002436/ticks")
            self.assertEqual(200, tick_response.status_code)
            tick_payload = tick_response.json()
            self.assertEqual(1, len(tick_payload["trades"]))
        finally:
            app.dependency_overrides.clear()

    def test_intraday_storage_roundtrip(self) -> None:
        engine = create_engine(
            "sqlite+pysqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        db = Session(engine)
        try:
            stock = Stock(stock_code="002436", stock_name="兴森科技")
            recommender = Recommender(name="研究员A")
            db.add_all([stock, recommender])
            db.commit()

            service = FinanceAgentService(
                db=db,
                market_provider=MockMarketDataProvider(),
                news_provider=MockNewsDataProvider(),
                notifier=_CollectNotifier(),
            )
            bar_result = service.save_intraday_bars(
                stock_code="002436",
                bars=[
                    IntradayBar(
                        timestamp="2026-03-06 09:31:00",
                        open_price=10.1,
                        close_price=10.2,
                        high_price=10.3,
                        low_price=10.0,
                        volume=1200,
                        amount=122400,
                    )
                ],
                period="1",
            )
            tick_result = service.save_intraday_ticks(
                stock_code="002436",
                trades=[IntradayTrade(timestamp="09:31:03", price=10.22, volume_lot=35, side="买盘")],
                trading_day=date(2026, 3, 6),
            )

            bars = service.list_intraday_bars_from_storage("002436", period="1")
            ticks = service.list_intraday_ticks_from_storage("002436")
            summary = service.get_intraday_storage_summary("002436")

            self.assertEqual(1, bar_result["saved"])
            self.assertEqual(1, tick_result["saved"])
            self.assertEqual(1, len(bars))
            self.assertEqual(1, len(ticks))
            self.assertEqual(1, summary["bar_count"])
            self.assertEqual(1, summary["tick_count"])
        finally:
            db.close()
            engine.dispose()

    def test_intraday_sync_endpoint_persists_and_supports_batch(self) -> None:
        class _StubIntradayProvider:
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
                            timestamp="2026-03-06 09:31:00",
                            open_price=10.1,
                            close_price=10.2,
                            high_price=10.3,
                            low_price=10.0,
                            volume=1200,
                            amount=122400,
                        )
                    ],
                    False,
                )

            def get_trade_ticks(self, stock_code: str):
                return ([IntradayTrade(timestamp="09:31:03", price=10.22, volume_lot=35, side="买盘")], False)

        engine = create_engine(
            "sqlite+pysqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        db = Session(engine)
        try:
            recommender = Recommender(name="研究员A")
            stocks = [Stock(stock_code="002436", stock_name="兴森科技"), Stock(stock_code="002384", stock_name="东山精密")]
            db.add(recommender)
            db.add_all(stocks)
            db.commit()
            for stock in stocks:
                db.add(
                    Recommendation(
                        stock_id=stock.id,
                        recommender_id=recommender.id,
                        original_message="测试",
                        extracted_logic="测试逻辑",
                        status="tracking",
                        source="manual",
                    )
                )
            db.commit()

            service = FinanceAgentService(
                db=db,
                market_provider=MockMarketDataProvider(),
                news_provider=MockNewsDataProvider(),
                notifier=_CollectNotifier(),
            )
            app.dependency_overrides[get_service] = lambda: service
            app.dependency_overrides[get_intraday_provider] = lambda: _StubIntradayProvider()
            client = TestClient(app)

            sync_response = client.post("/api/intraday/002436/sync?period=1&include_ticks=true")
            self.assertEqual(200, sync_response.status_code)
            sync_payload = sync_response.json()
            self.assertEqual(1, sync_payload["saved_bars"])

            stored_response = client.get("/api/intraday/002436?use_storage=true&period=1")
            self.assertEqual(200, stored_response.status_code)
            stored_payload = stored_response.json()
            self.assertEqual(1, len(stored_payload["bars"]))

            batch_response = client.post(
                "/api/intraday/batch_sync",
                json={"period": "1", "include_ticks": True, "limit": 2},
            )
            self.assertEqual(200, batch_response.status_code)
            batch_payload = batch_response.json()
            self.assertEqual(2, batch_payload["success_count"])
        finally:
            app.dependency_overrides.clear()
            db.close()
            engine.dispose()

    def test_system_check_endpoint(self) -> None:
        engine = create_engine(
            "sqlite+pysqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        db = Session(engine)

        service = FinanceAgentService(
            db=db,
            market_provider=MockMarketDataProvider(),
            news_provider=MockNewsDataProvider(),
            notifier=_CollectNotifier(),
        )

        app.dependency_overrides[get_service] = lambda: service
        try:
            client = TestClient(app)
            response = client.get("/api/system/check")
            self.assertEqual(200, response.status_code)
            payload = response.json()
            self.assertTrue(payload["database"])
            self.assertTrue(payload["market_provider"])
            self.assertTrue(payload["news_provider"])
        finally:
            app.dependency_overrides.clear()
            db.close()
            engine.dispose()

    def test_research_ingest_endpoint(self) -> None:
        engine = create_engine(
            "sqlite+pysqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        db = Session(engine)

        service = FinanceAgentService(
            db=db,
            market_provider=MockMarketDataProvider(),
            news_provider=MockNewsDataProvider(),
            notifier=_CollectNotifier(),
            daily_report_dir="tests/.tmp_reports",
        )

        app.dependency_overrides[get_service] = lambda: service
        try:
            client = TestClient(app)
            response = client.post(
                "/api/research/ingest",
                json={"text": "宏观观察：资金偏防御，暂无明确个股推荐", "operator_name": "研究员A"},
            )
            self.assertEqual(200, response.status_code)
            payload = response.json()
            self.assertEqual(1, payload["saved"])
        finally:
            app.dependency_overrides.clear()
            db.close()
            engine.dispose()

    def test_openclaw_qq_webhook_ingests_recommendation(self) -> None:
        engine = create_engine(
            "sqlite+pysqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        db = Session(engine)

        service = FinanceAgentService(
            db=db,
            market_provider=MockMarketDataProvider(),
            news_provider=MockNewsDataProvider(),
            notifier=_CollectNotifier(),
        )

        app.dependency_overrides[get_service] = lambda: service
        try:
            client = TestClient(app)
            response = client.post(
                "/api/connectors/openclaw/webhook",
                json={
                    "channel": "qq",
                    "message": "002436 看好，逻辑是订单增长与景气回暖",
                    "sender_name": "QQ群友A",
                    "sender_id": "qq_user_1",
                    "group_name": "测试群",
                },
            )
            self.assertEqual(200, response.status_code)
            payload = response.json()
            self.assertEqual("ingest", payload["action"])
            self.assertEqual(1, payload["created"])

            record = db.query(Recommendation).one()
            self.assertEqual("openclaw_qq", record.source)
            self.assertEqual("002436", record.stock.stock_code)
        finally:
            app.dependency_overrides.clear()
            db.close()
            engine.dispose()

    def test_openclaw_qq_webhook_handles_command(self) -> None:
        engine = create_engine(
            "sqlite+pysqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        db = Session(engine)

        service = FinanceAgentService(
            db=db,
            market_provider=MockMarketDataProvider(),
            news_provider=MockNewsDataProvider(),
            notifier=_CollectNotifier(),
        )
        service.add_manual_recommendation("002436", "测试逻辑", "QQ群友A", stock_name="兴森科技")

        app.dependency_overrides[get_service] = lambda: service
        try:
            client = TestClient(app)
            response = client.post(
                "/api/connectors/qq/webhook",
                json={
                    "channel": "qq",
                    "text": "/who QQ群友A",
                    "sender_name": "QQ群友A",
                    "sender_id": "qq_user_1",
                    "group_name": "测试群",
                },
            )
            self.assertEqual(200, response.status_code)
            payload = response.json()
            self.assertEqual("command", payload["action"])
            self.assertIn("QQ群友A", payload["reply_message"])
            self.assertIn("历史推荐", payload["reply_message"])
        finally:
            app.dependency_overrides.clear()
            db.close()
            engine.dispose()

    def test_qq_webhook_accepts_onebot_group_payload(self) -> None:
        engine = create_engine(
            "sqlite+pysqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        db = Session(engine)

        service = FinanceAgentService(
            db=db,
            market_provider=MockMarketDataProvider(),
            news_provider=MockNewsDataProvider(),
            notifier=_CollectNotifier(),
        )

        app.dependency_overrides[get_service] = lambda: service
        try:
            client = TestClient(app)
            response = client.post(
                "/api/connectors/qq/webhook",
                json={
                    "post_type": "message",
                    "message_type": "group",
                    "raw_message": "002384 看好，逻辑是消费电子复苏",
                    "group_id": 123456,
                    "user_id": 1612085779,
                    "sender": {"nickname": "QQ群友A"},
                },
            )
            self.assertEqual(200, response.status_code)
            payload = response.json()
            self.assertEqual("ingest", payload["action"])
            self.assertEqual(1, payload["created"])
            self.assertEqual("qq", payload["channel"])
        finally:
            app.dependency_overrides.clear()
            db.close()
            engine.dispose()
