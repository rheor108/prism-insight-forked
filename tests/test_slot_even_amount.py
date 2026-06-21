from trading.domestic_stock_trading import slot_even_amount


def test_even_split_no_decay():
    # 10,000,000 / 10 slots = 1,000,000; after a buy 9,000,000 / 9 = 1,000,000
    assert slot_even_amount(10_000_000, 10, 10_000) == 1_000_000
    assert slot_even_amount(9_000_000, 9, 10_000) == 1_000_000


def test_floor_division():
    assert slot_even_amount(1_000, 3, 10_000) == 333


def test_zero_slots_falls_back_to_default():
    assert slot_even_amount(10_000_000, 0, 10_000) == 10_000


def test_negative_slots_falls_back():
    assert slot_even_amount(10_000_000, -1, 10_000) == 10_000


def test_no_cash_falls_back_to_default():
    assert slot_even_amount(0, 5, 10_000) == 10_000
    assert slot_even_amount(None, 5, 10_000) == 10_000
