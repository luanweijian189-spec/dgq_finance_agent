from __future__ import annotations

from datetime import date
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.main import app, get_service
from app.database import Base
from app.notifier import AlertNotifier
from app.providers import (
    MockMarketDataProvider,
    MockNewsDataProvider,
    SiteWhitelistNewsDataProvider,
    build_market_provider,
    build_news_provider,
)
from app.services import FinanceAgentService


class _CollectNotifier(AlertNotifier):
    def send(self, title: str, content: str) -> None:
        return None


class SystemCheckTests(TestCase):
    def test_provider_factory(self) -> None:
        self.assertIsInstance(build_market_provider("mock"), MockMarketDataProvider)
        self.assertIsInstance(build_news_provider("mock"), MockNewsDataProvider)
        self.assertIsInstance(
            build_news_provider("sites", news_site_whitelist="https://www.eastmoney.com"),
            SiteWhitelistNewsDataProvider,
        )

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
