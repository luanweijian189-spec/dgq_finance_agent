from __future__ import annotations

import statistics
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import akshare as ak


FUNCTIONS = {
    "daily": lambda: ak.stock_zh_a_hist(
        symbol="002384",
        period="daily",
        start_date="20260305",
        end_date="20260306",
        adjust="",
    ),
    "min1": lambda: ak.stock_zh_a_hist_min_em(
        symbol="002384",
        start_date="2026-03-05 09:30:00",
        end_date="2026-03-05 15:00:00",
        period="1",
        adjust="",
    ),
    "fund_flow": lambda: ak.stock_individual_fund_flow(stock="002384", market="sz"),
    "tick_history": lambda: ak.stock_intraday_sina(symbol="sz002384", date="20260305"),
    "industry_flow": lambda: ak.stock_fund_flow_industry(symbol="即时"),
}

STAGES = {
    "daily": [
        {"name": "seq_soft", "mode": "seq", "rounds": 5, "sleep": 1.0},
        {"name": "seq_mid", "mode": "seq", "rounds": 10, "sleep": 0.2},
        {"name": "seq_burst", "mode": "seq", "rounds": 20, "sleep": 0.0},
        {"name": "parallel", "mode": "parallel", "rounds": 12, "workers": 3},
    ],
    "min1": [
        {"name": "seq_soft", "mode": "seq", "rounds": 5, "sleep": 1.0},
        {"name": "seq_mid", "mode": "seq", "rounds": 10, "sleep": 0.2},
        {"name": "seq_burst", "mode": "seq", "rounds": 20, "sleep": 0.0},
        {"name": "parallel", "mode": "parallel", "rounds": 12, "workers": 3},
    ],
    "fund_flow": [
        {"name": "seq_soft", "mode": "seq", "rounds": 5, "sleep": 0.5},
        {"name": "seq_mid", "mode": "seq", "rounds": 15, "sleep": 0.1},
        {"name": "seq_burst", "mode": "seq", "rounds": 30, "sleep": 0.0},
        {"name": "parallel", "mode": "parallel", "rounds": 15, "workers": 3},
    ],
    "tick_history": [
        {"name": "seq_soft", "mode": "seq", "rounds": 3, "sleep": 1.0},
        {"name": "seq_mid", "mode": "seq", "rounds": 5, "sleep": 0.2},
        {"name": "seq_burst", "mode": "seq", "rounds": 8, "sleep": 0.0},
    ],
    "industry_flow": [
        {"name": "seq_soft", "mode": "seq", "rounds": 5, "sleep": 0.5},
        {"name": "seq_mid", "mode": "seq", "rounds": 10, "sleep": 0.1},
        {"name": "seq_burst", "mode": "seq", "rounds": 20, "sleep": 0.0},
        {"name": "parallel", "mode": "parallel", "rounds": 12, "workers": 3},
    ],
}


def call_once(name: str):
    t0 = time.perf_counter()
    try:
        df = FUNCTIONS[name]()
        elapsed = time.perf_counter() - t0
        rows = len(df) if df is not None else None
        return {"ok": True, "elapsed": elapsed, "rows": rows, "error": ""}
    except Exception as exc:  # pragma: no cover
        elapsed = time.perf_counter() - t0
        return {
            "ok": False,
            "elapsed": elapsed,
            "rows": None,
            "error": f"{type(exc).__name__}: {str(exc).strip()}"[:220],
        }


def run_seq(name: str, rounds: int, sleep: float):
    results = []
    for index in range(rounds):
        result = call_once(name)
        result["index"] = index + 1
        results.append(result)
        if sleep > 0 and index < rounds - 1:
            time.sleep(sleep)
    return results


def run_parallel(name: str, rounds: int, workers: int):
    results = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(call_once, name) for _ in range(rounds)]
        for index, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            result["index"] = index
            results.append(result)
    return results


def summarize(results):
    success = sum(1 for item in results if item["ok"])
    failure = len(results) - success
    elapsed = [item["elapsed"] for item in results]
    error_counter = Counter(item["error"] for item in results if item["error"])
    return {
        "total": len(results),
        "success": success,
        "failure": failure,
        "success_rate": round(success / len(results), 3) if results else 0,
        "min_s": round(min(elapsed), 3) if elapsed else None,
        "avg_s": round(statistics.mean(elapsed), 3) if elapsed else None,
        "p95_s": round(sorted(elapsed)[max(int(len(elapsed) * 0.95) - 1, 0)], 3) if elapsed else None,
        "max_s": round(max(elapsed), 3) if elapsed else None,
        "rows_seen": sorted({item["rows"] for item in results if item["rows"] is not None})[:5],
        "top_errors": error_counter.most_common(3),
        "first_fail_index": next((item["index"] for item in results if not item["ok"]), None),
    }


if __name__ == "__main__":
    for func_name, stages in STAGES.items():
        print(f"\n=== {func_name} ===")
        for stage in stages:
            if stage["mode"] == "seq":
                results = run_seq(func_name, stage["rounds"], stage["sleep"])
            else:
                results = run_parallel(func_name, stage["rounds"], stage["workers"])
            print(stage["name"], summarize(results))
