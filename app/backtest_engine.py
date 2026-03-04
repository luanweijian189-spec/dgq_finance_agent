from __future__ import annotations

import itertools
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from statistics import mean, pstdev
from typing import Callable, Optional


@dataclass
class StrategyParams:
    momentum_window: int
    short_momentum_window: int
    volatility_window: int
    top_k: int
    hold_days: int
    weight_momentum: float
    weight_short: float
    weight_volatility: float
    weight_pullback: float


@dataclass
class BacktestMetrics:
    cumulative_return: float
    annualized_return: float
    annualized_volatility: float
    sharpe: float
    max_drawdown: float
    rebalance_count: int
    win_rate: float


@dataclass
class IterationResult:
    params: StrategyParams
    metrics: BacktestMetrics


class BaostockDataSource:
    def __init__(self) -> None:
        self._logged_in = False

    def _ensure_login(self) -> None:
        if self._logged_in:
            return
        import baostock as bs

        result = bs.login()
        if result.error_code != "0":
            raise RuntimeError(f"baostock login failed: {result.error_msg}")
        self._logged_in = True

    def logout(self) -> None:
        if not self._logged_in:
            return
        try:
            import baostock as bs

            bs.logout()
        except Exception:
            pass
        self._logged_in = False

    def get_universe(self, as_of: date, max_stocks: int = 300) -> list[str]:
        self._ensure_login()
        import baostock as bs

        rs = bs.query_all_stock(as_of.isoformat())
        candidates: list[str] = []
        while rs.error_code == "0" and rs.next():
            code = rs.get_row_data()[0]
            if not code:
                continue
            code_lower = code.lower()
            if not (code_lower.startswith("sh.60") or code_lower.startswith("sh.68") or code_lower.startswith("sz.00") or code_lower.startswith("sz.30")):
                continue
            candidates.append(code_lower)

        candidates.sort()
        return candidates[:max_stocks]

    def get_daily_closes(self, stock_code: str, start: date, end: date) -> list[tuple[str, float]]:
        self._ensure_login()
        import baostock as bs

        rs = bs.query_history_k_data_plus(
            stock_code,
            "date,close,volume,tradestatus,isST",
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            frequency="d",
            adjustflag="2",
        )
        rows: list[tuple[str, float]] = []
        while rs.error_code == "0" and rs.next():
            d, close, _volume, tradestatus, is_st = rs.get_row_data()
            if tradestatus != "1":
                continue
            if is_st == "1":
                continue
            try:
                close_value = float(close)
            except Exception:
                continue
            if close_value <= 0:
                continue
            rows.append((d, close_value))
        return rows


