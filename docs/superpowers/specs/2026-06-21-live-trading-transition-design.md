# 실전 매매 전환 설계 (Live Trading Transition)

> **작성일**: 2026-06-21 | **상태**: 설계 확정(사용자 검토 대기) | **대상**: KR + US

## 1. 배경 & 목표

모의투자(demo) 계좌에서 기존 매매 로직 검증이 끝났다. 이제 실전(real) 계좌로 실제 매매를 진행한다.
단순히 config의 `default_mode`를 `real`로 바꾸는 것이 아니라, 실거래에 필요한 **자금 관리(잔고 비율 매수)**와
**안전장치 4종**을 함께 갖춰 안전하게 전환하는 것이 목표다.

핵심 제약:
- 실전 자격증명이 **아직 발급 전**이다 → 키 발급은 사용자 액션이며, 그 전에 코드(잔고 비율 매수 + 안전장치)를
  **모의 모드에서 먼저 완성·검증**해 둔다.
- KR(국내주식)과 US(해외주식) **양쪽 모두** 전환한다.

## 2. 결정 요약 (사용자 선택)

| 항목 | 결정 |
|------|------|
| 대상 시장 | KR + US 둘 다 |
| 매수 금액 산정 | **슬롯 균등 배분** — 가용현금 ÷ 잔여 슬롯 |
| 안전장치 | DB 실전/모의 분리, 실전 활성화 가드+알림, 일일 매수 한도, 비상정지 킬스위치 (4종 전부) |
| DB 분리 방식 | **mode 컬럼 추가** (`account_mode`) |
| 일일 매수 한도 | **시장당 하루 3종목** (config로 조정 가능) |
| 실전 자격증명 | 미발급 → 발급 절차 안내 후 사용자가 입력 |

## 3. 현재 구조 (확인된 사실)

### 3.1 demo/real 전환 메커니즘
- `trading/config/kis_devlp.yaml:12` `default_mode: demo` 가 기본 모드 결정.
- `DomesticStockTrading.__init__` (`trading/domestic_stock_trading.py:52`)에서
  `self.env = "vps" if mode == "demo" else "prod"` (`:65`) → `ka.auth(svr=self.env)` (`:71`).
- 자격증명: demo = `paper_app`/`paper_sec`/`my_paper_stock`, real = `my_app`/`my_sec`/`my_acct_stock`
  (`trading/kis_auth.py:changeTREnv` `:682`).
- URL(`openapivts...:29443` ↔ `openapi...:9443`)과 TR ID(`VTTC0012U` ↔ `TTTC0012U`)는 모드에 따라 **자동 전환**.
- 자격증명 검증 가드: 모의키(`PSVT*`)를 real 모드에 쓰면 `CredentialMismatchError`
  (`trading/kis_auth.py:validate_credentials` `:433`).
- **현재 상태**: demo 항목은 채워짐, real 항목(`my_app`/`my_sec`/`my_acct_stock`)은 전부 placeholder.

### 3.2 매수 흐름 & 잔고 조회
- KR 매수 수량: `math.floor(amount / current_price)`
  (`domestic_stock_trading.py:_execute_buy_stock` `:1135`; `calculate_buy_quantity` `:195`).
- KR 잔고 조회: `get_account_summary()` (`domestic_stock_trading.py:1410`) → `available_amount`(주문가능현금
  `ord_psbl_cash`), `deposit`(예수금). TR `TTTC8434R`/`VTTC8434R`.
- US 매수 수량: `math.floor(amount / current_price)` (`us_stock_trading.py:_execute_buy_stock` `:1071`).
  예약주문은 `limit_price` 필수 (`buy_reserved_order` `:644`).
- US 잔고 조회: `get_account_summary()` (`us_stock_trading.py:1318`) → `available_amount`(USD), `usd_cash`,
  `exchange_rate`. TR `CTRP6504R`.
- `buy_amount`는 `__init__`/`AsyncTradingContext`/per-call 모두로 주입 가능 (이미 파라미터화됨).

### 3.3 운영 호출부 & 슬롯 상수
- KR 매수 트리거: `stock_tracking_agent.py:1366` `async with AsyncTradingContext() as trading:` →
  `async_buy_stock(stock_code=ticker, limit_price=current_price)` (mode 인자 없음 → 현재 항상 demo).
- US 매수 트리거: `prism-us/us_stock_tracking_agent.py:2065` (유사 구조).
- 슬롯 상수: `MAX_SLOTS = 10`, `MAX_SAME_SECTOR = 3` 는 **tracking agent**에 정의
  (`stock_tracking_agent.py:78-79`, `us_stock_tracking_agent.py:387-388`). agent가 보유종목수/슬롯 컨텍스트를 보유.

### 3.4 DB 스키마
- KR: `tracking/db_schema.py` — `stock_holdings`(:13), `trading_history`(:31), `trading_journal`(:49).
- US: `prism-us/tracking/db_schema.py` — `us_stock_holdings`, `us_trading_history`.
- 현재 demo/real 구분 컬럼 없음.

## 4. 설계

### 4.1 잔고 비율 매수 (슬롯 균등)

