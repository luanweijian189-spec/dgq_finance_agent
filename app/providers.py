from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
import hashlib
import importlib
import json
from pathlib import Path
import re
from time import monotonic, sleep
from urllib.parse import urljoin, urlparse
from typing import Optional

import requests


class ProviderError(RuntimeError):
    pass


@dataclass
class MarketSnapshot:
    snapshot_date: date
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


@dataclass
class IntradayBar:
    timestamp: str
    open_price: float
    close_price: float
    high_price: float
    low_price: float
    volume: float
    amount: float
    amplitude: float = 0.0
    change_percent: float = 0.0
    change_amount: float = 0.0
    turnover_rate: float = 0.0


@dataclass
class IntradayTrade:
    timestamp: str
    price: float
    volume_lot: float
    side: str = ""


class MarketDataProvider:
    def health_check(self) -> bool:
        return True

    def get_stock_name(self, stock_code: str) -> str:
        return ""

    def search_stock_candidates(self, query: str, limit: int = 5) -> list[tuple[str, str]]:
        return []

    def get_daily_snapshot(self, stock_code: str, trading_date: date) -> MarketSnapshot:
        raise NotImplementedError


class IntradayDataProvider:
    def health_check(self) -> bool:
        return True

    def get_minute_bars(
        self,
        stock_code: str,
        period: str = "1",
        adjust: str = "",
        start_datetime: Optional[datetime] = None,
        end_datetime: Optional[datetime] = None,
    ) -> tuple[list[IntradayBar], bool]:
        raise NotImplementedError

    def get_trade_ticks(self, stock_code: str) -> tuple[list[IntradayTrade], bool]:
        raise NotImplementedError


