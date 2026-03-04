from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from difflib import SequenceMatcher
import re
from urllib.parse import urljoin, urlparse
from typing import Optional

import requests


class ProviderError(RuntimeError):
    pass


@dataclass
class MarketSnapshot:
    close_price: float
    high_price: float
    low_price: float
    pnl_percent: float
    max_drawdown: float
    sharpe_ratio: float
    market_cap_score: float
    elasticity_score: float
    liquidity_score: float


@dataclass
class NewsDiscoveryItem:
    stock_code: str
    stock_name: str
    headline: str
    summary: str
    source_site: str
    source_url: str
    event_type: str
    discovery_score: float


class MarketDataProvider:
    def health_check(self) -> bool:
        return True

    def get_stock_name(self, stock_code: str) -> str:
        return ""

    def get_daily_snapshot(self, stock_code: str, trading_date: date) -> MarketSnapshot:
        raise NotImplementedError


class MockMarketDataProvider(MarketDataProvider):
    def get_stock_name(self, stock_code: str) -> str:
        return f"模拟股票{stock_code}"

    def get_daily_snapshot(self, stock_code: str, trading_date: date) -> MarketSnapshot:
        seed = (sum(ord(char) for char in stock_code) + trading_date.day) % 100
        close_price = 10.0 + seed
        high_price = close_price * 1.02
        low_price = close_price * 0.98
        pnl_percent = (seed % 25) - 8
        max_drawdown = -float((seed % 12) + 1)
        sharpe_ratio = round((seed % 20) / 10.0 - 0.5, 2)
        return MarketSnapshot(
            close_price=round(close_price, 2),
            high_price=round(high_price, 2),
            low_price=round(low_price, 2),
            pnl_percent=float(pnl_percent),
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe_ratio,
            market_cap_score=float(45 + seed % 45),
            elasticity_score=float(40 + seed % 50),
            liquidity_score=float(50 + seed % 40),
        )


class BaostockMarketDataProvider(MarketDataProvider):
    def __init__(self) -> None:
        self._logged_in = False

    def _ensure_login(self) -> None:
        if self._logged_in:
            return
        try:
            import baostock as bs
        except Exception as exc:  # pragma: no cover
            raise ProviderError("baostock 未安装") from exc

        result = bs.login()
        if result.error_code != "0":
            raise ProviderError(f"baostock 登录失败: {result.error_msg}")
        self._logged_in = True

    @staticmethod
    def _to_bs_code(stock_code: str) -> str:
        prefix = "sh" if stock_code.startswith(("60", "68")) else "sz"
        return f"{prefix}.{stock_code}"

    def health_check(self) -> bool:
        try:
            self._ensure_login()
            return True
        except Exception:
            return False

    def get_stock_name(self, stock_code: str) -> str:
        self._ensure_login()
        import baostock as bs

        rs = bs.query_stock_basic(code=self._to_bs_code(stock_code))
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        if not rows:
            return ""
        if len(rows[-1]) >= 2:
            return rows[-1][1]
        return ""

    def get_daily_snapshot(self, stock_code: str, trading_date: date) -> MarketSnapshot:
        self._ensure_login()
        import baostock as bs

        bs_code = self._to_bs_code(stock_code)
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,open,high,low,close,volume,amount,pctChg",
            start_date=trading_date.isoformat(),
            end_date=trading_date.isoformat(),
            frequency="d",
            adjustflag="2",
        )
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())

        if not rows:
            start_date = trading_date - timedelta(days=14)
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,open,high,low,close,volume,amount,pctChg",
                start_date=start_date.isoformat(),
                end_date=trading_date.isoformat(),
                frequency="d",
                adjustflag="2",
            )
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())

        if not rows:
            raise ProviderError(f"baostock 未返回 {stock_code} 在 {trading_date} 及近14日的行情")

        _, _open, high, low, close, volume, amount, pct_chg = rows[-1]
        close_price = float(close)
        high_price = float(high)
        low_price = float(low)
        pnl_percent = float(pct_chg) if pct_chg else 0.0

        if high_price <= 0:
            max_drawdown = -1.0
        else:
            max_drawdown = ((low_price / high_price) - 1.0) * 100

        elasticity = ((high_price - low_price) / max(close_price, 1e-6)) * 100
        amount_value = float(amount) if amount else 0.0
        market_cap_score = 60.0
        elasticity_score = max(20.0, min(95.0, 20.0 + elasticity * 8.0))
        liquidity_score = max(20.0, min(95.0, 20.0 + (amount_value / 1e8) * 8.0))
        sharpe_ratio = pnl_percent / max(abs(max_drawdown), 1.0)

        return MarketSnapshot(
            close_price=close_price,
            high_price=high_price,
            low_price=low_price,
            pnl_percent=pnl_percent,
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe_ratio,
            market_cap_score=market_cap_score,
            elasticity_score=elasticity_score,
            liquidity_score=liquidity_score,
        )


