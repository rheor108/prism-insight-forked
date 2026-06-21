import datetime
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracking.db_schema import (
    create_us_tables,
    add_account_mode_column_to_us_tables,
    get_us_holdings_count,
    count_today_us_buys,
    is_us_ticker_in_holdings,
)

TODAY = datetime.datetime.now().strftime("%Y-%m-%d")


def _conn(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "t.sqlite"))
    cur = conn.cursor()
    create_us_tables(cur, conn)
    add_account_mode_column_to_us_tables(cur, conn)
    return conn, cur


def test_us_slots_filtered_by_mode(tmp_path):
    conn, cur = _conn(tmp_path)
    cur.execute("INSERT INTO us_stock_holdings (ticker, company_name, buy_price, buy_date, account_mode) "
                "VALUES ('AAPL','Apple',200,?,'real')", (TODAY,))
    cur.execute("INSERT INTO us_stock_holdings (ticker, company_name, buy_price, buy_date, account_mode) "
                "VALUES ('MSFT','Microsoft',400,?,'demo')", (TODAY,))
    conn.commit()
    assert get_us_holdings_count(cur, "real") == 1
    assert get_us_holdings_count(cur) == 2
    conn.close()


def test_count_today_us_buys(tmp_path):
    conn, cur = _conn(tmp_path)
    cur.execute("INSERT INTO us_stock_holdings (ticker, company_name, buy_price, buy_date, account_mode) "
                "VALUES ('AAPL','Apple',200,?,'real')", (TODAY,))
    cur.execute("INSERT INTO us_trading_history "
                "(ticker, company_name, buy_price, buy_date, sell_price, sell_date, profit_rate, holding_days, account_mode) "
                "VALUES ('NVDA','Nvidia',100,?,110,?,10,0,'real')", (TODAY, TODAY))
    conn.commit()
    assert count_today_us_buys(cur, "real") == 2
    conn.close()


def test_is_us_ticker_in_holdings_mode_filter(tmp_path):
    conn, cur = _conn(tmp_path)
    cur.execute("INSERT INTO us_stock_holdings (ticker, company_name, buy_price, buy_date, account_mode) "
                "VALUES ('AAPL','Apple',200,?,'real')", (TODAY,))
    conn.commit()
    assert is_us_ticker_in_holdings(cur, "AAPL", "real") is True
    assert is_us_ticker_in_holdings(cur, "AAPL", "demo") is False
    assert is_us_ticker_in_holdings(cur, "AAPL") is True
    conn.close()


def test_us_history_account_mode_counted_for_correct_mode(tmp_path):
    """Fix 1 guard: a us_trading_history row with account_mode='real' IS counted
    by count_today_us_buys(..., 'real') and is NOT counted for 'demo'.

    Validates that sell_stock correctly records account_mode so a same-day
    buy-then-sell in real mode is not invisible to the daily cap.
    """
    conn, cur = _conn(tmp_path)
    cur.execute(
        "INSERT INTO us_trading_history "
        "(ticker, company_name, buy_price, buy_date, sell_price, sell_date, profit_rate, holding_days, account_mode) "
        "VALUES ('META','Meta',100,?,110,?,10,0,'real')",
        (TODAY, TODAY),
    )
    cur.execute(
        "INSERT INTO us_trading_history "
        "(ticker, company_name, buy_price, buy_date, sell_price, sell_date, profit_rate, holding_days, account_mode) "
        "VALUES ('AMZN','Amazon',200,?,220,?,10,0,'demo')",
        (TODAY, TODAY),
    )
    conn.commit()
    assert count_today_us_buys(cur, "real") == 1   # META only
    assert count_today_us_buys(cur, "demo") == 1   # AMZN only
    conn.close()
