from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import get_settings
from app.database import Base

TABLE_ORDER = [
    "recommenders",
    "stocks",
    "recommendations",
    "daily_performance",
    "alert_subscriptions",
    "news_discovery_candidates",
    "stock_predictions",
    "stock_daily_maintenance",
    "intraday_bars",
    "intraday_ticks",
]

BOOLEAN_COLUMNS = {
    "daily_performance": {"logic_validated"},
    "alert_subscriptions": {"is_active"},
    "intraday_bars": {"used_cache"},
    "intraday_ticks": {"used_cache"},
}


def sqlite_rows(conn: sqlite3.Connection, table: str) -> tuple[list[str], list[dict]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    if not rows:
        cur = conn.execute(f"PRAGMA table_info({table})")
        columns = [r[1] for r in cur.fetchall()]
        return columns, []
    columns = list(rows[0].keys())
    normalized_rows: list[dict] = []
    bool_columns = BOOLEAN_COLUMNS.get(table, set())
    for row in rows:
        item = dict(row)
        for column in bool_columns:
            if column in item and item[column] is not None:
                item[column] = bool(item[column])
        normalized_rows.append(item)
    return columns, normalized_rows


def reset_pg_sequence(conn, table: str) -> None:
    try:
        seq = conn.execute(text("SELECT pg_get_serial_sequence(:table, 'id')"), {"table": table}).scalar_one_or_none()
        if seq:
            conn.execute(
                text(
                    "SELECT setval(:seq, COALESCE((SELECT MAX(id) FROM "
                    + table
                    + "), 1), true)"
                ),
                {"seq": seq},
            )
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Import SQLite snapshot into current PostgreSQL database")
    parser.add_argument("--source", default="demo.db", help="Path to SQLite snapshot, default: demo.db")
    parser.add_argument("--replace", action="store_true", help="Replace target business tables before import")
    args = parser.parse_args()

    source_path = Path(args.source)
    if not source_path.exists():
        raise SystemExit(f"source sqlite not found: {source_path}")

    settings = get_settings()
    pg_engine = create_engine(settings.database_url, future=True)
    Base.metadata.create_all(pg_engine)

    sqlite_conn = sqlite3.connect(str(source_path))
    sqlite_conn.row_factory = sqlite3.Row

    inspector = inspect(pg_engine)
    pg_tables = set(inspector.get_table_names())
    src_tables = {
        row[0]
        for row in sqlite_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    tables = [table for table in TABLE_ORDER if table in src_tables]

    with pg_engine.begin() as conn:
        if args.replace:
            for table in reversed([t for t in TABLE_ORDER if t in pg_tables or t in src_tables]):
                if table in inspect(conn).get_table_names():
                    conn.execute(text(f'TRUNCATE TABLE {table} RESTART IDENTITY CASCADE'))

        for table in tables:
            columns, rows = sqlite_rows(sqlite_conn, table)
            if table not in inspect(conn).get_table_names():
                continue
            if not rows:
                continue
            placeholders = ", ".join(f":{col}" for col in columns)
            column_list = ", ".join(columns)
            conn.execute(text(f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})"), rows)
            reset_pg_sequence(conn, table)
            print(f"imported {table}: {len(rows)}")

    sqlite_conn.close()
    print("done")


if __name__ == "__main__":
    main()