class NewsDataProvider:
    def health_check(self) -> bool:
        return True

    def validate_recommendation_logic(self, stock_code: str, logic_text: str, trading_date: date) -> bool:
        raise NotImplementedError

    def discover_candidate_stocks(self, trading_date: date, limit: int = 30) -> list[NewsDiscoveryItem]:
        return []


class MockNewsDataProvider(NewsDataProvider):
    def validate_recommendation_logic(self, stock_code: str, logic_text: str, trading_date: date) -> bool:
        hints = ("业绩", "中标", "订单", "景气", "增长", "回暖", "改善")
        return any(hint in logic_text for hint in hints)


class TushareNewsDataProvider(NewsDataProvider):
    def __init__(self, token: str) -> None:
        self.token = token
        self._pro = None

    def _ensure_client(self):
        if self._pro is not None:
            return self._pro
        if not self.token:
            raise ProviderError("未配置 Tushare Token")
        try:
            import tushare as ts
        except Exception as exc:  # pragma: no cover
            raise ProviderError("tushare 未安装") from exc
        ts.set_token(self.token)
        self._pro = ts.pro_api()
        return self._pro

    def health_check(self) -> bool:
        try:
            pro = self._ensure_client()
            pro.trade_cal(start_date="20260101", end_date="20260105")
            return True
        except Exception:
            return False

    def validate_recommendation_logic(self, stock_code: str, logic_text: str, trading_date: date) -> bool:
        pro = self._ensure_client()
        ts_code = (f"{stock_code}.SH" if stock_code.startswith(("60", "68")) else f"{stock_code}.SZ")
        df = pro.news(
            src="sina",
            start_date=(trading_date.isoformat().replace("-", "") + " 00:00:00"),
            end_date=(trading_date.isoformat().replace("-", "") + " 23:59:59"),
        )
        if df is None or df.empty:
            return False
        text = " ".join(df.get("title", []).tolist())
        keywords = [item for item in logic_text.replace("，", " ").replace("。", " ").split() if len(item) >= 2]
        if ts_code in text:
            return True
        return any(keyword in text for keyword in keywords)


class HttpWebhookNewsDataProvider(NewsDataProvider):
    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint

    def health_check(self) -> bool:
        if not self.endpoint:
            return False
        try:
            response = requests.get(self.endpoint, timeout=3)
            return response.status_code < 500
        except Exception:
            return False

    def validate_recommendation_logic(self, stock_code: str, logic_text: str, trading_date: date) -> bool:
        if not self.endpoint:
            raise ProviderError("未配置 NEWS_WEBHOOK_URL")
        try:
            payload = {
                "stock_code": stock_code,
                "logic_text": logic_text,
                "trading_date": trading_date.isoformat(),
            }
            response = requests.post(self.endpoint, json=payload, timeout=5)
            response.raise_for_status()
            data = response.json()
            return bool(data.get("validated", False))
        except Exception as exc:
            raise ProviderError(f"webhook新闻验证失败: {exc}") from exc