**산정 규칙**
- 종목당 금액 = `floor(가용현금 ÷ max(1, MAX_SLOTS − 보유종목수))`
- 수량 = `floor(종목당 금액 ÷ 현재가)`, **1주 미만이면 매수 스킵**
- 가용현금 기준 = `get_account_summary()['available_amount']` (주문가능현금 / US는 가용 USD)
- KR 계좌와 US 계좌는 **각각 독립** 적용
- 점감 없음 증명: 1,000만/10슬롯=100만 매수 → 900만/9슬롯=100만 → … 매 종목 균등

**구현 구조** (관심사 분리)
- `trading` 클래스에 신규 메서드: `calculate_slot_even_amount(self, remaining_slots: int) -> int`
  - 내부에서 `get_account_summary()`로 가용현금 조회 → `floor(available / max(1, remaining_slots))` 반환
  - 잔고 조회 실패 시: 경고 로그 + 고정금액 폴백(`self.buy_amount`)
  - KR(`domestic_stock_trading.py`)/US(`us_stock_trading.py`) 각각 추가
- tracking agent 매수 호출부에서 잔여슬롯 계산 후 호출:
  ```python
  async with AsyncTradingContext(mode=resolve_trading_mode()) as trading:
      if buy_sizing_mode == "slot_even":
          remaining = self.max_slots - current_holdings_count
          buy_amount = trading.calculate_slot_even_amount(remaining)
      else:
          buy_amount = None  # 기존 고정금액 방식
      result = await trading.async_buy_stock(
          stock_code=ticker, buy_amount=buy_amount, limit_price=current_price)
  ```
  - `current_holdings_count`: agent가 슬롯 체크 시점에 이미 보유 중인 값 사용 (현재 모드 기준 — 4.2.1과 연동)

**config 추가** (`trading/config/kis_devlp.yaml`)
```yaml
buy_sizing_mode: slot_even   # slot_even | fixed. config 키가 없으면 코드 기본값은 fixed(하위호환)
```

### 4.2 안전장치 4종

#### 4.2.1 DB 실전/모의 분리 (mode 컬럼)
- 거래/보유 테이블에 `account_mode TEXT DEFAULT 'demo'` 컬럼 추가.
  - KR: `stock_holdings`, `trading_history`, `trading_journal`.
  - US: `us_stock_holdings`, `us_trading_history` (+ US journal 테이블이 있으면 동일 적용 — 구현 시
    `prism-us/tracking/db_schema.py`에서 정확한 거래 테이블 목록 확정).
- 마이그레이션: `ALTER TABLE ... ADD COLUMN account_mode TEXT DEFAULT 'demo'` → 기존 행은 자동으로 'demo'.
- **삽입 시**: 현재 거래 모드 값을 기록.
- **조회 시**: 보유종목·슬롯 카운트·일일 한도 카운트 모두 `WHERE account_mode = <현재모드>` 필터.
  → 실전 모드에서는 real 행만 보여 **빈 포트폴리오로 시작**하고, 슬롯·한도 카운트도 실전 거래만 집계.
- `db_schema.py`(KR/US)에 컬럼 정의 + 멱등 마이그레이션 함수 추가.

#### 4.2.2 실전 활성화 가드 + 알림 (이중 가드)
- 신규 헬퍼 `resolve_trading_mode() -> str`:
  - `default_mode == "real"` **AND** `os.environ.get("PRISM_LIVE_TRADING") == "1"` → `"real"`
  - 그 외 전부 → `"demo"` (안전 폴백). 즉 config만으로는 실전이 되지 않는다.
- 위치: 공통 모듈(예: `trading/trading_mode.py`)에 두고 US는 기존 `_import_from_main_cores()` 패턴으로 재사용.
- 운영 호출부 4곳(KR 매수/매도, US 매수/매도)을 `AsyncTradingContext(mode=resolve_trading_mode())` 로 교체.
- 실전 모드로 기동 시: **로그 경고(⚠️) + 텔레그램 알림** "실전 매매 모드 활성화" 1회 전송(세션 시작 시).

#### 4.2.3 일일 매수 한도
- config `max_daily_buys: 3` (시장당, 추후 조정).
- tracking agent 매수 결정 직전: `trading_history`에서 `당일 AND account_mode=현재모드` 매수 건수 카운트
  → `>= max_daily_buys`면 매수 스킵 + 로그(+선택적 텔레그램 1회 알림).
- KR/US 각각 독립 카운트.

#### 4.2.4 비상정지 킬스위치
- 파일 기반: `trading/EMERGENCY_STOP` 존재 시 발동.
- 체크 지점: `_execute_buy_stock` / `_execute_sell_stock` **실행 직전**(KR/US 양쪽). 존재 시 즉시 중단(매매 안 함)
  + 텔레그램 알림 + 명확한 로그.
- 공통 헬퍼 `is_emergency_stopped() -> bool`.
- 운용: `touch trading/EMERGENCY_STOP` 로 장중 즉시 발동, 파일 삭제로 해제. 코드 재시작 불필요.

### 4.3 실전 매매 실행 조건 (종합 게이트)

