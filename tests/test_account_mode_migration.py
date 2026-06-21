import sqlite3

from tracking.db_schema import create_all_tables, add_account_mode_column_if_missing


def _columns(cursor, table):
    cursor.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cursor.fetchall()]


def test_account_mode_added_to_kr_tables(tmp_path):
    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(str(db))
    cur = conn.cursor()
    create_all_tables(cur, conn)

    add_account_mode_column_if_missing(cur, conn)

    for table in ["stock_holdings", "trading_history", "trading_journal"]:
        assert "account_mode" in _columns(cur, table)
    conn.close()


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(str(db))
    cur = conn.cursor()
    create_all_tables(cur, conn)

    add_account_mode_column_if_missing(cur, conn)
    add_account_mode_column_if_missing(cur, conn)  # must not raise

    assert "account_mode" in _columns(cur, "stock_holdings")
    conn.close()


def test_existing_rows_default_to_demo(tmp_path):
    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(str(db))
    cur = conn.cursor()
    create_all_tables(cur, conn)
    cur.execute(
        "INSERT INTO stock_holdings (ticker, company_name, buy_price, buy_date) "
        "VALUES ('005930','Samsung',70000,'2026-06-21')"
    )
    conn.commit()

    add_account_mode_column_if_missing(cur, conn)

    cur.execute("SELECT account_mode FROM stock_holdings WHERE ticker='005930'")
    assert cur.fetchone()[0] == "demo"
    conn.close()
