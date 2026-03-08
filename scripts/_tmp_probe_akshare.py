import json
from pathlib import Path

import akshare as ak

symbol = '002384'
results = {}


def pack(df):
    return {
        'ok': df is not None and not getattr(df, 'empty', True),
        'columns': list(df.columns) if df is not None else [],
        'rows': 0 if df is None else len(df),
        'head': None if df is None or getattr(df, 'empty', True) else df.head(3).to_dict(orient='records'),
        'tail': None if df is None or getattr(df, 'empty', True) else df.tail(3).to_dict(orient='records'),
    }

checks = [
    ('daily', lambda: ak.stock_zh_a_hist(symbol=symbol, period='daily', start_date='20260305', end_date='20260305', adjust='')),
    ('min1', lambda: ak.stock_zh_a_hist_min_em(symbol=symbol, period='1', start_date='2026-03-05 09:30:00', end_date='2026-03-05 15:00:00', adjust='')),
    ('stock_individual_fund_flow', lambda: ak.stock_individual_fund_flow(stock=symbol, market='sz')),
    ('stock_individual_fund_flow_rank', lambda: ak.stock_individual_fund_flow_rank(indicator='今日')),
    ('stock_board_industry_name_em', lambda: ak.stock_board_industry_name_em()),
    ('stock_board_industry_hist_em', lambda: ak.stock_board_industry_hist_em(symbol='消费电子', start_date='20260305', end_date='20260305', period='日k', adjust='')),
    ('stock_board_industry_cons_em', lambda: ak.stock_board_industry_cons_em(symbol='消费电子')),
    ('stock_sector_fund_flow_rank', lambda: ak.stock_sector_fund_flow_rank(indicator='今日')),
    ('stock_bid_ask_em', lambda: ak.stock_bid_ask_em(symbol=symbol)),
]

for name, fn in checks:
    try:
        results[name] = pack(fn())
    except Exception as e:
        results[name] = {'ok': False, 'error': repr(e)}

path = Path('scripts/_tmp_probe_akshare_output.json')
path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
print(path)
