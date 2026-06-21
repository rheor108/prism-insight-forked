import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading.us_stock_trading import slot_even_amount, USStockTrading


def test_even_split_usd():
    assert slot_even_amount(10_000, 10, 100) == 1_000
    assert slot_even_amount(9_000, 9, 100) == 1_000


def test_zero_slots_falls_back():
    assert slot_even_amount(10_000, 0, 100) == 100


def test_no_cash_falls_back():
    assert slot_even_amount(0, 5, 100) == 100
    assert slot_even_amount(None, 5, 100) == 100


def test_method_falls_back_when_summary_none():
    trader = MagicMock(spec=USStockTrading)
    trader.get_account_summary.return_value = None
    trader.buy_amount = 1000
    result = USStockTrading.calculate_slot_even_amount(trader, remaining_slots=5)
    assert result == 1000


def test_method_falls_back_when_summary_empty():
    trader = MagicMock(spec=USStockTrading)
    trader.get_account_summary.return_value = {}
    trader.buy_amount = 1000
    result = USStockTrading.calculate_slot_even_amount(trader, remaining_slots=5)
    assert result == 1000


def test_method_uses_safe_float_for_numeric_string():
    # Proves the method delegates to slot_even_amount and _safe_float parses
    # KIS numeric strings; result (2000) differs from the fallback (1000).
    trader = MagicMock(spec=USStockTrading)
    trader.get_account_summary.return_value = {"available_amount": "10000.00"}
    trader.buy_amount = 1000
    result = USStockTrading.calculate_slot_even_amount(trader, remaining_slots=5)
    assert result == 2000


def test_method_falls_back_when_available_amount_is_empty_string():
    # KIS occasionally returns '' for balances; _safe_float('') -> 0.0 -> fallback.
    trader = MagicMock(spec=USStockTrading)
    trader.get_account_summary.return_value = {"available_amount": ""}
    trader.buy_amount = 1000
    result = USStockTrading.calculate_slot_even_amount(trader, remaining_slots=5)
    assert result == 1000
