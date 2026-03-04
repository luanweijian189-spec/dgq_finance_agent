from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.backtest_engine import AShareBacktestEngine, save_iteration_report


class ConsoleProgressBar:
    def __init__(self, width: int = 32) -> None:
        self.width = max(10, width)
        self._last_stage = ""
        self._start_ts = time.time()

    def update(self, stage: str, current: int, total: int, message: str = "") -> None:
        if stage != self._last_stage:
            if self._last_stage:
                print()
            self._last_stage = stage

        safe_total = max(1, total)
        safe_current = max(0, min(current, safe_total))
        pct = safe_current / safe_total
        filled = int(self.width * pct)
        bar = "#" * filled + "-" * (self.width - filled)
        elapsed = int(time.time() - self._start_ts)
        text = f"\r[{stage:9}] [{bar}] {safe_current:>4}/{safe_total:<4} {pct*100:6.2f}%  {message}  elapsed:{elapsed}s"
        print(text, end="", flush=True)

    def finish(self, note: str = "完成") -> None:
        if self._last_stage:
            print(f"\n{note}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run A-share real-data backtest and iterate core stock-picking logic")
    parser.add_argument("--start", type=str, default="2024-01-01", help="start date, e.g. 2024-01-01")
    parser.add_argument("--end", type=str, default=date.today().isoformat(), help="end date, e.g. 2026-03-03")
    parser.add_argument("--max-stocks", type=int, default=220, help="max number of stocks in universe")
    parser.add_argument("--output-dir", type=str, default="reports/backtest", help="directory for reports")
    parser.add_argument("--no-progress", action="store_true", help="disable console progress bar")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    progress = None if args.no_progress else ConsoleProgressBar()
    engine = AShareBacktestEngine(progress_callback=progress.update if progress else None)

    try:
        results = engine.iterate(start=start, end=end, max_stocks=args.max_stocks)
    finally:
        engine.data_source.logout()
        if progress:
            progress.finish("回测流程执行完成")

    json_path, md_path = save_iteration_report(
        output_dir=args.output_dir,
        start=start,
        end=end,
        max_stocks=args.max_stocks,
        results=results,
    )

    if not results:
        print("No valid iteration results generated")
        print(f"report_json={json_path}")
        print(f"report_md={md_path}")
        return

    best = results[0]
    print("A-share backtest iteration completed")
    print(f"best_sharpe={best.metrics.sharpe:.4f}")
    print(f"best_annualized_return={best.metrics.annualized_return:.2%}")
    print(f"best_max_drawdown={best.metrics.max_drawdown:.2%}")
    print(f"best_params={best.params}")
    print(f"report_json={json_path}")
    print(f"report_md={md_path}")


if __name__ == "__main__":
    main()
