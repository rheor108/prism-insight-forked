# 실전 매매 전환 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 모의(demo) 검증이 끝난 KR/US 자동매매를, 잔고 비율(슬롯 균등) 매수와 4종 안전장치를 갖춰 실전(real) 계좌로 전환한다.

**Architecture:** 기존 매매 로직은 유지하고, (1) 공통 가드 모듈(`trading/trading_mode.py`)이 demo/real 결정·킬스위치·config 게터를 한곳에서 제공하고, (2) 각 trading 클래스에 슬롯 균등 금액 산정 메서드를 추가하고, (3) DB에 `account_mode` 컬럼을 더해 실전/모의 이력을 분리하며, (4) tracking agent 호출부가 이들을 엮어 실전 게이트를 통과할 때만 실거래한다.

**Tech Stack:** Python 3.10+, SQLite(`stock_tracking_db.sqlite`, KR/US 공유), KIS REST API, pytest, PyYAML.

## Global Constraints

- Python 3.10+ ; 코드 주석·로그는 영어, 텔레그램 메시지는 한글(기존 규칙).
- 코드 파일 변경이므로 **feature 브랜치 `feat/live-trading-transition`** 에서 작업, PR로 머지(`.py` 변경 규칙).
- `account_mode` 값은 정확히 `'demo'` 또는 `'real'` 두 가지.
- **실전 게이트 (전부 충족해야 실거래)**: config `default_mode == 'real'` AND env `PRISM_LIVE_TRADING == '1'` AND `trading/EMERGENCY_STOP` 파일 없음 AND 당일 실전 매수 건수 < `max_daily_buys`. 하나라도 불충족 시 `'demo'`로 폴백하거나 해당 매수를 스킵.
- 안전 폴백 원칙: 모드 판정에 실패하거나 모호하면 **항상 `'demo'`**.
- KR `stock_holdings` / US `us_stock_holdings` 는 PRIMARY KEY가 `ticker` → demo/real 동일 종목 동시 보유 불가. 실전 시작은 **빈 holdings 전제**(Task 12의 리셋 스크립트로 보장), 조회는 `account_mode` 필터로 방어.
- 기본값 하위호환: config에 `buy_sizing_mode`/`max_daily_buys` 키가 없으면 코드 기본값(`fixed` / `3`) 사용.
- 새 SQL은 기존 마이그레이션 패턴(`add_trigger_columns_if_missing` 스타일: `try: ALTER TABLE ADD COLUMN ... except: pass`)을 따른다.

---

### Task 1: 공통 가드 모듈 `trading/trading_mode.py`

**Files:**
- Create: `trading/trading_mode.py`
- Test: `tests/test_trading_mode.py`

**Interfaces:**
- Produces:
  - `resolve_trading_mode() -> str` — `'real'` 또는 `'demo'`
  - `is_emergency_stopped() -> bool`
  - `get_max_daily_buys() -> int`
  - `get_buy_sizing_mode() -> str`
  - 모듈 상수 `EMERGENCY_STOP_FILE: Path`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trading_mode.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/seungbum/prism-insight && python -m pytest tests/test_trading_mode.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading.trading_mode'`

- [ ] **Step 3: Write minimal implementation**

```python
# trading/trading_mode.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/seungbum/prism-insight && python -m pytest tests/test_trading_mode.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add trading/trading_mode.py tests/test_trading_mode.py
git commit -m "feat: add trading_mode guard module (resolve mode, kill switch, config getters)"
```

---

### Task 2: KR DB `account_mode` 마이그레이션

**Files:**
- Modify: `tracking/db_schema.py` (add function after `add_sector_column_if_missing`, ~line 297)
- Modify: `tracking/__init__.py` (re-export the new function — KR agent imports via `from tracking import ...`)
- Modify: `stock_tracking_agent.py:50-72` (import block) and `:159-165` (`_create_tables` — call new migration)
- Test: `tests/test_account_mode_migration.py`

**Interfaces:**
- Produces: `add_account_mode_column_if_missing(cursor, conn)` in `tracking/db_schema.py`. Adds `account_mode TEXT DEFAULT 'demo'` to `stock_holdings`, `trading_history`, `trading_journal`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_account_mode_migration.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/seungbum/prism-insight && python -m pytest tests/test_account_mode_migration.py -v`
Expected: FAIL with `ImportError: cannot import name 'add_account_mode_column_if_missing'`

- [ ] **Step 3: Write minimal implementation**

Add to `tracking/db_schema.py` (after `add_sector_column_if_missing`):

```python
def add_account_mode_column_if_missing(cursor, conn):
    """
    Add account_mode column to trading tables (demo/real split migration).

    Existing rows default to 'demo' so prior simulated trades stay labeled
    correctly. Idempotent: re-running is a no-op once the column exists.
    """
    tables = ["stock_holdings", "trading_history", "trading_journal"]
    for table in tables:
        try:
            cursor.execute(
                f"ALTER TABLE {table} ADD COLUMN account_mode TEXT DEFAULT 'demo'"
            )
            conn.commit()
            logger.info(f"Added account_mode column to {table} table")
        except Exception:
            pass  # Column already exists
