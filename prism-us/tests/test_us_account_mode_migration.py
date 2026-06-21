import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracking.db_schema import create_us_tables, add_account_mode_column_to_us_tables


def _columns(cursor, table):
    cursor.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cursor.fetchall()]


def test_account_mode_added_to_us_tables(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "t.sqlite"))
    cur = conn.cursor()
    create_us_tables(cur, conn)

    add_account_mode_column_to_us_tables(cur, conn)

    for table in ["us_stock_holdings", "us_trading_history"]:
        assert "account_mode" in _columns(cur, table)
    conn.close()


def test_us_migration_idempotent(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "t.sqlite"))
    cur = conn.cursor()
    create_us_tables(cur, conn)

    add_account_mode_column_to_us_tables(cur, conn)
    add_account_mode_column_to_us_tables(cur, conn)  # must not raise

    assert "account_mode" in _columns(cur, "us_stock_holdings")
    conn.close()
