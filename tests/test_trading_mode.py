import importlib
from pathlib import Path

import trading.trading_mode as tm


def test_resolve_real_requires_both_config_and_env(monkeypatch):
    monkeypatch.setattr(tm, "_load_config", lambda: {"default_mode": "real"})
    monkeypatch.setenv("PRISM_LIVE_TRADING", "1")
    assert tm.resolve_trading_mode() == "real"


def test_resolve_demo_when_env_missing(monkeypatch):
    monkeypatch.setattr(tm, "_load_config", lambda: {"default_mode": "real"})
    monkeypatch.delenv("PRISM_LIVE_TRADING", raising=False)
    assert tm.resolve_trading_mode() == "demo"


def test_resolve_demo_when_config_demo(monkeypatch):
    monkeypatch.setattr(tm, "_load_config", lambda: {"default_mode": "demo"})
    monkeypatch.setenv("PRISM_LIVE_TRADING", "1")
    assert tm.resolve_trading_mode() == "demo"


def test_emergency_stop_detected(monkeypatch, tmp_path):
    flag = tmp_path / "EMERGENCY_STOP"
    monkeypatch.setattr(tm, "EMERGENCY_STOP_FILE", flag)
    assert tm.is_emergency_stopped() is False
    flag.write_text("stop")
    assert tm.is_emergency_stopped() is True


def test_config_getters_defaults(monkeypatch):
    monkeypatch.setattr(tm, "_load_config", lambda: {})
    assert tm.get_max_daily_buys() == 3
    assert tm.get_buy_sizing_mode() == "fixed"
