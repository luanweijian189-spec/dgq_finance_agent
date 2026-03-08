from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import Base, SessionLocal, engine
from app.main import build_service_for_scheduler, get_intraday_provider


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="批量同步 A 股分时数据并落库")
    parser.add_argument("--stocks", nargs="*", default=[], help="指定股票代码，默认自动选取当前跟踪池")
    parser.add_argument("--period", default="1", help="分钟周期，默认 1")
    parser.add_argument("--adjust", default="", help="复权方式")
    parser.add_argument("--limit", type=int, default=10, help="默认自动选择的股票数")
    parser.add_argument("--include-ticks", action="store_true", help="同时同步逐笔成交")
    parser.add_argument("--start", default="", help="开始时间，ISO 格式")
    parser.add_argument("--end", default="", help="结束时间，ISO 格式")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        service = build_service_for_scheduler(session)
        provider = get_intraday_provider()
        start_value = datetime.fromisoformat(args.start) if args.start else None
        end_value = datetime.fromisoformat(args.end) if args.end else None
        targets = args.stocks or [item["stock_code"] for item in service.list_intraday_sync_candidates(limit=args.limit)]
        print(f"target_count={len(targets)}")
        for stock_code in targets:
            try:
                bars, used_cache = provider.get_minute_bars(
                    stock_code=stock_code,
                    period=args.period,
                    adjust=args.adjust,
                    start_datetime=start_value,
                    end_datetime=end_value,
                )
                bar_result = service.save_intraday_bars(
                    stock_code=stock_code,
                    bars=bars,
                    period=args.period,
                    adjust=args.adjust,
                    source="akshare",
                    used_cache=used_cache,
                )
                tick_saved = 0
                if args.include_ticks:
                    try:
                        trades, tick_used_cache = provider.get_trade_ticks(stock_code=stock_code)
                        tick_saved = service.save_intraday_ticks(
                            stock_code=stock_code,
                            trades=trades,
                            source="akshare",
                            used_cache=tick_used_cache,
                        )["saved"]
                    except Exception as exc:  # pragma: no cover
                        print(f"tick_warn stock={stock_code} error={exc}")
                print(
                    f"synced stock={stock_code} saved_bars={bar_result['saved']} saved_ticks={tick_saved} latest={bar_result['latest_timestamp']}"
                )
            except Exception as exc:  # pragma: no cover
                print(f"failed stock={stock_code} error={exc}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
