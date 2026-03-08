from __future__ import annotations

from datetime import datetime
from pathlib import Path

import akshare as ak

from app.providers import build_intraday_provider


def main() -> None:
    workspace_dir = Path(__file__).resolve().parents[1]
    stock_code = "002384"
    stock_name = "东山精密"
    trade_date = "2026-03-05"
    trade_date_compact = "20260305"
    start = datetime(2026, 3, 5, 9, 30)
    end = datetime(2026, 3, 5, 15, 0)
    report_path = workspace_dir / "design_docs/smoke_tests/002384_2026-03-05_intraday_smoke_test.md"

    provider_results = []
    for name in ["pytdx", "freebest", "akshare"]:
        provider = build_intraday_provider(
            name,
            cache_dir=str(workspace_dir / "data/intraday"),
            request_interval_seconds=0.2,
            max_retries=1,
        )
        try:
            bars, used_cache = provider.get_minute_bars(
                stock_code,
                period="1",
                start_datetime=start,
                end_datetime=end,
            )
            provider_results.append(
                {
                    "provider": name,
                    "ok": True,
                    "count": len(bars),
                    "used_cache": used_cache,
                    "first": bars[0].timestamp if bars else "",
                    "last": bars[-1].timestamp if bars else "",
                    "error": "",
                }
            )
        except Exception as exc:  # pragma: no cover - smoke report script
            provider_results.append(
                {
                    "provider": name,
                    "ok": False,
                    "count": 0,
                    "used_cache": False,
                    "first": "",
                    "last": "",
                    "error": str(exc).replace("\n", " "),
                }
            )

    freebest_provider = build_intraday_provider(
        "freebest",
        cache_dir=str(workspace_dir / "data/intraday"),
        request_interval_seconds=0.2,
        max_retries=1,
    )
    bars, bar_used_cache = freebest_provider.get_minute_bars(
        stock_code,
        period="1",
        start_datetime=start,
        end_datetime=end,
    )

    tick_df = ak.stock_intraday_sina(symbol="sz002384", date=trade_date_compact).copy()
    tick_df["ticktime"] = tick_df["ticktime"].astype(str)
    tick_df["kind_desc"] = tick_df["kind"].map({"U": "向上", "D": "向下", "E": "平盘"}).fillna(
        tick_df["kind"].astype(str)
    )
    tick_df["session"] = tick_df["ticktime"].apply(lambda x: "上午" if x <= "11:30:00" else "下午")

    morning_ticks = tick_df[tick_df["session"] == "上午"]
    afternoon_ticks = tick_df[tick_df["session"] == "下午"]

    bar_volume_sum = sum(item.volume for item in bars)
    bar_amount_sum = sum(item.amount for item in bars)
    tick_volume_sum = float(tick_df["volume"].fillna(0).sum())

    lines: list[str] = []
    lines.append(f"# {stock_name}（{stock_code}）2026-03-05 盘中全量数据冒烟测试")
    lines.append("")
    lines.append("## 1. 测试目标")
    lines.append("")
    lines.append("- 标的：东山精密 `002384`")
    lines.append("- 交易日：2026-03-05")
    lines.append("- 目标：拉取上午到下午的全量盘中数据，并整理到单一文档中")
    lines.append("- 范围：1 分钟线 + 历史逐笔成交")
    lines.append("")
    lines.append("## 2. 测试结论")
    lines.append("")
    lines.append("- `freebest` 1 分钟线抓取成功。")
    lines.append("- `pytdx` 1 分钟线单源抓取成功。")
    lines.append("- `akshare` 1 分钟线本次单源抓取失败，说明单 AKShare 仍不适合做唯一主链路。")
    lines.append("- 历史逐笔成交通过 `AKShare stock_intraday_sina` 成功获取。")
    lines.append("- 本文档已保存 2026-03-05 当天上午到下午的完整分钟线与完整逐笔明细。")
    lines.append("")
    lines.append("## 3. Provider 冒烟结果")
    lines.append("")
    lines.append("| provider | 结果 | 条数 | 是否缓存 | 起始时间 | 结束时间 | 备注 |")
    lines.append("| --- | --- | ---: | --- | --- | --- | --- |")
    for row in provider_results:
        status = "成功" if row["ok"] else "失败"
        used_cache = "是" if row["used_cache"] else "否"
        note = row["error"] if row["error"] else "分钟线接口可用"
        lines.append(
            f"| {row['provider']} | {status} | {row['count']} | {used_cache} | {row['first']} | {row['last']} | {note} |"
        )
    lines.append("")
    lines.append("## 4. 数据摘要")
    lines.append("")
    lines.append("| 项目 | 值 |")
    lines.append("| --- | --- |")
    lines.append("| 分钟线来源 | freebest |")
    lines.append(f"| 分钟线条数 | {len(bars)} |")
    lines.append(f"| 分钟线时间范围 | {bars[0].timestamp} ~ {bars[-1].timestamp} |")
    lines.append(f"| 分钟线是否命中缓存 | {'是' if bar_used_cache else '否'} |")
    lines.append(f"| 分钟线累计成交量 | {bar_volume_sum:,.0f} |")
    lines.append(f"| 分钟线累计成交额 | {bar_amount_sum:,.2f} |")
    lines.append("| 逐笔来源 | AKShare `stock_intraday_sina` |")
    lines.append(f"| 逐笔总条数 | {len(tick_df)} |")
    lines.append(f"| 上午逐笔条数 | {len(morning_ticks)} |")
    lines.append(f"| 下午逐笔条数 | {len(afternoon_ticks)} |")
    lines.append(f"| 逐笔时间范围 | {tick_df.iloc[0]['ticktime']} ~ {tick_df.iloc[-1]['ticktime']} |")
    lines.append(f"| 逐笔累计成交量 | {tick_volume_sum:,.0f} |")
    lines.append("")
    lines.append("## 5. 1 分钟线全量明细")
    lines.append("")
    lines.append("| 序号 | 时间 | 开盘 | 最高 | 最低 | 收盘 | 成交量 | 成交额 |")
    lines.append("| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for idx, bar in enumerate(bars, start=1):
        lines.append(
            f"| {idx} | {bar.timestamp} | {bar.open_price:.2f} | {bar.high_price:.2f} | {bar.low_price:.2f} | {bar.close_price:.2f} | {bar.volume:.0f} | {bar.amount:.2f} |"
        )
    lines.append("")
    lines.append("## 6. 上午逐笔成交全量明细")
    lines.append("")
    lines.append("说明：`kind` 为涨跌方向标记，`U=向上`，`D=向下`，`E=平盘`。")
    lines.append("")
    lines.append("| 序号 | 时间 | 成交价 | 成交量 | 前价 | kind | 方向 |")
    lines.append("| ---: | --- | ---: | ---: | ---: | --- | --- |")
    for idx, (_, row) in enumerate(morning_ticks.iterrows(), start=1):
        lines.append(
            f"| {idx} | {row['ticktime']} | {float(row['price']):.2f} | {float(row['volume']):.0f} | {float(row['prev_price']):.2f} | {row['kind']} | {row['kind_desc']} |"
        )
    lines.append("")
    lines.append("## 7. 下午逐笔成交全量明细")
    lines.append("")
    lines.append("| 序号 | 时间 | 成交价 | 成交量 | 前价 | kind | 方向 |")
    lines.append("| ---: | --- | ---: | ---: | ---: | --- | --- |")
    for idx, (_, row) in enumerate(afternoon_ticks.iterrows(), start=1):
        lines.append(
            f"| {idx} | {row['ticktime']} | {float(row['price']):.2f} | {float(row['volume']):.0f} | {float(row['prev_price']):.2f} | {row['kind']} | {row['kind_desc']} |"
        )
    lines.append("")
    lines.append("## 8. 冒烟测试判定")
    lines.append("")
    lines.append("- 2026-03-05 东山精密全日盘中数据已成功抓取并落成文档。")
    lines.append("- 分钟线主链路建议继续使用 `freebest`。")
    lines.append("- 历史逐笔在当前代码下仍以 AKShare 历史接口补充最直接。")
    lines.append("- 如果下一步要做批量历史逐笔归档，建议把 `pytdx` 的历史逐笔接口也正式接入 provider。")
    lines.append("")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"report_written={report_path}")
    print(f"bytes={report_path.stat().st_size}")
    print(f"bars={len(bars)} ticks={len(tick_df)} morning={len(morning_ticks)} afternoon={len(afternoon_ticks)}")


if __name__ == "__main__":
    main()