class SiteWhitelistNewsDataProvider(NewsDataProvider):
    DEFAULT_SITES = [
        "https://www.eastmoney.com",
        "https://finance.sina.com.cn",
        "https://www.stcn.com",
        "https://www.cnstock.com",
    ]

    def __init__(self, sites: Optional[list[str]] = None, timeout: int = 5) -> None:
        candidate_sites = sites or self.DEFAULT_SITES
        self.sites = [item.strip() for item in candidate_sites if item and item.strip()]
        if not self.sites:
            self.sites = list(self.DEFAULT_SITES)
        self.timeout = timeout
        self._event_keywords: dict[str, tuple[str, ...]] = {
            "earnings": ("业绩", "预增", "利润", "增长", "超预期", "年报", "季报"),
            "order": ("订单", "中标", "签约", "项目", "合同"),
            "policy": ("政策", "扶持", "指导意见", "规划", "改革"),
            "risk": ("处罚", "问询", "减持", "亏损", "诉讼", "风险提示"),
        }

    @staticmethod
    def _extract_keywords(logic_text: str) -> list[str]:
        parts = re.split(r"[\s,，。；;：:、|/()（）\[\]{}<>]+", logic_text or "")
        keywords = [item.strip() for item in parts if len(item.strip()) >= 2]
        dedup: list[str] = []
        for key in keywords:
            if key not in dedup:
                dedup.append(key)
        return dedup[:12]

    @staticmethod
    def _extract_stock_codes(text: str) -> list[str]:
        matches = re.findall(r"\b((?:60|00|30|68)\d{4})\b", text or "")
        dedup: list[str] = []
        for code in matches:
            if code not in dedup:
                dedup.append(code)
        return dedup

    @staticmethod
    def _extract_stock_name_candidates(text: str) -> list[str]:
        names = re.findall(r"([\u4e00-\u9fa5]{2,10})(?:\(|（)?(?:60|00|30|68)\d{4}", text or "")
        dedup: list[str] = []
        for name in names:
            if name not in dedup:
                dedup.append(name)
        return dedup

    def _detect_event_type(self, text: str) -> str:
        normalized = text or ""
        best_type = "generic"
        best_hits = 0
        for event_type, words in self._event_keywords.items():
            hits = sum(1 for word in words if word in normalized)
            if hits > best_hits:
                best_type = event_type
                best_hits = hits
        return best_type

    def _score_discovery(self, title: str, content: str, event_type: str) -> float:
        base = 0.8
        text = f"{title} {content}"
        code_hits = len(self._extract_stock_codes(text))
        base += min(2.0, code_hits * 0.8)
        name_hits = len(self._extract_stock_name_candidates(text))
        base += min(1.5, name_hits * 0.5)
        keyword_hits = 0
        if event_type in self._event_keywords:
            keyword_hits = sum(1 for word in self._event_keywords[event_type] if word in text)
        base += min(2.2, keyword_hits * 0.6)
        if event_type == "risk":
            base -= 0.8
        return round(max(0.0, min(5.0, base)), 2)

    def _fetch_site_text(self, url: str) -> str:
        response = requests.get(
            url,
            timeout=self.timeout,
            headers={"User-Agent": "Mozilla/5.0 (dgq-finance-agent news validator)"},
        )
        response.raise_for_status()
        return response.text[:300000]

    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r"\s+", "", (text or "").lower())

    @staticmethod
    def _extract_links(base_url: str, html_text: str) -> list[tuple[str, str]]:
        pattern = re.compile(r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
        matches = pattern.findall(html_text or "")
        links: list[tuple[str, str]] = []
        seen: set[str] = set()
        base_host = urlparse(base_url).netloc
        for href, raw_title in matches:
            title = re.sub(r"<[^>]+>", "", raw_title)
            title = re.sub(r"\s+", " ", title).strip()
            if len(title) < 6:
                continue
            abs_url = urljoin(base_url, href)
            parsed = urlparse(abs_url)
            if parsed.scheme not in {"http", "https"}:
                continue
            if parsed.netloc and parsed.netloc != base_host:
                continue
            normalized = abs_url.split("#")[0]
            if normalized in seen:
                continue
            seen.add(normalized)
            links.append((title[:120], normalized))
            if len(links) >= 40:
                break
        return links

    @staticmethod
    def _title_semantic_score(title: str, logic_text: str, keywords: list[str], code_tokens: list[str]) -> float:
        title_norm = SiteWhitelistNewsDataProvider._normalize_text(title)
        logic_norm = SiteWhitelistNewsDataProvider._normalize_text(logic_text)
        score = 0.0

        keyword_hits = sum(1 for keyword in keywords if keyword and keyword in title)
        score += keyword_hits * 1.4

        if any(token and token.lower() in title_norm for token in code_tokens):
            score += 2.0

        if logic_norm and title_norm:
            ratio = SequenceMatcher(None, title_norm, logic_norm).ratio()
            if ratio >= 0.55:
                score += 3.0
            elif ratio >= 0.4:
                score += 1.5
        return score

    @staticmethod
    def _content_signal_score(content: str, keywords: list[str], code_tokens: list[str]) -> float:
        text = content or ""
        text_norm = SiteWhitelistNewsDataProvider._normalize_text(text)
        score = 0.0
        code_hit = any(token and token.lower() in text_norm for token in code_tokens)
        if code_hit:
            score += 2.0
        keyword_hits = sum(1 for keyword in keywords if keyword and keyword in text)
        if keyword_hits >= 3:
            score += 3.0
        elif keyword_hits == 2:
            score += 2.0
        elif keyword_hits == 1:
            score += 0.8
        return score

    def health_check(self) -> bool:
        success_count = 0
        for url in self.sites:
            try:
                self._fetch_site_text(url)
                success_count += 1
            except Exception:
                continue
        return success_count > 0

    def validate_recommendation_logic(self, stock_code: str, logic_text: str, trading_date: date) -> bool:
        keywords = self._extract_keywords(logic_text)
        code_tokens = [stock_code, f"{stock_code}.sz", f"{stock_code}.sh"]
        strong_sites = 0
        weak_sites = 0
        for url in self.sites:
            try:
                homepage = self._fetch_site_text(url)
            except Exception:
                continue

            links = self._extract_links(url, homepage)
            homepage_score = self._content_signal_score(homepage, keywords, code_tokens)
            best_score = homepage_score

            ranked_links = sorted(
                links,
                key=lambda item: self._title_semantic_score(item[0], logic_text, keywords, code_tokens),
                reverse=True,
            )

            for title, article_url in ranked_links[:8]:
                title_score = self._title_semantic_score(title, logic_text, keywords, code_tokens)
                if title_score < 1.2:
                    continue
                detail_score = title_score
                try:
                    detail_text = self._fetch_site_text(article_url)
                    detail_score += self._content_signal_score(detail_text, keywords, code_tokens)
                except Exception:
                    pass
                if detail_score > best_score:
                    best_score = detail_score

            if best_score >= 4.0:
                strong_sites += 1
            elif best_score >= 2.2:
                weak_sites += 1

        return strong_sites >= 1 or (strong_sites == 0 and weak_sites >= 2)

    def discover_candidate_stocks(self, trading_date: date, limit: int = 30) -> list[NewsDiscoveryItem]:
        candidates: list[NewsDiscoveryItem] = []
        seen: set[tuple[str, str]] = set()

        for site in self.sites:
            try:
                homepage = self._fetch_site_text(site)
            except Exception:
                continue

            links = self._extract_links(site, homepage)
            for title, article_url in links[:20]:
                text_blob = title
                try:
                    detail_text = self._fetch_site_text(article_url)
                    text_blob = f"{title} {detail_text[:3000]}"
                except Exception:
                    detail_text = ""

                stock_codes = self._extract_stock_codes(text_blob)
                if not stock_codes:
                    continue

                event_type = self._detect_event_type(text_blob)
                score = self._score_discovery(title=title, content=detail_text, event_type=event_type)
                if score < 1.8:
                    continue

                name_candidates = self._extract_stock_name_candidates(text_blob)
                stock_name = name_candidates[0] if name_candidates else ""

                for code in stock_codes:
                    fingerprint = (code, article_url)
                    if fingerprint in seen:
                        continue
                    seen.add(fingerprint)
                    candidates.append(
                        NewsDiscoveryItem(
                            stock_code=code,
                            stock_name=stock_name,
                            headline=title,
                            summary=(detail_text or title)[:220],
                            source_site=site,
                            source_url=article_url,
                            event_type=event_type,
                            discovery_score=score,
                        )
                    )
                    if len(candidates) >= limit:
                        return sorted(candidates, key=lambda item: item.discovery_score, reverse=True)

        return sorted(candidates, key=lambda item: item.discovery_score, reverse=True)[:limit]


class WeChatConnector:
    def sync_history(self) -> int:
        raise NotImplementedError

    def start_realtime_listener(self) -> None:
        raise NotImplementedError


class WechatyConnectorPlaceholder(WeChatConnector):
    def sync_history(self) -> int:
        return 0

    def start_realtime_listener(self) -> None:
        return None


def build_market_provider(name: str) -> MarketDataProvider:
    normalized = (name or "mock").strip().lower()
    if normalized == "baostock":
        return BaostockMarketDataProvider()
    if normalized == "mock":
        return MockMarketDataProvider()
    raise ProviderError(f"未知行情provider: {name}")


def build_news_provider(
    name: str,
    tushare_token: str = "",
    news_webhook_url: str = "",
    news_site_whitelist: str = "",
    news_site_timeout: int = 5,
) -> NewsDataProvider:
    normalized = (name or "mock").strip().lower()
    if normalized == "tushare":
        return TushareNewsDataProvider(token=tushare_token)
    if normalized == "webhook":
        return HttpWebhookNewsDataProvider(endpoint=news_webhook_url)
    if normalized in {"sites", "site_whitelist", "website"}:
        sites = [item.strip() for item in (news_site_whitelist or "").split(",") if item.strip()]
        return SiteWhitelistNewsDataProvider(sites=sites, timeout=max(news_site_timeout, 1))
    if normalized == "mock":
        return MockNewsDataProvider()
    raise ProviderError(f"未知新闻provider: {name}")
