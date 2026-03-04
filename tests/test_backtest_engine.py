from __future__ import annotations

from unittest import TestCase

from app.backtest_engine import AShareBacktestEngine, StrategyParams


class BacktestEngineTests(TestCase):
    def test_max_drawdown(self) -> None:
        nav = [1.0, 1.1, 1.05, 0.9, 0.95]
        dd = AShareBacktestEngine._max_drawdown(nav)
        self.assertLess(dd, 0)
        self.assertAlmostEqual(dd, -0.18181818, places=5)

    def test_calc_metrics(self) -> None:
        metrics = AShareBacktestEngine._calc_metrics([0.02, -0.01, 0.03, 0.0], hold_days=5)
        self.assertGreater(metrics.cumulative_return, 0)
        self.assertGreater(metrics.rebalance_count, 0)

    def test_score_stock_has_value(self) -> None:
        params = StrategyParams(
            momentum_window=20,
            short_momentum_window=5,
            volatility_window=15,
            top_k=5,
            hold_days=5,
            weight_momentum=1.2,
            weight_short=0.8,
            weight_volatility=1.0,
            weight_pullback=0.6,
        )
        history = [10 + i * 0.1 for i in range(60)]
        score = AShareBacktestEngine._score_stock(history, params, idx=30)
        self.assertIsNotNone(score)