```
실매매 실행 = (config.default_mode == "real")
            AND (env PRISM_LIVE_TRADING == "1")
            AND (trading/EMERGENCY_STOP 파일 없음)
            AND (당일 실전 매수 건수 < max_daily_buys)
```
하나라도 불충족 시 demo로 폴백하거나 해당 매수를 스킵한다.

## 5. 파일별 변경 요약

### KR
| 파일 | 변경 |
|------|------|
| `trading/config/kis_devlp.yaml` | `buy_sizing_mode`, `max_daily_buys` 추가; (Phase 3) 실전 자격증명 + `default_mode: real` |
| `trading/domestic_stock_trading.py` | `calculate_slot_even_amount()` 추가; `_execute_buy/sell` 킬스위치 체크 |
| `trading/trading_mode.py` (신규) | `resolve_trading_mode()`, `is_emergency_stopped()` 공통 헬퍼 |
| `tracking/db_schema.py` | `account_mode` 컬럼 + 마이그레이션 |
| `stock_tracking_agent.py` | 매수 호출부에서 슬롯 균등 금액 산정 + mode 주입; 일일 한도 체크; 보유/슬롯 조회 mode 필터; 실전 기동 알림 |

### US
| 파일 | 변경 |
|------|------|
| `prism-us/trading/us_stock_trading.py` | `calculate_slot_even_amount()` 추가; `_execute_buy/sell` 킬스위치 체크 |
| `prism-us/tracking/db_schema.py` | `account_mode` 컬럼 + 마이그레이션 |
| `prism-us/us_stock_tracking_agent.py` | 매수 호출부 슬롯 균등 + mode 주입; 일일 한도; 보유/슬롯 mode 필터; 실전 기동 알림 |
| 공통 헬퍼 | `resolve_trading_mode()`/`is_emergency_stopped()` 를 `_import_from_main_cores()` 패턴으로 재사용 |

## 6. 작업·검증 순서

### Phase 1 — 코드 + 모의 검증 (지금 진행)
1. 공통 헬퍼(`resolve_trading_mode`, `is_emergency_stopped`) 추가.
2. DB `account_mode` 컬럼 + 마이그레이션 (KR/US).
3. 슬롯 균등 매수(`calculate_slot_even_amount`) + 호출부 연결 (KR/US).
4. 일일 한도 + 킬스위치 체크 (KR/US).
5. 실전 기동 알림.
6. **모의 모드로 회귀 검증**: 새 코드가 모의 잔고로 슬롯 균등 매수, mode 필터, 한도, 킬스위치 모두 정상 작동하는지.

### Phase 2 — 실전 발급 (사용자 액션)
7. 한국투자증권 실전투자 API 발급 — **국내주식 + 해외주식 각각** (KIS Developers).
8. `kis_devlp.yaml`에 `my_app`/`my_sec`/`my_acct_stock` 입력.
9. 기존 토큰 캐시(`trading/config/KIS*`) 삭제 → 실전 토큰 신규 발급 유도.

### Phase 3 — 실전 전환
10. `default_mode: real` + `PRISM_LIVE_TRADING=1` 설정.
11. 소액(슬롯 균등 + 일일 3종목 한도)으로 실전 검증, 킬스위치 동작 확인.
12. 모니터링 후 한도/비율 조정.

## 7. 사용자 액션 — 실전 API 발급 (개요)
- KIS Developers(<https://apiportal.koreainvestment.com>)에서 **실전투자** 앱 등록 → App Key/Secret 발급.
- 국내주식과 **해외주식 권한**을 별도로 신청/활성화해야 할 수 있음.
- 실전 **계좌 개설** 및 모의→실전 전환 약관 동의 필요.
- 상세 단계는 Phase 1 완료 후 별도 안내.

## 8. 검증 계획
- 단위: `calculate_slot_even_amount` 경계값(잔여슬롯 0/1, 가용현금 부족, 1주 미만 스킵).
- 마이그레이션 멱등성: 재실행 시 컬럼 중복 추가 에러 없는지.
- mode 필터: demo/real 행 혼재 시 각 모드에서 올바른 부분집합만 조회되는지.
- 일일 한도: 4번째 매수 시도 스킵 확인.
- 킬스위치: 파일 생성 시 매수/매도 즉시 차단 확인.
- 가드: `PRISM_LIVE_TRADING` 미설정 시 real config여도 demo로 폴백 확인.
- 회귀: 기존 demo 자동매매 파이프라인이 깨지지 않는지(`--no-telegram` 로컬 실행).

## 9. 미해결 / 추후 고려
- `watchlist_history`, `analysis_performance_tracker` 등 분석 추적 테이블은 mode 무관으로 보고 1차 범위에서 제외(추후 필요 시 분리).
- 실전 손절/익절 트리거 기준(`TRIGGER_CRITERIA`)은 기존 값 유지 — 실전 데이터 축적 후 재검토.
- 종목당 금액이 가용현금 대비 과소(예: 잔여슬롯이 커서 단주도 못 사는 고가주)일 때의 처리 정책 — 1차는 스킵.
- FX: US는 계좌의 USD 현금 기준이라 환전 로직 불필요(확인됨).
