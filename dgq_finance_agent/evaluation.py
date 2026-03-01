from __future__ import annotations

import math
from dataclasses import dataclass


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, value))


@dataclass
class DailyMarketMetrics:
    pnl_percent: float
    peak_pnl_percent: float
    sharpe_ratio: float
    max_drawdown: float
    logic_validated: bool
    market_cap_score: float
    elasticity_score: float
    liquidity_score: float


@dataclass
class RecommendationOutcome:
    return_percent: float
    max_drawdown: float
    days_ago: int


def compute_stock_quality_score(metrics: DailyMarketMetrics) -> tuple[float, str]:
    profitability_score = _clamp(
        50.0
        + 1.8 * metrics.pnl_percent
        + 0.8 * metrics.peak_pnl_percent
        + 8.0 * metrics.sharpe_ratio
        - 0.6 * abs(min(metrics.max_drawdown, 0.0))
    )
    logic_score = 100.0 if metrics.logic_validated else 45.0
    quantitative_score = _clamp(
        (metrics.market_cap_score + metrics.elasticity_score + metrics.liquidity_score) / 3.0
    )

    final_score = _clamp(
        profitability_score * 0.6 + logic_score * 0.2 + quantitative_score * 0.2
    )

    action = "继续跟踪"
    if final_score >= 80:
        action = "重点持有"
    elif final_score < 50:
        action = "谨慎观察"

    analysis = (
        f"SQS={final_score:.1f}，收益指标{profitability_score:.1f}，"
        f"逻辑{'已验证' if metrics.logic_validated else '待验证'}，"
        f"量化指标{quantitative_score:.1f}，建议：{action}。"
    )
    return final_score, analysis


def compute_recommender_reliability(outcomes: list[RecommendationOutcome]) -> tuple[float, str]:
    if not outcomes:
        return 50.0, "样本不足，维持默认可靠性评分。"

    half_life_days = 90.0
    weighted_sum = 0.0
    total_weight = 0.0
    weighted_hits = 0.0
    weighted_drawdown = 0.0

    for outcome in outcomes:
        weight = math.exp(-math.log(2) * max(outcome.days_ago, 0) / half_life_days)
        total_weight += weight
        weighted_sum += outcome.return_percent * weight
        weighted_drawdown += abs(min(outcome.max_drawdown, 0.0)) * weight
        if outcome.return_percent >= 10.0:
            weighted_hits += weight

    avg_return = weighted_sum / total_weight
    hit_rate = weighted_hits / total_weight
    avg_drawdown = weighted_drawdown / total_weight

    reliability_score = _clamp(50.0 + 1.4 * avg_return + 28.0 * hit_rate - 1.1 * avg_drawdown)

    if reliability_score >= 80:
        tag = "高胜率短线选手"
    elif reliability_score >= 60:
        tag = "稳定输出型"
    elif reliability_score >= 40:
        tag = "观察名单"
    else:
        tag = "高风险信号源"

    analysis = (
        f"RRS={reliability_score:.1f}，平均回报{avg_return:.1f}% ，"
        f"胜率{hit_rate * 100:.1f}% ，平均回撤{avg_drawdown:.1f}% ，标签：{tag}。"
    )
    return reliability_score, analysis
