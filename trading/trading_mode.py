"""
Trading mode resolution and live-trading safety guards.

Single source of truth for the demo/real decision, the emergency-stop kill
switch, and live-trading config knobs. Shared by KR and US trading paths.
"""
import logging
import os
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

TRADING_DIR = Path(__file__).parent
CONFIG_FILE = TRADING_DIR / "config" / "kis_devlp.yaml"
EMERGENCY_STOP_FILE = TRADING_DIR / "EMERGENCY_STOP"


def _load_config() -> dict:
    """Load kis_devlp.yaml; return {} on any failure (safe default)."""
    try:
        with open(CONFIG_FILE, encoding="UTF-8") as f:
            return yaml.load(f, Loader=yaml.FullLoader) or {}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"trading_mode: config load failed ({e}); using defaults")
        return {}


def resolve_trading_mode() -> str:
    """Resolve effective trading mode with a dual guard.

    Returns 'real' ONLY when config default_mode == 'real' AND
    env PRISM_LIVE_TRADING == '1'. Otherwise 'demo' (safe fallback).
    """
    config_mode = str(_load_config().get("default_mode", "demo")).strip().lower()
    env_flag = os.environ.get("PRISM_LIVE_TRADING", "").strip()
    if config_mode == "real" and env_flag == "1":
        return "real"
    return "demo"


def is_emergency_stopped() -> bool:
    """True if the emergency-stop kill switch file exists."""
    return EMERGENCY_STOP_FILE.exists()


def get_max_daily_buys() -> int:
    """Per-market daily new-buy cap (default 3)."""
    try:
        return int(_load_config().get("max_daily_buys", 3))
    except (TypeError, ValueError):
        return 3


def get_buy_sizing_mode() -> str:
    """'slot_even' or 'fixed' (default 'fixed' for backward compatibility)."""
    return str(_load_config().get("buy_sizing_mode", "fixed")).strip().lower()