class MockMarketDataProvider(MarketDataProvider):
    def get_stock_name(self, stock_code: str) -> str:
        return f"模拟股票{stock_code}"

    def search_stock_candidates(self, query: str, limit: int = 5) -> list[tuple[str, str]]:
        return []

    def get_daily_snapshot(self, stock_code: str, trading_date: date) -> MarketSnapshot:
        seed = (sum(ord(char) for char in stock_code) + trading_date.day) % 100
        close_price = 10.0 + seed
        high_price = close_price * 1.02
        low_price = close_price * 0.98
        pnl_percent = (seed % 25) - 8
        max_drawdown = -float((seed % 12) + 1)
        sharpe_ratio = round((seed % 20) / 10.0 - 0.5, 2)
        return MarketSnapshot(
            snapshot_date=trading_date,
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
        self._stock_catalog: Optional[list[tuple[str, str]]] = None

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

    @staticmethod
    def _to_plain_code(stock_code: str) -> str:
        value = (stock_code or "").strip().lower()
        if "." in value:
            return value.split(".", 1)[1]
        return value

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

    def _load_stock_catalog(self) -> list[tuple[str, str]]:
        if self._stock_catalog is not None:
            return self._stock_catalog

        self._ensure_login()
        import baostock as bs

        as_of = date.today()
        while as_of.weekday() >= 5:
            as_of -= timedelta(days=1)

        rs = bs.query_all_stock(as_of.isoformat())
        rows: list[tuple[str, str]] = []
        while rs.error_code == "0" and rs.next():
            item = rs.get_row_data()
            if len(item) < 3:
                continue
            code = self._to_plain_code(item[0])
            name = (item[2] or "").strip()
            if not code or not name:
                continue
            if not re.fullmatch(r"\d{6}", code):
                continue
            rows.append((code, name))
        self._stock_catalog = rows
        return rows

    def search_stock_candidates(self, query: str, limit: int = 5) -> list[tuple[str, str]]:
        keyword = (query or "").strip()
        if not keyword:
            return []

        catalog = self._load_stock_catalog()
        exact: list[tuple[str, str]] = []
        partial: list[tuple[str, str]] = []
        for code, name in catalog:
            if name == keyword:
                exact.append((code, name))
            elif keyword in name:
                partial.append((code, name))

        return (exact + partial)[: max(limit, 1)]

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

        row_date, _open, high, low, close, volume, amount, pct_chg = rows[-1]
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
            snapshot_date=date.fromisoformat(row_date),
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


class AkshareIntradayDataProvider(IntradayDataProvider):
    def __init__(
        self,
        cache_dir: str = "data/intraday",
        request_interval_seconds: float = 1.2,
        max_retries: int = 2,
        provider_label: str = "AKShare",
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.request_interval_seconds = max(request_interval_seconds, 0.0)
        self.max_retries = max(max_retries, 0)
        self._last_request_at = 0.0
        self.provider_label = provider_label

    @staticmethod
    def _normalize_stock_code(stock_code: str) -> str:
        normalized = re.sub(r"\D", "", stock_code or "")
        if not re.fullmatch(r"\d{6}", normalized):
            raise ProviderError(f"分时接口要求 6 位股票代码，收到: {stock_code}")
        return normalized

    @staticmethod
    def _format_datetime(value: Optional[datetime]) -> str:
        if value is None:
            return ""
        return value.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _safe_text(record: dict, *keys: str) -> str:
        for key in keys:
            value = record.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    @staticmethod
    def _safe_float(record: dict, *keys: str) -> float:
        for key in keys:
            value = record.get(key)
            if value in (None, "", "-"):
                continue
            try:
                return float(str(value).replace(",", ""))
            except (TypeError, ValueError):
                continue
        return 0.0

    def _rate_limit(self) -> None:
        if self.request_interval_seconds <= 0:
            return
        elapsed = monotonic() - self._last_request_at
        wait_seconds = self.request_interval_seconds - elapsed
        if wait_seconds > 0:
            sleep(wait_seconds)
        self._last_request_at = monotonic()

    def _cache_path(self, stock_code: str, data_type: str, **params: str) -> Path:
        digest = hashlib.sha1(
            json.dumps(params, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]
        return self.cache_dir / stock_code / f"{data_type}_{digest}.json"

    @staticmethod
    def _write_cache(path: Path, items: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "items": items,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _load_cache(path: Path) -> list[dict]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return []
        items = payload.get("items", [])
        return items if isinstance(items, list) else []

    def _load_akshare(self):
        try:
            ak = importlib.import_module("akshare")
        except Exception as exc:  # pragma: no cover
            raise ProviderError("akshare 未安装，无法获取分时数据") from exc
        return ak

    def health_check(self) -> bool:
        try:
            self._load_akshare()
            return True
        except Exception:
            return False

    def _fetch_with_retry(self, fetcher, cache_path: Path, loader):
        last_error: Optional[Exception] = None
        for _ in range(self.max_retries + 1):
            try:
                self._rate_limit()
                items = fetcher()
                self._write_cache(cache_path, items)
                return loader(items), False
            except Exception as exc:
                last_error = exc
                sleep(0.8)

        if cache_path.exists():
            return loader(self._load_cache(cache_path)), True

        if isinstance(last_error, ProviderError):
            raise last_error
        raise ProviderError(f"{self.provider_label} 分时请求失败: {last_error}") from last_error

    def get_minute_bars(
        self,
        stock_code: str,
        period: str = "1",
        adjust: str = "",
        start_datetime: Optional[datetime] = None,
        end_datetime: Optional[datetime] = None,
    ) -> tuple[list[IntradayBar], bool]:
        normalized_code = self._normalize_stock_code(stock_code)
        cache_path = self._cache_path(
            normalized_code,
            "bars",
            period=period,
            adjust=adjust or "",
            start=self._format_datetime(start_datetime),
            end=self._format_datetime(end_datetime),
        )

        def fetcher() -> list[dict]:
            ak = self._load_akshare()
            kwargs = {"symbol": normalized_code, "period": str(period), "adjust": adjust or ""}
            if start_datetime is not None:
                kwargs["start_date"] = self._format_datetime(start_datetime)
            if end_datetime is not None:
                kwargs["end_date"] = self._format_datetime(end_datetime)
            df = ak.stock_zh_a_hist_min_em(**kwargs)
            if df is None or getattr(df, "empty", True):
                raise ProviderError(f"AKShare 未返回 {normalized_code} 的分时 K 线")
            return list(df.to_dict(orient="records"))

        def loader(items: list[dict]) -> list[IntradayBar]:
            bars: list[IntradayBar] = []
            for item in items:
                bars.append(
                    IntradayBar(
                        timestamp=self._safe_text(item, "时间", "datetime", "日期时间"),
                        open_price=self._safe_float(item, "开盘", "open"),
                        close_price=self._safe_float(item, "收盘", "close"),
                        high_price=self._safe_float(item, "最高", "high"),
                        low_price=self._safe_float(item, "最低", "low"),
                        volume=self._safe_float(item, "成交量", "volume"),
                        amount=self._safe_float(item, "成交额", "amount"),
                        amplitude=self._safe_float(item, "振幅", "amplitude"),
                        change_percent=self._safe_float(item, "涨跌幅", "pct_change"),
                        change_amount=self._safe_float(item, "涨跌额", "change_amount"),
                        turnover_rate=self._safe_float(item, "换手率", "turnover_rate"),
                    )
                )
            return bars

        return self._fetch_with_retry(fetcher, cache_path, loader)

    def get_trade_ticks(self, stock_code: str) -> tuple[list[IntradayTrade], bool]:
        normalized_code = self._normalize_stock_code(stock_code)
        cache_path = self._cache_path(normalized_code, "ticks")

        def fetcher() -> list[dict]:
            ak = self._load_akshare()
            df = ak.stock_intraday_em(symbol=normalized_code)
            if df is None or getattr(df, "empty", True):
                raise ProviderError(f"AKShare 未返回 {normalized_code} 的逐笔成交")
            return list(df.to_dict(orient="records"))

        def loader(items: list[dict]) -> list[IntradayTrade]:
            trades: list[IntradayTrade] = []
            for item in items:
                trades.append(
                    IntradayTrade(
                        timestamp=self._safe_text(item, "时间", "trade_time", "datetime"),
                        price=self._safe_float(item, "成交价", "price"),
                        volume_lot=self._safe_float(item, "手数", "volume", "volume_lot"),
                        side=self._safe_text(item, "买卖盘性质", "side", "direction"),
                    )
                )
            return trades

        return self._fetch_with_retry(fetcher, cache_path, loader)


class PytdxIntradayDataProvider(AkshareIntradayDataProvider):
    def __init__(
        self,
        cache_dir: str = "data/intraday",
        request_interval_seconds: float = 1.2,
        max_retries: int = 2,
        hosts: str = "",
        bar_count: int = 800,
        tick_limit: int = 2000,
    ):
        super().__init__(
            cache_dir=cache_dir,
            request_interval_seconds=request_interval_seconds,
            max_retries=max_retries,
            provider_label="pytdx",
        )
        self.hosts = hosts
        self.bar_count = max(1, min(int(bar_count or 800), 800))
        self.tick_limit = max(1, int(tick_limit or 2000))
        self._preferred_host: Optional[tuple[str, int]] = None

    @staticmethod
    def _market_for_stock(stock_code: str) -> int:
        if stock_code.startswith(("50", "51", "58", "60", "68", "90")):
            return 1
        return 0

    @staticmethod
    def _category_for_period(period: str) -> int:
        mapping = {
            "1": 8,
            "5": 0,
            "15": 1,
            "30": 2,
            "60": 3,
        }
        normalized = str(period or "1").strip()
        if normalized not in mapping:
            raise ProviderError(f"pytdx 仅支持 1/5/15/30/60 分钟，收到: {period}")
        return mapping[normalized]

    @staticmethod
    def _parse_timestamp_text(value: str) -> Optional[datetime]:
        text = (value or "").strip()
        if not text:
            return None
        text = text.replace("/", "-")
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(text, pattern)
            except ValueError:
                continue
        return None

    @staticmethod
    def _coerce_bar_timestamp(item: dict) -> str:
        direct = AkshareIntradayDataProvider._safe_text(item, "datetime", "时间", "time")
        parsed = PytdxIntradayDataProvider._parse_timestamp_text(direct)
        if parsed is not None:
            return parsed.strftime("%Y-%m-%d %H:%M:%S")

        year = item.get("year")
        month = item.get("month")
        day = item.get("day")
        if year is not None and month is not None and day is not None:
            hour = int(item.get("hour") or 0)
            minute = int(item.get("minute") or 0)
            return f"{int(year):04d}-{int(month):02d}-{int(day):02d} {hour:02d}:{minute:02d}:00"
        return ""

    @staticmethod
    def _normalize_side(value: object) -> str:
        text = str(value).strip().lower()
        mapping = {
            "0": "买盘",
            "1": "卖盘",
            "2": "中性盘",
            "b": "买盘",
            "s": "卖盘",
            "buy": "买盘",
            "sell": "卖盘",
        }
        return mapping.get(text, str(value).strip()) if text else ""

    def _load_pytdx(self):
        try:
            hq_module = importlib.import_module("pytdx.hq")
            host_module = importlib.import_module("pytdx.config.hosts")
        except Exception as exc:  # pragma: no cover
            raise ProviderError("pytdx 未安装，无法获取更稳定的免费盘中数据") from exc
        return hq_module.TdxHq_API, getattr(host_module, "hq_hosts", [])

    def _candidate_hosts(self) -> list[tuple[str, int]]:
        candidates: list[tuple[str, int]] = []
        for raw in (self.hosts or "").split(","):
            text = raw.strip()
            if not text or ":" not in text:
                continue
            host, port = text.rsplit(":", 1)
            try:
                item = (host.strip(), int(port.strip()))
            except ValueError:
                continue
            if item not in candidates:
                candidates.append(item)

        if not candidates:
            _, hq_hosts = self._load_pytdx()
            for row in hq_hosts:
                if len(row) < 3:
                    continue
                item = (str(row[1]).strip(), int(row[2]))
                if item not in candidates:
                    candidates.append(item)
                if len(candidates) >= 12:
                    break

        if self._preferred_host and self._preferred_host in candidates:
            candidates.remove(self._preferred_host)
            candidates.insert(0, self._preferred_host)
        return candidates

    def _call_pytdx(self, caller):
        TdxHq_API, _ = self._load_pytdx()
        last_error: Optional[Exception] = None

        for host, port in self._candidate_hosts():
            api = TdxHq_API(heartbeat=True, auto_retry=True, raise_exception=False)
            try:
                self._rate_limit()
                if not api.connect(host, port):
                    last_error = ProviderError(f"连接 {host}:{port} 失败")
                    continue
                result = caller(api)
                if result is None:
                    raise ProviderError(f"pytdx 从 {host}:{port} 返回空结果")
                self._preferred_host = (host, port)
                return result
            except Exception as exc:
                last_error = exc
            finally:
                try:
                    api.disconnect()
                except Exception:
                    pass

        if isinstance(last_error, ProviderError):
            raise last_error
        raise ProviderError(f"pytdx 未找到可用行情主机: {last_error}") from last_error

    def health_check(self) -> bool:
        try:
            market = self._market_for_stock("000001")
            rows = self._call_pytdx(lambda api: api.get_security_quotes([(market, "000001")]))
            return bool(rows)
        except Exception:
            return False

    def get_minute_bars(
        self,
        stock_code: str,
        period: str = "1",
        adjust: str = "",
        start_datetime: Optional[datetime] = None,
        end_datetime: Optional[datetime] = None,
    ) -> tuple[list[IntradayBar], bool]:
        normalized_code = self._normalize_stock_code(stock_code)
        market = self._market_for_stock(normalized_code)
        category = self._category_for_period(period)
        cache_path = self._cache_path(
            normalized_code,
            "bars_pytdx",
            period=str(period),
            adjust=adjust or "",
            start=self._format_datetime(start_datetime),
            end=self._format_datetime(end_datetime),
        )

        def fetcher() -> list[dict]:
            def caller(api):
                raw = api.get_security_bars(category, market, normalized_code, 0, self.bar_count)
                if not raw:
                    raise ProviderError(f"pytdx 未返回 {normalized_code} 的分钟线")
                df = api.to_df(raw)
                if df is None or getattr(df, "empty", True):
                    raise ProviderError(f"pytdx 未返回 {normalized_code} 的有效分钟线")
                return list(df.to_dict(orient="records"))

            return self._call_pytdx(caller)

        def loader(items: list[dict]) -> list[IntradayBar]:
            bars: list[IntradayBar] = []
            for item in items:
                timestamp = self._coerce_bar_timestamp(item)
                dt = self._parse_timestamp_text(timestamp)
                if not timestamp or dt is None:
                    continue
                if start_datetime and dt < start_datetime:
                    continue
                if end_datetime and dt > end_datetime:
                    continue
                bars.append(
                    IntradayBar(
                        timestamp=dt.strftime("%Y-%m-%d %H:%M:%S"),
                        open_price=self._safe_float(item, "open", "开盘"),
                        close_price=self._safe_float(item, "close", "price", "收盘"),
                        high_price=self._safe_float(item, "high", "最高"),
                        low_price=self._safe_float(item, "low", "最低"),
                        volume=self._safe_float(item, "vol", "volume", "成交量"),
                        amount=self._safe_float(item, "amount", "成交额"),
                    )
                )

            bars.sort(key=lambda item: item.timestamp)
            if not bars:
                raise ProviderError(f"pytdx 未返回 {normalized_code} 的过滤后分钟线")
            return bars

        return self._fetch_with_retry(fetcher, cache_path, loader)

    def get_trade_ticks(self, stock_code: str) -> tuple[list[IntradayTrade], bool]:
        normalized_code = self._normalize_stock_code(stock_code)
        market = self._market_for_stock(normalized_code)
        cache_path = self._cache_path(normalized_code, "ticks_pytdx")

        def fetcher() -> list[dict]:
            def caller(api):
                rows: list[dict] = []
                start = 0
                batch_size = 2000
                while start < self.tick_limit:
                    count = min(batch_size, self.tick_limit - start)
                    batch = api.get_transaction_data(market, normalized_code, start, count)
                    if not batch:
                        break
                    rows.extend(batch)
                    if len(batch) < count:
                        break
                    start += len(batch)
                if not rows:
                    raise ProviderError(f"pytdx 未返回 {normalized_code} 的逐笔成交")
                return rows

            return self._call_pytdx(caller)

        def loader(items: list[dict]) -> list[IntradayTrade]:
            trades: list[IntradayTrade] = []
            for item in items:
                timestamp = self._safe_text(item, "time", "trade_time", "datetime", "时间")
                trades.append(
                    IntradayTrade(
                        timestamp=timestamp,
                        price=self._safe_float(item, "price", "成交价"),
                        volume_lot=self._safe_float(item, "vol", "volume", "成交量", "手数"),
                        side=self._normalize_side(item.get("buyorsell", item.get("side", ""))),
                    )
                )

            trades.sort(key=lambda item: item.timestamp)
            if not trades:
                raise ProviderError(f"pytdx 未返回 {normalized_code} 的有效逐笔成交")
            return trades

        return self._fetch_with_retry(fetcher, cache_path, loader)


class CompositeIntradayDataProvider(IntradayDataProvider):
    def __init__(self, providers: list[tuple[str, IntradayDataProvider]]):
        self.providers = providers

    def health_check(self) -> bool:
        return any(provider.health_check() for _, provider in self.providers)

    def _dispatch(self, method_name: str, *args, **kwargs):
        errors: list[str] = []
        for name, provider in self.providers:
            try:
                return getattr(provider, method_name)(*args, **kwargs)
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        raise ProviderError("多源分时 provider 全部失败: " + "; ".join(errors))

    def get_minute_bars(
        self,
        stock_code: str,
        period: str = "1",
        adjust: str = "",
        start_datetime: Optional[datetime] = None,
        end_datetime: Optional[datetime] = None,
    ) -> tuple[list[IntradayBar], bool]:
        return self._dispatch(
            "get_minute_bars",
            stock_code,
            period=period,
            adjust=adjust,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
        )

    def get_trade_ticks(self, stock_code: str) -> tuple[list[IntradayTrade], bool]:
        return self._dispatch("get_trade_ticks", stock_code)


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
    normalized = (name or "baostock").strip().lower()
    if normalized == "baostock":
        return BaostockMarketDataProvider()
    raise ProviderError(f"未知或不允许的行情provider: {name}，生产模式仅支持 baostock")


def build_intraday_provider(
    name: str,
    cache_dir: str = "data/intraday",
    request_interval_seconds: float = 1.2,
    max_retries: int = 2,
    pytdx_hosts: str = "",
    pytdx_bar_count: int = 800,
    pytdx_tick_limit: int = 2000,
) -> IntradayDataProvider:
    normalized = (name or "akshare").strip().lower()
    if normalized in {"akshare", "aks"}:
        return AkshareIntradayDataProvider(
            cache_dir=cache_dir,
            request_interval_seconds=request_interval_seconds,
            max_retries=max_retries,
        )
    if normalized in {"pytdx", "tdx"}:
        return PytdxIntradayDataProvider(
            cache_dir=cache_dir,
            request_interval_seconds=request_interval_seconds,
            max_retries=max_retries,
            hosts=pytdx_hosts,
            bar_count=pytdx_bar_count,
            tick_limit=pytdx_tick_limit,
        )
    if normalized in {"freebest", "best", "free"}:
        return CompositeIntradayDataProvider(
            [
                (
                    "pytdx",
                    PytdxIntradayDataProvider(
                        cache_dir=cache_dir,
                        request_interval_seconds=request_interval_seconds,
                        max_retries=max_retries,
                        hosts=pytdx_hosts,
                        bar_count=pytdx_bar_count,
                        tick_limit=pytdx_tick_limit,
                    ),
                ),
                (
                    "akshare",
                    AkshareIntradayDataProvider(
                        cache_dir=cache_dir,
                        request_interval_seconds=request_interval_seconds,
                        max_retries=max_retries,
                    ),
                ),
            ]
        )
    raise ProviderError(f"未知或不允许的分时provider: {name}，当前支持 akshare/pytdx/freebest")


def build_news_provider(
    name: str,
    tushare_token: str = "",
    news_webhook_url: str = "",
    news_site_whitelist: str = "",
    news_site_timeout: int = 5,
) -> NewsDataProvider:
    normalized = (name or "sites").strip().lower()
    if normalized == "tushare":
        return TushareNewsDataProvider(token=tushare_token)
    if normalized == "webhook":
        return HttpWebhookNewsDataProvider(endpoint=news_webhook_url)
    if normalized in {"sites", "site_whitelist", "website"}:
        sites = [item.strip() for item in (news_site_whitelist or "").split(",") if item.strip()]
        return SiteWhitelistNewsDataProvider(sites=sites, timeout=max(news_site_timeout, 1))
    raise ProviderError(f"未知或不允许的新闻provider: {name}，生产模式仅支持 tushare/webhook/sites")
