from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from .models import DailyPerformance, Recommendation, Recommender, Stock


class InMemoryRepository:
    def __init__(self) -> None:
        self._stocks: dict[int, Stock] = {}
        self._stocks_by_code: dict[str, int] = {}

        self._recommenders: dict[int, Recommender] = {}
        self._recommenders_by_name: dict[str, int] = {}

        self._recommendations: dict[int, Recommendation] = {}
        self._recommendations_by_stock: dict[int, list[int]] = defaultdict(list)
        self._recommendations_by_recommender: dict[int, list[int]] = defaultdict(list)

        self._daily_performance: dict[int, DailyPerformance] = {}
        self._daily_by_recommendation: dict[int, list[int]] = defaultdict(list)

        self._stock_id_seq = 1
        self._recommender_id_seq = 1
        self._recommendation_id_seq = 1
        self._daily_id_seq = 1

    def upsert_stock(self, stock_code: str, stock_name: str = "", industry: str = "") -> Stock:
        stock_code = stock_code.strip()
        existing_id = self._stocks_by_code.get(stock_code)
        if existing_id is not None:
            stock = self._stocks[existing_id]
            if stock_name and not stock.stock_name:
                stock.stock_name = stock_name
            if industry and not stock.industry:
                stock.industry = industry
            return stock

        stock = Stock(
            id=self._stock_id_seq,
            stock_code=stock_code,
            stock_name=stock_name,
            industry=industry,
        )
        self._stocks[self._stock_id_seq] = stock
        self._stocks_by_code[stock_code] = self._stock_id_seq
        self._stock_id_seq += 1
        return stock

    def upsert_recommender(self, name: str, wechat_id: str = "") -> Recommender:
        key = name.strip().lower()
        existing_id = self._recommenders_by_name.get(key)
        if existing_id is not None:
            recommender = self._recommenders[existing_id]
            if wechat_id and not recommender.wechat_id:
                recommender.wechat_id = wechat_id
            return recommender

        recommender = Recommender(id=self._recommender_id_seq, name=name, wechat_id=wechat_id)
        self._recommenders[self._recommender_id_seq] = recommender
        self._recommenders_by_name[key] = self._recommender_id_seq
        self._recommender_id_seq += 1
        return recommender

    def add_recommendation(
        self,
        stock_id: int,
        recommender_id: int,
        original_message: str,
        extracted_logic: str,
        initial_price: float | None = None,
        recommend_ts: datetime | None = None,
    ) -> Recommendation:
        recommendation = Recommendation(
            id=self._recommendation_id_seq,
            stock_id=stock_id,
            recommender_id=recommender_id,
            recommend_ts=recommend_ts or datetime.now(),
            initial_price=initial_price,
            original_message=original_message,
            extracted_logic=extracted_logic,
        )
        self._recommendations[self._recommendation_id_seq] = recommendation
        self._recommendations_by_stock[stock_id].append(self._recommendation_id_seq)
        self._recommendations_by_recommender[recommender_id].append(self._recommendation_id_seq)
        self._recommendation_id_seq += 1
        return recommendation

    def add_daily_performance(
        self,
        recommendation_id: int,
        close_price: float,
        high_price: float,
        low_price: float,
        pnl_percent: float,
        max_drawdown: float,
        evaluation_score: float,
        date,
        sharpe_ratio: float = 0.0,
        notes: str = "",
        extra: dict[str, float | str | bool] | None = None,
    ) -> DailyPerformance:
        daily = DailyPerformance(
            id=self._daily_id_seq,
            recommendation_id=recommendation_id,
            date=date,
            close_price=close_price,
            high_price=high_price,
            low_price=low_price,
            pnl_percent=pnl_percent,
            max_drawdown=max_drawdown,
            evaluation_score=evaluation_score,
            sharpe_ratio=sharpe_ratio,
            notes=notes,
            extra=extra or {},
        )
        self._daily_performance[self._daily_id_seq] = daily
        self._daily_by_recommendation[recommendation_id].append(self._daily_id_seq)
        self._daily_id_seq += 1
        return daily

    def get_stock_by_code(self, stock_code: str) -> Stock | None:
        stock_id = self._stocks_by_code.get(stock_code)
        return self._stocks.get(stock_id) if stock_id else None

    def get_recommender_by_name(self, name: str) -> Recommender | None:
        recommender_id = self._recommenders_by_name.get(name.strip().lower())
        return self._recommenders.get(recommender_id) if recommender_id else None

    def get_recommendations_for_stock(self, stock_id: int) -> list[Recommendation]:
        ids = self._recommendations_by_stock.get(stock_id, [])
        return [self._recommendations[item] for item in ids]

    def get_recommendations_for_recommender(self, recommender_id: int) -> list[Recommendation]:
        ids = self._recommendations_by_recommender.get(recommender_id, [])
        return [self._recommendations[item] for item in ids]

    def get_daily_for_recommendation(self, recommendation_id: int) -> list[DailyPerformance]:
        ids = self._daily_by_recommendation.get(recommendation_id, [])
        return [self._daily_performance[item] for item in ids]

    def latest_daily_for_recommendation(self, recommendation_id: int) -> DailyPerformance | None:
        daily_list = self.get_daily_for_recommendation(recommendation_id)
        if not daily_list:
            return None
        return max(daily_list, key=lambda item: item.date)

    def latest_daily_for_stock(self, stock_code: str) -> DailyPerformance | None:
        stock = self.get_stock_by_code(stock_code)
        if not stock:
            return None
        recs = self.get_recommendations_for_stock(stock.id)
        all_daily: list[DailyPerformance] = []
        for rec in recs:
            all_daily.extend(self.get_daily_for_recommendation(rec.id))
        if not all_daily:
            return None
        return max(all_daily, key=lambda item: item.date)

    def get_top_stocks(self, n: int, reverse: bool = True) -> list[tuple[Stock, DailyPerformance]]:
        pairs: list[tuple[Stock, DailyPerformance]] = []
        for stock in self._stocks.values():
            latest = self.latest_daily_for_stock(stock.stock_code)
            if latest is not None:
                pairs.append((stock, latest))
        pairs.sort(key=lambda item: item[1].evaluation_score, reverse=reverse)
        return pairs[:n]

    def iter_recommenders(self) -> list[Recommender]:
        return list(self._recommenders.values())

    def iter_recommendations(self) -> list[Recommendation]:
        return list(self._recommendations.values())

    def update_recommender_score(self, recommender_id: int, reliability_score: float) -> None:
        recommender = self._recommenders[recommender_id]
        recommender.reliability_score = reliability_score