class AShareBacktestEngine:
    def __init__(
        self,
        data_source: Optional[BaostockDataSource] = None,
        progress_callback: Optional[Callable[[str, int, int, str], None]] = None,
    ) -> None:
        self.data_source = data_source or BaostockDataSource()
        self.progress_callback = progress_callback

    def _emit_progress(self, stage: str, current: int, total: int, message: str = "") -> None:
        if not self.progress_callback:
            return
        try:
            self.progress_callback(stage, max(0, current), max(1, total), message)
        except Exception:
            pass

    @staticmethod
    def _returns(prices: list[float]) -> list[float]:
        values: list[float] = []
        for i in range(1, len(prices)):
            prev = prices[i - 1]
            now = prices[i]
            if prev <= 0:
                continue
            values.append(now / prev - 1.0)
        return values

    @staticmethod
    def _max_drawdown(nav: list[float]) -> float:
        if not nav:
            return 0.0
        peak = nav[0]
        worst = 0.0
        for v in nav:
            peak = max(peak, v)
            dd = (v / peak - 1.0) if peak > 0 else 0.0
            worst = min(worst, dd)
        return worst

    @staticmethod
    def _calc_metrics(period_returns: list[float], hold_days: int) -> BacktestMetrics:
        if not period_returns:
            return BacktestMetrics(0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0)

        nav = [1.0]
        for r in period_returns:
            nav.append(nav[-1] * (1.0 + r))

        cumulative_return = nav[-1] - 1.0
        yearly_periods = max(1.0, 250.0 / max(hold_days, 1))
        avg_period_return = mean(period_returns)
        annualized_return = (1.0 + avg_period_return) ** yearly_periods - 1.0

        vol = pstdev(period_returns) if len(period_returns) > 1 else 0.0
        annualized_vol = vol * math.sqrt(yearly_periods)
        sharpe = annualized_return / annualized_vol if annualized_vol > 1e-12 else 0.0

        max_drawdown = AShareBacktestEngine._max_drawdown(nav)
        win_rate = sum(1 for x in period_returns if x > 0) / len(period_returns)

        return BacktestMetrics(
            cumulative_return=cumulative_return,
            annualized_return=annualized_return,
            annualized_volatility=annualized_vol,
            sharpe=sharpe,
            max_drawdown=max_drawdown,
            rebalance_count=len(period_returns),
            win_rate=win_rate,
        )

    @staticmethod
    def _score_stock(history: list[float], params: StrategyParams, idx: int) -> Optional[float]:
        max_window = max(params.momentum_window, params.short_momentum_window, params.volatility_window)
        if idx <= max_window:
            return None

        close_now = history[idx - 1]
        close_m = history[idx - params.momentum_window]
        close_s = history[idx - params.short_momentum_window]
        if close_m <= 0 or close_s <= 0 or close_now <= 0:
            return None

        momentum = close_now / close_m - 1.0
        short_momentum = close_now / close_s - 1.0

        window_prices = history[idx - params.volatility_window : idx]
        rets = AShareBacktestEngine._returns(window_prices)
        if not rets:
            return None
        volatility = pstdev(rets) if len(rets) > 1 else 0.0

        max_price = max(window_prices)
        pullback = (max_price - close_now) / max_price if max_price > 0 else 0.0

        return (
            params.weight_momentum * momentum
            + params.weight_short * short_momentum
            - params.weight_volatility * volatility
            - params.weight_pullback * pullback
        )

    def _prepare_market_data(
        self,
        start: date,
        end: date,
        max_stocks: int,
        min_required_rows: int,
        emit_progress: bool,
    ) -> tuple[list[str], dict[str, list[float]]]:
        universe = self.data_source.get_universe(as_of=end, max_stocks=max_stocks)
        if emit_progress:
            self._emit_progress("download", 0, max(1, len(universe)), "正在拉取股票历史行情")

        series: dict[str, list[tuple[str, float]]] = {}
        for idx, code in enumerate(universe, start=1):
            rows = self.data_source.get_daily_closes(code, start=start, end=end)
            if len(rows) >= min_required_rows:
                series[code] = rows
            if emit_progress and (idx == len(universe) or idx % max(1, len(universe) // 20) == 0):
                self._emit_progress("download", idx, len(universe), f"已拉取 {idx}/{len(universe)}")

        if not series:
            return [], {}

        common_dates = sorted({d for rows in series.values() for d, _ in rows})
        price_map: dict[str, list[float]] = {}
        for code, rows in series.items():
            arr: list[float] = [0.0] * len(common_dates)
            last = 0.0
            row_dict = {d: p for d, p in rows}
            for i, d in enumerate(common_dates):
                if d in row_dict:
                    last = row_dict[d]
                arr[i] = last
            price_map[code] = arr
        return common_dates, price_map

    def _run_with_cached_data(
        self,
        common_dates: list[str],
        price_map: dict[str, list[float]],
        params: StrategyParams,
        emit_progress: bool,
    ) -> BacktestMetrics:
        if not common_dates or not price_map:
            return BacktestMetrics(0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0)

        period_returns: list[float] = []
        start_idx = max(params.momentum_window, params.short_momentum_window, params.volatility_window) + 1
        i = start_idx
        rebalance_total = max(1, (len(common_dates) - start_idx) // max(params.hold_days, 1))
        rebalance_done = 0
        while i + params.hold_days < len(common_dates):
            scored: list[tuple[str, float]] = []
            for code, prices in price_map.items():
                if prices[i - 1] <= 0 or prices[i + params.hold_days - 1] <= 0:
                    continue
                score = self._score_stock(prices, params, i)
                if score is None:
                    continue
                scored.append((code, score))

            if not scored:
                i += params.hold_days
                continue

            scored.sort(key=lambda item: item[1], reverse=True)
            selected = scored[: params.top_k]

            selected_returns: list[float] = []
            for code, _ in selected:
                prices = price_map[code]
                start_price = prices[i - 1]
                end_price = prices[i + params.hold_days - 1]
                if start_price <= 0 or end_price <= 0:
                    continue
                selected_returns.append(end_price / start_price - 1.0)

            if selected_returns:
                period_returns.append(mean(selected_returns))

            rebalance_done += 1
            if emit_progress and (rebalance_done == rebalance_total or rebalance_done % max(1, rebalance_total // 15) == 0):
                self._emit_progress("rebalance", rebalance_done, rebalance_total, "单组参数回测中")

            i += params.hold_days

        return self._calc_metrics(period_returns=period_returns, hold_days=params.hold_days)

    def run_single_backtest(
        self,
        start: date,
        end: date,
        params: StrategyParams,
        max_stocks: int = 220,
        emit_progress: bool = True,
    ) -> BacktestMetrics:
        min_required_rows = max(80, params.momentum_window + params.hold_days + 5)
        common_dates, price_map = self._prepare_market_data(
            start=start,
            end=end,
            max_stocks=max_stocks,
            min_required_rows=min_required_rows,
            emit_progress=emit_progress,
        )
        return self._run_with_cached_data(common_dates, price_map, params, emit_progress=emit_progress)

    def iterate(
        self,
        start: date,
        end: date,
        max_stocks: int = 220,
    ) -> list[IterationResult]:
        param_grid = {
            "momentum_window": [15, 20, 30],
            "short_momentum_window": [5, 10],
            "volatility_window": [15, 20],
            "top_k": [5, 8, 10],
            "hold_days": [5, 10],
            "weight_momentum": [1.0, 1.3],
            "weight_short": [0.7, 1.0],
            "weight_volatility": [0.8, 1.1],
            "weight_pullback": [0.5, 0.8],
        }

        keys = list(param_grid.keys())
        values_product = itertools.product(*(param_grid[key] for key in keys))
        candidate_params: list[StrategyParams] = []
        for values in values_product:
            payload = dict(zip(keys, values))
            if payload["short_momentum_window"] >= payload["momentum_window"]:
                continue
            candidate_params.append(StrategyParams(**payload))
        results: list[IterationResult] = []

        if not candidate_params:
            return []

        max_need = max(max(item.momentum_window, item.short_momentum_window, item.volatility_window) + item.hold_days + 5 for item in candidate_params)
        common_dates, price_map = self._prepare_market_data(
            start=start,
            end=end,
            max_stocks=max_stocks,
            min_required_rows=max(80, max_need),
            emit_progress=True,
        )
        if not common_dates or not price_map:
            return []

        total_iterations = max(1, len(candidate_params))
        self._emit_progress("iterate", 0, total_iterations, "开始参数迭代")

        for idx, params in enumerate(candidate_params, start=1):
            metrics = self._run_with_cached_data(common_dates, price_map, params, emit_progress=False)
            results.append(IterationResult(params=params, metrics=metrics))
            self._emit_progress("iterate", idx, total_iterations, "参数搜索中")

        results.sort(
            key=lambda item: (item.metrics.sharpe, item.metrics.annualized_return, item.metrics.max_drawdown),
            reverse=True,
        )
        self._emit_progress("iterate", total_iterations, total_iterations, "参数迭代完成")
        return results


def save_iteration_report(
    output_dir: str,
    start: date,
    end: date,
    max_stocks: int,
    results: list[IterationResult],
) -> tuple[str, str]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = directory / f"a_share_backtest_{ts}.json"
    md_path = directory / f"a_share_backtest_{ts}.md"

    payload = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "max_stocks": max_stocks,
        "total_iterations": len(results),
        "top_results": [
            {
                "params": asdict(item.params),
                "metrics": asdict(item.metrics),
            }
            for item in results[:10]
        ],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines: list[str] = []
    lines.append(f"# A股回测迭代报告 ({ts})")
    lines.append("")
    lines.append(f"- 回测区间: {start.isoformat()} ~ {end.isoformat()}")
    lines.append(f"- 股票池规模: {max_stocks}")
    lines.append(f"- 总迭代次数: {len(results)}")
    lines.append("")

    for idx, item in enumerate(results[:5], start=1):
        p = item.params
        m = item.metrics
        lines.append(f"## Top {idx}")
        lines.append(
            f"- 参数: momentum={p.momentum_window}, short={p.short_momentum_window}, vol={p.volatility_window}, "
            f"top_k={p.top_k}, hold={p.hold_days}, "
            f"w=({p.weight_momentum},{p.weight_short},{p.weight_volatility},{p.weight_pullback})"
        )
        lines.append(
            f"- 指标: 累计收益={m.cumulative_return:.2%}, 年化={m.annualized_return:.2%}, "
            f"年化波动={m.annualized_volatility:.2%}, 夏普={m.sharpe:.3f}, 最大回撤={m.max_drawdown:.2%}, 胜率={m.win_rate:.2%}"
        )
        lines.append("")

    lines.append("## 核心选股逻辑（当前最佳）")
    if results:
        best = results[0].params
        lines.append(
            "- 基于中短期动量排序，叠加波动率与回撤惩罚；每个调仓周期等权买入Top-K股票。"
        )
        lines.append(
            f"- 当前最佳参数: momentum_window={best.momentum_window}, short_momentum_window={best.short_momentum_window}, "
            f"volatility_window={best.volatility_window}, top_k={best.top_k}, hold_days={best.hold_days}."
        )
    else:
        lines.append("- 未得到可用结果，请检查数据源和回测区间。")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return str(json_path), str(md_path)