```

Wire it into `stock_tracking_agent.py` `_create_tables` (after line 164, before `create_indexes`):

```python
    async def _create_tables(self):
        """Create necessary database tables (delegates to tracking.db_schema)"""
        create_all_tables(self.cursor, self.conn)
        add_scope_column_if_missing(self.cursor, self.conn)  # Must run before indexes
        add_trigger_columns_if_missing(self.cursor, self.conn)  # v1.16.5 migration
        add_sector_column_if_missing(self.cursor, self.conn)  # v1.17 migration for AI agent sector queries
        add_account_mode_column_if_missing(self.cursor, self.conn)  # live-trading demo/real split
        create_indexes(self.cursor, self.conn)
```

Re-export the new function from `tracking/__init__.py` so the agent's `from tracking import (...)` keeps working. In the `from tracking.db_schema import (` block (line 8-14) add `add_account_mode_column_if_missing,` and add `"add_account_mode_column_if_missing",` to `__all__` (after `"add_sector_column_if_missing",`).

Then add `add_account_mode_column_if_missing,` to the `from tracking import (` block in `stock_tracking_agent.py` (after `add_sector_column_if_missing,` at line 55).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/seungbum/prism-insight && python -m pytest tests/test_account_mode_migration.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add tracking/db_schema.py stock_tracking_agent.py tests/test_account_mode_migration.py
git commit -m "feat: add account_mode column migration for KR trading tables"
```

---

### Task 3: US DB `account_mode` 마이그레이션

**Files:**
- Modify: `prism-us/tracking/db_schema.py` (add function near other migrations; call it in `initialize_us_database` ~line 408-421 and in `async_initialize_us_database`)
- Modify: `prism-us/us_stock_tracking_agent.py:484` (`_create_tables`)
- Test: `prism-us/tests/test_us_account_mode_migration.py`

**Interfaces:**
- Produces: `add_account_mode_column_to_us_tables(cursor, conn)` in `prism-us/tracking/db_schema.py`. Adds `account_mode TEXT DEFAULT 'demo'` to `us_stock_holdings`, `us_trading_history`.

- [ ] **Step 1: Write the failing test**

```python
# prism-us/tests/test_us_account_mode_migration.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/seungbum/prism-insight/prism-us && python -m pytest tests/test_us_account_mode_migration.py -v`
Expected: FAIL with `ImportError: cannot import name 'add_account_mode_column_to_us_tables'`

- [ ] **Step 3: Write minimal implementation**

Add to `prism-us/tracking/db_schema.py` (after `add_sector_column_if_missing`):

```python
def add_account_mode_column_to_us_tables(cursor, conn):
    """
    Add account_mode column to US trading tables (demo/real split migration).

    Existing rows default to 'demo'. Idempotent.
    """
    tables = ["us_stock_holdings", "us_trading_history"]
    for table in tables:
        try:
            cursor.execute(
                f"ALTER TABLE {table} ADD COLUMN account_mode TEXT DEFAULT 'demo'"
            )
            conn.commit()
            logger.info(f"Added account_mode column to {table}")
        except Exception as e:
            if "duplicate column name" not in str(e).lower():
                logger.warning(f"Migration warning for {table}: {e}")
```

Call it inside `initialize_us_database` (after `add_sector_column_if_missing` if present, else after `add_market_column_to_shared_tables`):

```python
    # Add account_mode column for demo/real split
    add_account_mode_column_to_us_tables(cursor, conn)
```

And inside `async_initialize_us_database`, after the market-column loop:

```python
    for table in ["us_stock_holdings", "us_trading_history"]:
        try:
            await conn.execute(
                f"ALTER TABLE {table} ADD COLUMN account_mode TEXT DEFAULT 'demo'"
            )
        except Exception:
            pass
    await conn.commit()
```

Wire into `prism-us/us_stock_tracking_agent.py` `_create_tables` (line 484). Open the method, and after its existing migration calls add:

```python
        add_account_mode_column_to_us_tables(self.cursor, self.conn)
```

Ensure `add_account_mode_column_to_us_tables` is imported in that file's `from tracking.db_schema import (...)` block.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/seungbum/prism-insight/prism-us && python -m pytest tests/test_us_account_mode_migration.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add prism-us/tracking/db_schema.py prism-us/us_stock_tracking_agent.py prism-us/tests/test_us_account_mode_migration.py
git commit -m "feat: add account_mode column migration for US trading tables"
```

---

### Task 4: KR 슬롯 균등 매수 금액 산정

**Files:**
- Modify: `trading/domestic_stock_trading.py` (module-level pure fn + `DomesticStockTrading` method, near `calculate_buy_quantity` ~line 195)
- Test: `tests/test_slot_even_amount.py`

**Interfaces:**
- Consumes: `get_account_summary()` (returns dict with `available_amount`).
- Produces:
  - module fn `slot_even_amount(available_amount: float, remaining_slots: int, default_amount: int) -> int`
  - method `DomesticStockTrading.calculate_slot_even_amount(self, remaining_slots: int) -> int`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_slot_even_amount.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/seungbum/prism-insight && python -m pytest tests/test_slot_even_amount.py -v`
Expected: FAIL with `ImportError: cannot import name 'slot_even_amount'`

- [ ] **Step 3: Write minimal implementation**

Add module-level function near the top of `trading/domestic_stock_trading.py` (after `_cfg` load, before the class, ~line 40):

```python
def slot_even_amount(available_amount, remaining_slots: int, default_amount: int) -> int:
    """Evenly split available cash across remaining portfolio slots.

    Pure helper (no I/O) so it is trivially unit-testable. Returns
    default_amount when there is no free slot or no usable cash.
    """
    if remaining_slots <= 0:
        return default_amount
    if not available_amount or available_amount <= 0:
        return default_amount
    return math.floor(available_amount / remaining_slots)
```

Add method to `DomesticStockTrading` (after `calculate_buy_quantity`, ~line 225):

```python
    def calculate_slot_even_amount(self, remaining_slots: int) -> int:
        """Per-stock KRW amount = available cash / remaining slots.

        Falls back to the configured fixed buy amount if the balance
        inquiry fails.
        """
        summary = self.get_account_summary()
        available = float(summary.get("available_amount", 0)) if summary else 0
        amount = slot_even_amount(available, remaining_slots, self.buy_amount)
        logger.info(
            f"[Slot-even] available {available:,.0f} KRW / {remaining_slots} slots "
            f"-> {amount:,} KRW per stock"
        )
        return amount
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/seungbum/prism-insight && python -m pytest tests/test_slot_even_amount.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add trading/domestic_stock_trading.py tests/test_slot_even_amount.py
git commit -m "feat: add KR slot-even buy amount sizing"
```

---

### Task 5: US 슬롯 균등 매수 금액 산정

**Files:**
- Modify: `prism-us/trading/us_stock_trading.py` (module-level pure fn + `USStockTrading` method, near `calculate_buy_quantity` ~line 242)
- Test: `prism-us/tests/test_us_slot_even_amount.py`

**Interfaces:**
- Consumes: `get_account_summary()` (returns dict with `available_amount` in USD).
- Produces:
  - module fn `slot_even_amount(available_amount, remaining_slots: int, default_amount) -> int`
  - method `USStockTrading.calculate_slot_even_amount(self, remaining_slots: int) -> int`

- [ ] **Step 1: Write the failing test**

```python
# prism-us/tests/test_us_slot_even_amount.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading.us_stock_trading import slot_even_amount


def test_even_split_usd():
    assert slot_even_amount(10_000, 10, 100) == 1_000
    assert slot_even_amount(9_000, 9, 100) == 1_000


def test_zero_slots_falls_back():
    assert slot_even_amount(10_000, 0, 100) == 100


def test_no_cash_falls_back():
    assert slot_even_amount(0, 5, 100) == 100
    assert slot_even_amount(None, 5, 100) == 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/seungbum/prism-insight/prism-us && python -m pytest tests/test_us_slot_even_amount.py -v`
Expected: FAIL with `ImportError: cannot import name 'slot_even_amount'`

- [ ] **Step 3: Write minimal implementation**

Add module-level function near the top of `prism-us/trading/us_stock_trading.py` (after `_cfg` load, before `USStockTrading`):

```python
def slot_even_amount(available_amount, remaining_slots: int, default_amount) -> int:
    """Evenly split available USD cash across remaining portfolio slots.

    Pure helper. Returns default_amount when no free slot or no usable cash.
    """
    if remaining_slots <= 0:
        return default_amount
    if not available_amount or available_amount <= 0:
        return default_amount
    return math.floor(available_amount / remaining_slots)
```

Add method to `USStockTrading` (after `calculate_buy_quantity`, ~line 278):

```python
    def calculate_slot_even_amount(self, remaining_slots: int) -> int:
        """Per-stock USD amount = available USD cash / remaining slots.

        Falls back to the configured fixed USD buy amount on balance
        inquiry failure.
        """
        summary = self.get_account_summary()
        available = float(summary.get("available_amount", 0)) if summary else 0
        amount = slot_even_amount(available, remaining_slots, self.buy_amount)
        logger.info(
            f"[Slot-even] available ${available:,.2f} / {remaining_slots} slots "
            f"-> ${amount} per stock"
        )
        return amount
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/seungbum/prism-insight/prism-us && python -m pytest tests/test_us_slot_even_amount.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add prism-us/trading/us_stock_trading.py prism-us/tests/test_us_slot_even_amount.py
git commit -m "feat: add US slot-even buy amount sizing"
```

---

### Task 6: KR 모드 필터 조회 + 일일 매수 카운트 헬퍼

**Files:**
- Modify: `tracking/helpers.py` (`get_current_slots_count`, `is_ticker_in_holdings` add optional `account_mode`; add `count_today_buys`)
- Test: `tests/test_daily_buy_cap.py`

**Interfaces:**
- Produces:
  - `get_current_slots_count(cursor, account_mode: str = None) -> int` (filters by mode when given)
  - `count_today_buys(cursor, account_mode: str) -> int` (today's buys = holdings bought today + history rows bought today, for that mode)
- Note: `buy_date` is stored as a string beginning with `YYYY-MM-DD` (verify against `buy_stock` insert). `substr(buy_date,1,10)` compares the date prefix.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_daily_buy_cap.py
import datetime
import sqlite3

from tracking.db_schema import create_all_tables, add_account_mode_column_if_missing
from tracking.helpers import get_current_slots_count, count_today_buys

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/seungbum/prism-insight && python -m pytest tests/test_daily_buy_cap.py -v`
Expected: FAIL with `ImportError: cannot import name 'count_today_buys'`

- [ ] **Step 3: Write minimal implementation**

Replace `get_current_slots_count` in `tracking/helpers.py` (line 213) and add `count_today_buys`:

```python
def get_current_slots_count(cursor, account_mode: str = None) -> int:
    """Get current number of holdings, optionally filtered by account_mode."""
    try:
        if account_mode:
            cursor.execute(
                "SELECT COUNT(*) FROM stock_holdings WHERE account_mode = ?",
                (account_mode,),
            )
        else:
            cursor.execute("SELECT COUNT(*) FROM stock_holdings")
        return cursor.fetchone()[0]
    except Exception as e:
        logger.error(f"Error querying holdings count: {str(e)}")
        return 0


def count_today_buys(cursor, account_mode: str) -> int:
    """Count today's new buys for the given mode.

    Sums positions still held today plus same-day buys already sold (in
    trading_history), so the daily cap counts every buy event regardless of
    whether the position is still open.
    """
    import datetime as _dt
    today = _dt.datetime.now().strftime("%Y-%m-%d")
    total = 0
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM stock_holdings "
            "WHERE account_mode = ? AND substr(buy_date,1,10) = ?",
            (account_mode, today),
        )
        total += cursor.fetchone()[0]
        cursor.execute(
            "SELECT COUNT(*) FROM trading_history "
            "WHERE account_mode = ? AND substr(buy_date,1,10) = ?",
            (account_mode, today),
        )
        total += cursor.fetchone()[0]
    except Exception as e:
        logger.error(f"Error counting today's buys: {str(e)}")
    return total
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/seungbum/prism-insight && python -m pytest tests/test_daily_buy_cap.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add tracking/helpers.py tests/test_daily_buy_cap.py
git commit -m "feat: add mode-filtered slot count and daily buy counter (KR)"
```

---

### Task 7: US 모드 필터 조회 + 일일 매수 카운트 헬퍼

**Files:**
- Modify: `prism-us/tracking/db_schema.py` (`get_us_holdings_count` add optional `account_mode`; add `count_today_us_buys`)
- Test: `prism-us/tests/test_us_daily_buy_cap.py`

**Interfaces:**
- Produces:
  - `get_us_holdings_count(cursor, account_mode: str = None) -> int`
  - `count_today_us_buys(cursor, account_mode: str) -> int`

- [ ] **Step 1: Write the failing test**

```python
# prism-us/tests/test_us_daily_buy_cap.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/seungbum/prism-insight/prism-us && python -m pytest tests/test_us_daily_buy_cap.py -v`
Expected: FAIL with `ImportError: cannot import name 'count_today_us_buys'`

- [ ] **Step 3: Write minimal implementation**

Replace `get_us_holdings_count` in `prism-us/tracking/db_schema.py` (line 481) and add `count_today_us_buys`:

```python
def get_us_holdings_count(cursor, account_mode: str = None) -> int:
    """Get count of current US holdings, optionally filtered by account_mode."""
    if account_mode:
        cursor.execute(
            "SELECT COUNT(*) FROM us_stock_holdings WHERE account_mode = ?",
            (account_mode,),
        )
    else:
        cursor.execute("SELECT COUNT(*) FROM us_stock_holdings")
    return cursor.fetchone()[0]


def count_today_us_buys(cursor, account_mode: str) -> int:
    """Count today's new US buys for the given mode (holdings + same-day sold)."""
    import datetime as _dt
    today = _dt.datetime.now().strftime("%Y-%m-%d")
    total = 0
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM us_stock_holdings "
            "WHERE account_mode = ? AND substr(buy_date,1,10) = ?",
            (account_mode, today),
        )
        total += cursor.fetchone()[0]
        cursor.execute(
            "SELECT COUNT(*) FROM us_trading_history "
            "WHERE account_mode = ? AND substr(buy_date,1,10) = ?",
            (account_mode, today),
        )
        total += cursor.fetchone()[0]
    except Exception as e:
        logger.error(f"Error counting today's US buys: {str(e)}")
    return total
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/seungbum/prism-insight/prism-us && python -m pytest tests/test_us_daily_buy_cap.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add prism-us/tracking/db_schema.py prism-us/tests/test_us_daily_buy_cap.py
git commit -m "feat: add mode-filtered slot count and daily buy counter (US)"
```

---

### Task 8: KR 킬스위치 가드 통합

**Files:**
- Modify: `trading/domestic_stock_trading.py` (`_execute_buy_stock` ~line 1095, `_execute_sell_stock` ~line 1178; import `is_emergency_stopped`)
- Test: covered by Task 1's `is_emergency_stopped` test; integration verified manually (Step 4).

**Interfaces:**
- Consumes: `trading_mode.is_emergency_stopped()`.

- [ ] **Step 1: Add the import**

At top of `trading/domestic_stock_trading.py` (the file already does `sys.path.insert(0, str(TRADING_DIR))`, so `trading_mode` is importable as a sibling):

```python
from trading_mode import is_emergency_stopped
```

- [ ] **Step 2: Guard the buy path**

In `_execute_buy_stock`, immediately after `amount = buy_amount if buy_amount else self.buy_amount` (line 1097) and before building `result`, add:

```python
        if is_emergency_stopped():
            logger.warning(f"[Async Buy API] {stock_code} BLOCKED by EMERGENCY_STOP kill switch")
            return {
                'success': False, 'stock_code': stock_code, 'current_price': 0,
                'quantity': 0, 'total_amount': 0, 'order_no': None,
                'message': 'Blocked by emergency stop', 'timestamp': datetime.datetime.now().isoformat()
            }
```

- [ ] **Step 3: Guard the sell path**

In `_execute_sell_stock`, at the very start of the method body (after the `result = {...}` dict is built), add:

```python
        if is_emergency_stopped():
            logger.warning(f"[Async Sell API] {stock_code} BLOCKED by EMERGENCY_STOP kill switch")
            result['message'] = 'Blocked by emergency stop'
            return result
```

- [ ] **Step 4: Verify manually**

Run:
```bash
cd /home/seungbum/prism-insight && touch trading/EMERGENCY_STOP && \
python -c "
import asyncio
from trading.domestic_stock_trading import AsyncTradingContext
async def main():
    async with AsyncTradingContext(mode='demo') as t:
        r = await t.async_buy_stock(stock_code='005930', limit_price=70000)
        print('BUY blocked:', not r['success'], '|', r['message'])
asyncio.run(main())
" ; rm -f trading/EMERGENCY_STOP
```
Expected: `BUY blocked: True | Blocked by emergency stop`

- [ ] **Step 5: Commit**

```bash
git add trading/domestic_stock_trading.py
git commit -m "feat: integrate emergency-stop kill switch into KR buy/sell"
```

---

### Task 9: US 킬스위치 가드 통합

**Files:**
- Modify: `prism-us/trading/us_stock_trading.py` (`_execute_buy_stock` ~line 1033, `_execute_sell_stock` ~line 1140; import `is_emergency_stopped`)
- Test: integration verified manually (Step 4).

**Interfaces:**
- Consumes: `trading_mode.is_emergency_stopped()`.

- [ ] **Step 1: Add the import**

`us_stock_trading.py` imports `kis_auth as ka` from the shared `trading/` dir. Match that import style for `trading_mode`. Inspect the top of the file to see how `kis_auth` is imported (likely a `sys.path.insert` to the main `trading/` dir), then add alongside it:

```python
from trading_mode import is_emergency_stopped
```

(If the file imports via `from trading.kis_auth import ...` instead, use `from trading.trading_mode import is_emergency_stopped`.)

- [ ] **Step 2: Guard the buy path**

In `_execute_buy_stock`, right after `amount = buy_amount if buy_amount else self.buy_amount` (line 1036), add:

```python
        if is_emergency_stopped():
            logger.warning(f"[Async Buy] {ticker} BLOCKED by EMERGENCY_STOP kill switch")
            return {
                'success': False, 'ticker': ticker, 'current_price': 0,
                'quantity': 0, 'total_amount': 0, 'order_no': None,
                'message': 'Blocked by emergency stop', 'timestamp': datetime.datetime.now().isoformat()
            }
```

- [ ] **Step 3: Guard the sell path**

In `_execute_sell_stock`, after the initial `result = {...}` dict (line 1152), add:

```python
        if is_emergency_stopped():
            logger.warning(f"[Async Sell] {ticker} BLOCKED by EMERGENCY_STOP kill switch")
            result['message'] = 'Blocked by emergency stop'
            return result
```

- [ ] **Step 4: Verify manually**

Run:
```bash
cd /home/seungbum/prism-insight && touch trading/EMERGENCY_STOP && \
python -c "
import sys; sys.path.insert(0, 'prism-us')
import asyncio
from trading.us_stock_trading import AsyncUSTradingContext
async def main():
    async with AsyncUSTradingContext(mode='demo') as t:
        r = await t.async_buy_stock(ticker='AAPL', limit_price=200)
        print('BUY blocked:', not r['success'], '|', r['message'])
asyncio.run(main())
" ; rm -f trading/EMERGENCY_STOP
```
Expected: `BUY blocked: True | Blocked by emergency stop`

- [ ] **Step 5: Commit**

```bash
git add prism-us/trading/us_stock_trading.py
git commit -m "feat: integrate emergency-stop kill switch into US buy/sell"
```

---

### Task 10: KR 호출부 통합 (mode 주입 + 슬롯 균등 + 일일 한도 + account_mode 기록 + 실전 알림)

**Files:**
- Modify: `stock_tracking_agent.py` — buy call site (line 1359-1373), `_get_current_slots_count` (line 185-187), `buy_stock` (the method that INSERTs into `stock_holdings`), and `run`/`process` entry for the live-mode alert.
- Test: manual demo regression (Step 5).

**Interfaces:**
- Consumes: `resolve_trading_mode`, `get_buy_sizing_mode`, `get_max_daily_buys` (Task 1); `count_today_buys`, `get_current_slots_count(account_mode)` (Task 6); `calculate_slot_even_amount` (Task 4).

- [ ] **Step 1: Update slot-count delegate to pass mode**

`stock_tracking_agent.py:185`:

```python
    async def _get_current_slots_count(self, account_mode: str = None) -> int:
        """Get current number of holdings (delegates to tracking.helpers)"""
        return get_current_slots_count(self.cursor, account_mode)
```

- [ ] **Step 2: Record account_mode on buy**

In `stock_tracking_agent.py:576` extend the `buy_stock` signature with `account_mode`:

```python
    async def buy_stock(self, ticker: str, company_name: str, current_price: float, scenario: Dict[str, Any], rank_change_msg: str = "", account_mode: str = "demo") -> bool:
```

Then update the INSERT (lines 623-642) to add the `account_mode` column and value. (The internal `_is_ticker_in_holdings`/`_get_current_slots_count()` checks above stay unchanged: Task 12 empties `stock_holdings` before going live, so it holds a single mode's rows.)

```python
            self.cursor.execute(
                """
                INSERT INTO stock_holdings
                (ticker, company_name, buy_price, buy_date, current_price, last_updated, scenario, target_price, stop_loss, trigger_type, trigger_mode, account_mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticker,
                    company_name,
                    current_price,
                    now,
                    current_price,
                    now,
                    json.dumps(scenario, ensure_ascii=False),
                    scenario.get('target_price', 0),
                    scenario.get('stop_loss', 0),
                    trigger_type,
                    trigger_mode,
                    account_mode,
                )
            )
```

- [ ] **Step 3: Wire the buy call site**

Replace lines 1359-1373 with:

```python
                if analysis_result.get("decision") == "Enter":
                    from trading.trading_mode import (
                        resolve_trading_mode, get_buy_sizing_mode, get_max_daily_buys,
                    )
                    from tracking.helpers import count_today_buys
                    trading_mode = resolve_trading_mode()

                    # Daily buy cap
                    if count_today_buys(self.cursor, trading_mode) >= get_max_daily_buys():
                        logger.warning(
                            f"Daily buy cap ({get_max_daily_buys()}) reached for "
                            f"{trading_mode}; skipping {company_name}({ticker})"
                        )
                        continue

                    # Process buy (record account_mode)
                    buy_success = await self.buy_stock(
                        ticker, company_name, current_price, scenario,
                        rank_change_msg, account_mode=trading_mode,
                    )

                    if buy_success:
                        from trading.domestic_stock_trading import AsyncTradingContext
                        async with AsyncTradingContext(mode=trading_mode) as trading:
                            # Slot-even sizing (fall back to fixed when configured)
                            buy_amount = None
                            if get_buy_sizing_mode() == "slot_even":
                                occupied = await self._get_current_slots_count(trading_mode)
                                remaining = self.max_slots - occupied
                                buy_amount = await asyncio.to_thread(
                                    trading.calculate_slot_even_amount, remaining
                                )
                            trade_result = await trading.async_buy_stock(
                                stock_code=ticker, buy_amount=buy_amount,
                                limit_price=current_price,
                            )

                        if trade_result['success']:
                            logger.info(f"Actual purchase successful: {trade_result['message']}")
                        else:
                            logger.error(f"Actual purchase failed: {trade_result['message']}")
```

Keep the existing Redis/GCP publish blocks and the `buy_count += 1` logic that follow.

- [ ] **Step 4: Add live-mode startup alert**

In `run` (line 1658), right after entry/initialization and before processing reports, add a one-time warning when live:

```python
        from trading.trading_mode import resolve_trading_mode
        if resolve_trading_mode() == "real":
            warn = "⚠️ 실전 매매 모드가 활성화되었습니다 (KR). 실제 주문이 체결됩니다."
            logger.warning(warn)
            # best-effort telegram notice (non-blocking, ignore failures)
            if self.telegram_bot and chat_id:
                try:
                    await self.telegram_bot.send_message(chat_id=chat_id, text=warn)
                except Exception as e:
                    logger.warning(f"Live-mode telegram notice failed: {e}")
```

- [ ] **Step 5: Verify demo regression (no real orders)**

Run:
```bash
cd /home/seungbum/prism-insight && python -c "
import asyncio, sqlite3, tempfile, os
from stock_tracking_agent import StockTrackingAgent
async def main():
    db = tempfile.mktemp(suffix='.sqlite')
    a = StockTrackingAgent(db_path=db)
    await a.initialize()
    # account_mode column present after init
    a.cursor.execute('PRAGMA table_info(stock_holdings)')
    cols = [r[1] for r in a.cursor.fetchall()]
    print('account_mode present:', 'account_mode' in cols)
    print('slots(real)=', await a._get_current_slots_count('real'))
    os.remove(db)
asyncio.run(main())
"
```
Expected: `account_mode present: True` and `slots(real)= 0`

- [ ] **Step 6: Commit**

```bash
git add stock_tracking_agent.py
git commit -m "feat: wire KR live mode, slot-even sizing, daily cap, account_mode"
```

---

### Task 11: US 호출부 통합

**Files:**
- Modify: `prism-us/us_stock_tracking_agent.py` — buy call site (line 2052-2068), `_get_current_slots_count` (line 536-538), the `buy_stock` method that INSERTs into `us_stock_holdings` (~line 879), and `run` entry for the live alert.
- Test: manual demo regression (Step 5).

**Interfaces:**
- Consumes: `resolve_trading_mode`, `get_buy_sizing_mode`, `get_max_daily_buys` (Task 1); `count_today_us_buys`, `get_us_holdings_count(account_mode)` (Task 7); `calculate_slot_even_amount` (Task 5).

- [ ] **Step 1: Update slot-count delegate to pass mode**

`prism-us/us_stock_tracking_agent.py:536`:

```python
    async def _get_current_slots_count(self, account_mode: str = None) -> int:
        return get_us_holdings_count(self.cursor, account_mode)
```

- [ ] **Step 2: Record account_mode on buy**

In `prism-us/us_stock_tracking_agent.py:841` extend the `buy_stock` signature:

```python
    async def buy_stock(self, ticker: str, company_name: str, current_price: float,
                        scenario: Dict[str, Any], rank_change_msg: str = "", account_mode: str = "demo") -> bool:
```

Then update the INSERT (lines 877-898) to add the `account_mode` column and value:

```python
            self.cursor.execute(
                """
                INSERT INTO us_stock_holdings
                (ticker, company_name, buy_price, buy_date, current_price, last_updated,
                 scenario, target_price, stop_loss, trigger_type, trigger_mode, sector, account_mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticker,
                    company_name,
                    current_price,
                    now,
                    current_price,
                    now,
                    json.dumps(scenario, ensure_ascii=False),
                    scenario.get('target_price', 0),
                    scenario.get('stop_loss', 0),
                    trigger_type,
                    trigger_mode,
                    scenario.get('sector', 'Unknown'),
                    account_mode,
                )
            )
```

- [ ] **Step 3: Wire the buy call site**

Replace lines 2052-2068 (`buy_success = await self.buy_stock(...)` through the `async with AsyncUSTradingContext()` block) with:

```python
                    from trading.trading_mode import (
                        resolve_trading_mode, get_buy_sizing_mode, get_max_daily_buys,
                    )
                    from tracking.db_schema import count_today_us_buys
                    trading_mode = resolve_trading_mode()

                    if count_today_us_buys(self.cursor, trading_mode) >= get_max_daily_buys():
                        logger.warning(
                            f"Daily buy cap ({get_max_daily_buys()}) reached for "
                            f"{trading_mode}; skipping {company_name}({ticker})"
                        )
                        continue

                    buy_success = await self.buy_stock(
                        ticker, company_name, current_price, scenario,
                        rank_change_msg, account_mode=trading_mode,
                    )

                    if buy_success:
                        trade_result = {'success': False, 'message': 'Trading not executed'}
                        if current_price > 0:
                            try:
                                try:
                                    from trading.us_stock_trading import AsyncUSTradingContext
                                except ImportError:
                                    from prism_us.trading.us_stock_trading import AsyncUSTradingContext
                                async with AsyncUSTradingContext(mode=trading_mode) as trading:
                                    buy_amount = None
                                    if get_buy_sizing_mode() == "slot_even":
                                        occupied = await self._get_current_slots_count(trading_mode)
                                        remaining = self.max_slots - occupied
                                        buy_amount = await asyncio.to_thread(
                                            trading.calculate_slot_even_amount, remaining
                                        )
                                    trade_result = await trading.async_buy_stock(
                                        ticker=ticker, buy_amount=buy_amount,
                                        limit_price=current_price,
                                    )
                                if trade_result['success']:
                                    logger.info(f"Actual purchase successful: {trade_result['message']}")
                                else:
                                    logger.error(f"Actual purchase failed: {trade_result['message']}")
                            except Exception as trade_err:
                                logger.warning(f"Trading execution skipped: {trade_err}")
                        else:
                            logger.warning(f"Skipping actual purchase for {ticker}: invalid current_price ({current_price})")
```

Keep the existing Redis/GCP publish blocks and `buy_count += 1` that follow. Note `trading_mode` import is `from trading.trading_mode import ...` (the US agent already imports the main `trading` package in this block); if that path is unavailable use the same fallback pattern as `AsyncUSTradingContext`.

- [ ] **Step 4: Add live-mode startup alert**

In `run` (line ~2520, near where `send_telegram_message` is first used), add:

```python
        from trading.trading_mode import resolve_trading_mode
        if resolve_trading_mode() == "real":
            warn = "⚠️ 실전 매매 모드가 활성화되었습니다 (US). 실제 주문이 체결됩니다."
            logger.warning(warn)
            if self.telegram_bot and chat_id:
                try:
                    await self.telegram_bot.send_message(chat_id=chat_id, text=warn)
                except Exception as e:
                    logger.warning(f"Live-mode telegram notice failed: {e}")
```

- [ ] **Step 5: Verify demo regression**

Run:
```bash
cd /home/seungbum/prism-insight/prism-us && python -c "
import asyncio, tempfile, os, sys
sys.path.insert(0, '.')
from us_stock_tracking_agent import USStockTrackingAgent
async def main():
    db = tempfile.mktemp(suffix='.sqlite')
    a = USStockTrackingAgent(db_path=db)
    await a.initialize()
    a.cursor.execute('PRAGMA table_info(us_stock_holdings)')
    cols = [r[1] for r in a.cursor.fetchall()]
    print('account_mode present:', 'account_mode' in cols)
    print('slots(real)=', await a._get_current_slots_count('real'))
    os.remove(db)
asyncio.run(main())
"
```
Expected: `account_mode present: True` and `slots(real)= 0`
(If the agent class name differs, adjust the import — confirm via `grep -n "^class .*Agent" prism-us/us_stock_tracking_agent.py`.)

- [ ] **Step 6: Commit**

```bash
git add prism-us/us_stock_tracking_agent.py
git commit -m "feat: wire US live mode, slot-even sizing, daily cap, account_mode"
```

---

### Task 12: config 템플릿 + holdings 리셋 스크립트 + 실전 전환 가이드

**Files:**
- Modify: `trading/config/kis_devlp.yaml.example`
- Create: `scripts/reset_holdings_for_live.py`
- Create: `docs/LIVE_TRADING_SETUP_ko.md`
- Test: script smoke test (Step 4).

- [ ] **Step 1: Update config template**

In `trading/config/kis_devlp.yaml.example`, after `default_mode: demo` (line 12), add:

```yaml

# 매수 금액 산정 방식 (slot_even: 가용현금/잔여슬롯 균등 배분, fixed: 위 고정금액)
buy_sizing_mode: fixed
# 시장당 하루 최대 신규매수 종목 수 (폭주 방지)
max_daily_buys: 3
```

- [ ] **Step 2: Create the holdings reset script**

```python
# scripts/reset_holdings_for_live.py
"""
Archive and clear simulated holdings before starting LIVE trading.

stock_holdings / us_stock_holdings use ticker as PRIMARY KEY, so leftover demo
positions would block real buys of the same ticker. This archives them into
*_holdings_demo_archive and empties the live holdings tables. Trade history and
journals are preserved (they carry account_mode).

Usage:
    python scripts/reset_holdings_for_live.py --confirm
"""
import argparse
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "stock_tracking_db.sqlite"


def _archive_and_clear(cur, table):
    archive = f"{table}_demo_archive"
    cur.execute(f"CREATE TABLE IF NOT EXISTS {archive} AS SELECT * FROM {table} WHERE 0")
    cur.execute(f"INSERT INTO {archive} SELECT * FROM {table}")
    moved = cur.rowcount
    cur.execute(f"DELETE FROM {table}")
    return moved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm", action="store_true", help="actually perform the reset")
    args = ap.parse_args()

    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()
    for table in ["stock_holdings", "us_stock_holdings"]:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            n = cur.fetchone()[0]
        except sqlite3.OperationalError:
            print(f"  {table}: not found, skipping")
            continue
        if not args.confirm:
            print(f"  {table}: {n} rows would be archived+cleared (dry-run)")
            continue
        moved = _archive_and_clear(cur, table)
        conn.commit()
        print(f"  {table}: archived {moved} rows -> {table}_demo_archive, cleared")
    conn.close()
    if not args.confirm:
        print("\nDry-run only. Re-run with --confirm to apply.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Create the setup guide**

```markdown
# 실전 매매 전환 가이드 (LIVE_TRADING_SETUP_ko.md)

## 1. 한국투자증권 실전 API 발급
1. https://apiportal.koreainvestment.com 접속 → 로그인
2. **실전투자** 앱 등록 → App Key / App Secret 발급 (실전 키는 `PS`로 시작, `PSVT` 아님)
3. **해외주식** 사용 신청/활성화 (국내와 별도)
4. 실전 계좌 개설 및 모의→실전 약관 동의

## 2. config 입력 (`trading/config/kis_devlp.yaml`)
- `my_app`, `my_sec`: 실전 App Key/Secret
- `my_acct_stock`: 실전 계좌번호 앞 8자리
- `buy_sizing_mode: slot_even` (잔고 비율 매수)
- `max_daily_buys: 3` (필요 시 조정)
- ⚠️ `default_mode`는 아직 `demo`로 두세요 (3단계에서 전환)

## 3. 토큰 캐시 리셋
```bash
rm -f trading/config/KIS*
```

## 4. 모의 포지션 정리 (실전은 빈 포트폴리오로 시작)
```bash
python scripts/reset_holdings_for_live.py            # dry-run 확인
python scripts/reset_holdings_for_live.py --confirm  # 실제 정리
```

## 5. 실전 전환 (4중 게이트)
```bash
# config: default_mode: real 로 변경
export PRISM_LIVE_TRADING=1        # 환경변수 가드
# 정상 운영. 긴급 정지가 필요하면:
touch trading/EMERGENCY_STOP       # 모든 실매매 즉시 차단
rm trading/EMERGENCY_STOP          # 해제
```

실전 게이트: `default_mode:real` + `PRISM_LIVE_TRADING=1` + `EMERGENCY_STOP` 없음 + 당일 매수 < `max_daily_buys` 를 **모두** 만족해야 실거래됩니다. 하나라도 빠지면 모의로 폴백/스킵됩니다.

## 6. 검증
- 첫날은 `max_daily_buys`를 낮게(예: 1~2) 두고 소액 슬롯 균등으로 체결 확인
- 로그에서 env `prod`, 실전 계좌번호, `openapi.koreainvestment.com` 확인
- 킬스위치(`touch trading/EMERGENCY_STOP`)로 즉시 차단되는지 확인
```

- [ ] **Step 4: Smoke-test the script (dry-run)**

Run: `cd /home/seungbum/prism-insight && python scripts/reset_holdings_for_live.py`
Expected: dry-run lines like `stock_holdings: N rows would be archived+cleared (dry-run)` and the "Dry-run only" notice. No data modified.

- [ ] **Step 5: Commit**

```bash
git add trading/config/kis_devlp.yaml.example scripts/reset_holdings_for_live.py docs/LIVE_TRADING_SETUP_ko.md
git commit -m "feat: add live config knobs, holdings reset script, setup guide"
```

---

## Final Verification (after all tasks)

- [ ] Run full new test suite (KR): `cd /home/seungbum/prism-insight && python -m pytest tests/test_trading_mode.py tests/test_account_mode_migration.py tests/test_slot_even_amount.py tests/test_daily_buy_cap.py -v`
- [ ] Run full new test suite (US): `cd /home/seungbum/prism-insight/prism-us && python -m pytest tests/test_us_account_mode_migration.py tests/test_us_slot_even_amount.py tests/test_us_daily_buy_cap.py -v`
- [ ] Demo regression KR: `python stock_analysis_orchestrator.py --mode morning --no-telegram` completes without trade errors (or the lighter agent smoke test in Task 10 Step 5).
- [ ] Confirm with `PRISM_LIVE_TRADING` unset, `resolve_trading_mode()` returns `'demo'` even if `default_mode: real`.
- [ ] Open PR `feat/live-trading-transition` → review → merge.
- [ ] Hand off `docs/LIVE_TRADING_SETUP_ko.md` to the user for Phase 2 (credential issuance) and Phase 3 (cutover).
