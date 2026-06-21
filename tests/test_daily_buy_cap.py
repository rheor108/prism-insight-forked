import datetime
import sqlite3

from tracking.db_schema import create_all_tables, add_account_mode_column_if_missing
from tracking.helpers import get_current_slots_count, count_today_buys, is_ticker_in_holdings

TODAY = datetime.datetime.now().strftime("%Y-%m-%d")
YESTERDAY = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")


def _conn(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "t.sqlite"))
    cur = conn.cursor()
    create_all_tables(cur, conn)
    add_account_mode_column_if_missing(cur, conn)
    return conn, cur


def _add_holding(cur, ticker, mode, buy_date):
    cur.execute(
        "INSERT INTO stock_holdings (ticker, company_name, buy_price, buy_date, account_mode) "
        "VALUES (?, 'X', 100, ?, ?)",
        (ticker, buy_date, mode),
    )


def test_slots_count_filtered_by_mode(tmp_path):
    conn, cur = _conn(tmp_path)
    _add_holding(cur, "AAA", "real", TODAY)
    _add_holding(cur, "BBB", "demo", TODAY)
    conn.commit()
    assert get_current_slots_count(cur, "real") == 1
    assert get_current_slots_count(cur, "demo") == 1
    assert get_current_slots_count(cur) == 2  # no filter = all
    conn.close()


def test_count_today_buys_holdings_and_history(tmp_path):
    conn, cur = _conn(tmp_path)
    _add_holding(cur, "AAA", "real", TODAY)
    _add_holding(cur, "BBB", "real", YESTERDAY)   # not today
    _add_holding(cur, "CCC", "demo", TODAY)       # wrong mode
    # a same-day buy that was already sold lives in trading_history
    cur.execute(
        "INSERT INTO trading_history "
        "(ticker, company_name, buy_price, buy_date, sell_price, sell_date, profit_rate, holding_days, account_mode) "
        "VALUES ('DDD','X',100,?,110,?,10,0,'real')",
        (TODAY, TODAY),
    )
    conn.commit()
    assert count_today_buys(cur, "real") == 2  # AAA (holding) + DDD (history)
    conn.close()


def test_is_ticker_in_holdings_mode_filter(tmp_path):
    conn, cur = _conn(tmp_path)
    _add_holding(cur, "AAA", "real", TODAY)
    conn.commit()
    assert is_ticker_in_holdings(cur, "AAA", "real") is True
    assert is_ticker_in_holdings(cur, "AAA", "demo") is False   # real row excluded by demo filter
    assert is_ticker_in_holdings(cur, "AAA") is True            # no filter = any mode
    conn.close()


def test_history_account_mode_counted_for_correct_mode(tmp_path):
    """Fix 1 guard: a trading_history row with account_mode='real' IS counted by
    count_today_buys(..., 'real') and is NOT counted for 'demo'.

    This validates that sell_stock correctly records account_mode so that a
    same-day buy-then-sell in real mode is not invisible to the daily cap.
    """
    conn, cur = _conn(tmp_path)
    # Simulate a real buy that was sold the same day (now lives in trading_history)
    cur.execute(
        "INSERT INTO trading_history "
        "(ticker, company_name, buy_price, buy_date, sell_price, sell_date, profit_rate, holding_days, account_mode) "
        "VALUES ('EEE','X',100,?,110,?,10,0,'real')",
        (TODAY, TODAY),
    )
    # Also add a demo row with same buy_date — must NOT count toward real cap
    cur.execute(
        "INSERT INTO trading_history "
        "(ticker, company_name, buy_price, buy_date, sell_price, sell_date, profit_rate, holding_days, account_mode) "
        "VALUES ('FFF','Y',100,?,110,?,10,0,'demo')",
        (TODAY, TODAY),
    )
    conn.commit()
    assert count_today_buys(cur, "real") == 1   # EEE only
    assert count_today_buys(cur, "demo") == 1   # FFF only
    conn.close()
