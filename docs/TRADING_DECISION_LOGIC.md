# PRISM-INSIGHT 매수/매도 판단 로직 분석

> **작성일**: 2026-02-21

---

## 목차

1. [실행 스케줄](#1-실행-스케줄)
2. [전체 파이프라인](#2-전체-파이프라인)
3. [Stage 1: Trigger — 종목 선별](#3-stage-1-trigger--종목-선별)
4. [Stage 2: Report Generation — AI 리포트 생성](#4-stage-2-report-generation--ai-리포트-생성)
5. [Stage 3: Trading Scenario Agent — AI 매수 판단](#5-stage-3-trading-scenario-agent--ai-매수-판단)
6. [Stage 4: Portfolio Constraint — 포트폴리오 제약](#6-stage-4-portfolio-constraint--포트폴리오-제약)
7. [Stage 5: Trade Execution — 주문 실행](#7-stage-5-trade-execution--주문-실행)
8. [Stage 6: Sell Decision Agent — AI 매도 판단](#8-stage-6-sell-decision-agent--ai-매도-판단)
9. [Stage 7: Trading Journal — 사후 학습](#9-stage-7-trading-journal--사후-학습)
10. [KR vs US 상세 비교](#10-kr-vs-us-상세-비교)
11. [O'Neil 페르소나의 판단 영향 분석](#11-oneil-페르소나의-판단-영향-분석)

---

## 1. 실행 스케줄

Docker crontab (`docker/crontab`) 기준, 평일만 실행됩니다.

### KR (한국 주식)

| 시간 (KST) | 모드 | 설명 |
|------------|------|------|
| 07:00 | - | 종목 데이터 업데이트 (`update_stock_data.py`) |
| 09:30 | `morning` | 장 시작 후 30분 — 급등주 감지 + 분석 + 매수 |
| 15:40 | `afternoon` | 장 마감 후 — 마감 강도/횡보주 분석 |
| 17:00 | - | 성과 추적 (`performance_tracker_batch.py`) |

### US (미국 주식)

| 시간 (KST) | EST | 모드 | 설명 |
|------------|-----|------|------|
| 00:15 | 10:15 | `morning` | 장 시작 후 분석 |
| 02:30 | 12:30 | `midday` | 중간 체크 (KR에는 없음) |
| 06:30 | 16:30 | `afternoon` | 장 마감 후 분석 |
| 07:30 | 17:30 | - | 성과 추적 |

매 실행마다 **보유종목 매도 판단 → 신규종목 매수 판단** 순서로 동작합니다.

---

## 2. 전체 파이프라인

```
[Stage 1] Trigger (규칙 기반, AI 아님)
    → 전 종목 스캔 → 거래량/갭/자금유입 등 정량 필터 → 최대 3종목 선정

[Stage 2] Report Generation (AI 6개 에이전트)
    → 선정된 종목별 6개 분석 리포트 생성

[Stage 3] Trading Scenario Agent (AI 매수 판단)
    → 리포트 + 포트폴리오 상태 + 과거 매매 교훈 → 매수 점수(1-10) + 진입/미진입 결정

[Stage 4] Portfolio Constraint Check (규칙 기반)
    → 최대 10종목, 동일 섹터 3종목 제한 확인

[Stage 5] Trade Execution
    → KIS API로 실제 주문 (demo/real 모드)

[Stage 6] Sell Decision Agent (AI 매도 판단)
    → 매 실행 시 보유종목에 대해 매도 여부 판단

[Stage 7] Trading Journal Agent (AI 사후 학습)
    → 매매 종료 후 복기 → 다음 판단에 교훈 반영
```

### 흐름도

```
trigger_batch.py run_batch()
  → 3 or 6 quantitative trigger functions
  → select_final_tickers() — hybrid scoring (composite 30% + agent_fit 70%)
  → Max 3 stocks selected

orchestrator.generate_reports()
  → cores.analysis.analyze_stock() per stock
  → 6 sequential AI agents → comprehensive markdown report

stock_tracking_enhanced_agent.process_reports()
  → update_holdings() [SELL PATH]
      → _analyze_sell_decision() — gpt-5.2 sell_decision_agent
      → should_sell=True → execute sell via KIS API
      → should_sell=False → update trailing stop if new high
  → For each new report [BUY PATH]
      → analyze_report()
          → get current price
          → _extract_trading_scenario() — gpt-5.2 trading_scenario_agent
              → buy_score (1-10) + decision + target_price + stop_loss
      → Check: buy_score >= min_score AND sector_diverse AND decision=="Enter"
      → If yes: buy_stock() → async_buy_stock() via KIS API
      → If no: save to watchlist_history + Telegram skip message

Post-trade (optional, ENABLE_TRADING_JOURNAL=true):
  → trading_journal_agent: retrospective analysis
  → context_retriever_agent: fetch lessons for next buy
```

---

## 3. Stage 1: Trigger — 종목 선별

**파일:** `trigger_batch.py` (KR), `prism-us/us_trigger_batch.py` (US)

AI를 사용하지 않는 순수 정량 필터입니다.

### Morning 트리거 (장 시작 직후)

| 트리거 | KR 이름 | 핵심 조건 |
|--------|---------|----------|
| 거래량 급증 | `거래량 급증 상위주` | 거래량 30%↑, 양봉, 시총 5000억+ |
| 갭 상승 모멘텀 | `갭 상승 모멘텀 상위주` | 갭 1%+, 변동률 15% 이내 |
| 자금 유입 | `시총 대비 집중 자금 유입 상위주` | 거래대금/시총 비율 상위, 시총 5000억+ |

### Afternoon 트리거 (장 마감 후)

| 트리거 | KR 이름 | 핵심 조건 |
|--------|---------|----------|
| 일중 상승률 | `일중 상승률 상위주` | 전일 대비 3~15% 상승 |
| 마감 강도 | `마감 강도 상위주` | 마감 무렵 강세, 거래량 증가 |
| 횡보 거래량 증가 | `거래량 증가 상위 횡보주` | 변동 ±5% 이내, 거래량 50%↑ |

### 최종 선별

- 복합 점수 = `composite_score × 0.3 + agent_fit_score × 0.7`
- 트리거당 1개, **최대 3종목** 선정
- 공통 필터: 시총 5000억+, 거래대금 100억+, 변동률 20% 이내

### Agent Fit Score 산정

- 손절가: 트리거별 -5% 또는 -7% (고정)
- 목표가: 10일 저항선 기준, 최소 +15% 보장
- Risk/Reward 비율: `(목표가 - 현재가) / (현재가 - 손절가)`
- 점수: `rr_score × 0.6 + sl_score × 0.4`

### 트리거별 R/R 및 손절 기준

| 트리거 | R/R 목표 | 손절 |
|--------|---------|------|
| 거래량 급증 | 1.2+ | -5% |
| 갭 상승 모멘텀 | 1.2+ | -5% |
| 일중 상승률 | 1.2+ | -5% |
| 마감 강도 | 1.3+ | -5% |
| 자금 유입 | 1.3+ | -5% |
| 횡보 거래량 증가 | 1.5+ | -7% |
| 기본값 | 1.5+ | -7% |

---

## 4. Stage 2: Report Generation — AI 리포트 생성

**파일:** `cores/analysis.py` (KR), `prism-us/cores/us_analysis.py` (US)
**모델:** GPT-5.2 (OpenAI)

선정된 종목마다 6개 에이전트가 **순차적**으로 분석합니다.

| # | 에이전트 | 분석 내용 |
|---|---------|----------|
| 1 | `price_volume_analysis` | 가격/거래량 기술적 분석 |
| 2 | `investor_trading_analysis` | 기관/외국인 수급 (KR: 일별, US: 분기별) |
| 3 | `company_status` | 재무제표, PER/PBR, ROE/ROA, 부채비율 |
| 4 | `company_overview` | 사업구조, 경쟁력, R&D |
| 5 | `news_analysis` | 뉴스/카탈리스트 (Perplexity) |
| 6 | `market_index_analysis` | 시장 지수 상황 |

6개 리포트가 합쳐져서 다음 단계(Trading Scenario Agent)의 입력이 됩니다.

---

## 5. Stage 3: Trading Scenario Agent — AI 매수 판단

**파일:** `cores/agents/trading_agents.py` → `create_trading_scenario_agent()`
**모델:** GPT-5.2
**페르소나:** William O'Neil (CAN SLIM 창시자)

### 입력 데이터

- 6개 에이전트의 종합 리포트
- 현재 포트폴리오 상태 (보유 종목, 섹터 분포, 투자기간 분포)
- 트리거 타입 & 모드
- 과거 유사 매매 교훈 (Trading Journal에서 검색)
- 점수 조정 제안 (`score_adjustment` -1 ~ +1)

### 매수 점수 체계 (1-10)

| 점수 | 의미 |
|------|------|
| 8-10 | 적극 진입 (저평가 + 강한 모멘텀) |
| 7 | 진입 (기본 조건 충족) |
| 6 | 조건부 진입 (강세장 + 모멘텀 확인 시) |
| ≤5 | 미진입 (명확한 부정 요소) |

### min_score (시장 상황 적응형)

| 시장 상황 | KR | US |
|----------|----|----|
| 상승장 | 6 | 5 |
| 횡보/하락장 | 7 | 6 |

### 상승장 판단 기준

- **KR**: KOSPI가 20일 이평선 위 + 최근 2주 5%↑
- **US**: S&P 500이 20일 이평선 위 + (4주 +2% 이상 OR 2주 +3% 이상)

### Score-Decision 강제 보정 (US만 해당)

```python
# AI가 점수는 높게 줬는데 "미진입"으로 결정 시 → 강제 "진입"
if adjusted_score >= min_score and normalized_decision != "entry":
    normalized_decision = "entry"
```

### Risk/Reward 산출

```
기대수익률(%) = (목표가 - 현재가) / 현재가 × 100
기대손실률(%) = (현재가 - 손절가) / 현재가 × 100
Risk/Reward = 기대수익률 / 기대손실률
```

### 독립적 미진입이 허용되는 경우 (2가지만)

1. 손절 지지선이 -10% 이상 깊은 경우 (유효한 손절가 설정 불가)
2. PER이 업종 평균의 2배 이상 (극단적 고평가)

### 에이전트 반환값

```json
{
    "buy_score": 7,
    "min_score": 6,
    "decision": "진입" / "미진입",
    "target_price": 85000,
    "stop_loss": 78000,
    "risk_reward_ratio": 2.3,
    "expected_return_pct": 15.0,
    "expected_loss_pct": 6.5,
    "investment_period": "단기/중기/장기",
    "sector": "반도체",
    "market_condition": "상승추세",
    "max_portfolio_size": 8,
    "trading_scenarios": {
        "key_levels": {},
        "sell_triggers": [],
        "hold_conditions": []
    }
}
```

---

## 6. Stage 4: Portfolio Constraint — 포트폴리오 제약

**파일:** `stock_tracking_enhanced_agent.py`

```python
MAX_SLOTS = 10              # 최대 보유 종목 수
MAX_SAME_SECTOR = 3         # 동일 섹터 최대 3종목
SECTOR_CONCENTRATION = 0.3  # 한 섹터 30% 이하
```

### 판단 흐름

```
이미 보유 중? → Skip
↓ No
buy_score >= min_score AND sector_diverse AND decision == "Enter"?
↓ Yes                            ↓ No
매수 실행                         watchlist 저장 + 텔레그램 "보류" 메시지
```

### 동적 손절/목표 (AI가 0을 반환한 경우)

- 기본 손절: 5%, 종목 변동성에 따라 조정 (`stock_volatility / 15% × 5%`), 3~15% 범위
- 하락장: ×0.8 (타이트), 상승장: ×1.2 (넓게)
- 기본 목표: 10%, 유사하게 조정, 5~30% 범위

---

## 7. Stage 5: Trade Execution — 주문 실행

**파일:** `trading/domestic_stock_trading.py` (KR), `trading/us_stock_trading.py` (US)

```python
# KR
async with AsyncTradingContext() as trading:
    result = await trading.async_buy_stock(stock_code=ticker, limit_price=current_price)

# US (예약주문 필수 — 시차 때문)
async with AsyncUSTradingContext() as trading:
    result = await trading.async_buy_stock(ticker=ticker, limit_price=current_price)
```

- 모드: `demo` (기본, 시뮬레이션) 또는 `real`
- KIS API TR ID: `TTTC0012U` (실전 매수), `VTTC0012U` (모의 매수)
- 수량: `floor(기본투자금액 / 현재가)`

---

## 8. Stage 6: Sell Decision Agent — AI 매도 판단

**파일:** `cores/agents/trading_agents.py` → `create_sell_decision_agent()`
**모델:** GPT-5.2
**실행 시점:** 매 파이프라인 실행 시 `update_holdings()`에서 보유종목 전체 체크

### 매도 우선순위

#### Priority 1: 손절 (비협상)

- 손실 ≥ **-7.1%**: **무조건 매도**, 예외 없음
- 유일한 예외 (-5% ~ -7% 구간, 아래 조건 모두 충족 시 1일 유예):
  - 당일 반등 +3% 이상
  - 거래량 20일 평균의 2배 이상
  - 기관/외인 순매수 (KR만)

#### Priority 2: 익절

- **상승장 (추세 추종 모드)**: 목표가는 최소선, 추세 유지 시 계속 보유
  - Trailing stop: 고점 대비 -8% (`peak × 0.92`)
- **횡보/하락장 (수비 모드)**: 목표가 도달 시 매도
  - Trailing stop: 고점 대비 -3~5% (`peak × 0.95~0.97`)
  - 최대 7거래일 관찰

#### Priority 3: 시간 기반

| 조건 | 판단 |
|------|------|
| 단기 (~1개월) | 목표가 도달 시 매도 |
| 중기 (1~3개월) | 시장 상황에 따라 상승장/횡보장 모드 적용 |
| 장기 (3개월+) | 펀더멘탈 변화 확인 |
| 30일+ 보유 + 손실 + 약세 | 매도 |
| 60일+ 보유 + 수익 3%↑ + 약세 | 매도 |

### Trailing Stop 자동 업데이트

매 실행 시 현재가가 이전 고점을 돌파하면 DB의 stop_loss를 갱신:
- 상승장: `new_high × 0.92`
- 횡보/하락장: `new_high × 0.95`

### AI 파싱 실패 시 Fallback (규칙 기반)

1. 현재가 ≤ stop_loss → 매도 (강한 상승추세 + 손실 7% 미만 아닌 경우)
2. 현재가 ≥ target_price → 매도 (강한 상승추세 아닌 경우)
3. 하락장 + 하락추세 + 수익 3%↑ → 매도
4. 단기 15일+ 보유 + 수익 5%↑ + 강한 상승추세 아님 → 매도
5. 수익 10%↑ + 강한 상승추세 아님 → 매도
6. 30일+ 보유 + 손실 + 상승추세 아님 → 매도

### 에이전트 반환값

```json
{
    "should_sell": true,
    "sell_reason": "상세 사유",
    "confidence": 7,
    "analysis_summary": {
        "technical_trend": "하락 강세",
        "volume_analysis": "거래량 감소",
        "market_condition_impact": "약세장",
        "time_factor": "28일 보유"
    },
    "portfolio_adjustment": {
        "needed": false,
        "new_target_price": null,
        "new_stop_loss": null,
        "urgency": "low"
    }
}
```

---

## 9. Stage 7: Trading Journal — 사후 학습

**파일:** `cores/agents/trading_journal_agent.py`

매매 종료 후 복기하는 에이전트입니다.

### 분석 항목

- 매수 판단 적절성 평가
- 매도 타이밍 적절성 평가
- 놓친/과잉반응 신호 분석
- 패턴 태그 추출 (`급등후조정`, `박스권돌파`, `손절지연` 등)
- 교훈의 우선순위 태그 (`high` / `medium` / `low`)

### 피드백 루프

다음 매수 판단 시 `context_retriever_agent`가 유사 패턴의 과거 교훈을 검색하여 `score_adjustment` (-1 ~ +1)로 Trading Scenario Agent에 반영합니다.

---

## 10. KR vs US 상세 비교

### 10-1. 종목 선별 (Trigger)

| 항목 | KR | US |
|------|----|----|
| 종목 풀 | KOSPI/KOSDAQ 전체 (~2,500종목) | S&P 500 + NASDAQ-100만 (~550종목) |
| 시총 필터 | 5,000억 KRW (~$370M) | $20B (~54배 차이) |
| 거래대금 필터 | 100억 KRW (~$7.4M) | $100M (~13배 차이) |
| 트리거 타입 | 6개 (한국어 키) | 6개 동일 (영어 키) |
| R/R·손절 기준 | 동일 | 동일 |
| midday 모드 | 없음 | 있음 (12:30 EST 추가 실행) |
| 시총 데이터 | pykrx에서 실시간 조회 | None (이미 대형주 풀) |

### 10-2. 리포트 생성 에이전트

| 에이전트 | KR | US |
|---------|----|----|
| 가격/거래량 | pykrx OHLCV | yfinance OHLCV |
| 수급 분석 | **일별 기관/외인 매매동향** (pykrx) | **분기별 기관 보유현황** (yfinance) |
| 기업 재무 | WiseReport 크롤링 | yfinance financials |
| 기업 개요 | WiseReport 크롤링 | yfinance info |
| 뉴스 | Perplexity | Perplexity |
| 시장지수 | KOSPI/KOSDAQ (pykrx) | S&P 500/NASDAQ/Dow (yfinance) |
| 실행 방식 | 순차 | 하이브리드 (yfinance 순차 + Perplexity 병렬) |

### 10-3. 매수 판단 (Trading Scenario Agent)

| 항목 | KR | US |
|------|----|----|
| 상승장 판단 | KOSPI 20일선 위 + 2주 +5% | S&P500 20일선 위 + (4주 +2% OR 2주 +3%) |
| min_score (상승장) | 6 | **5** (1점 낮음) |
| min_score (횡보/하락장) | 7 | **6** (1점 낮음) |
| 6점 정의 | "조건부 진입" (강세장+모멘텀 필요) | "신중 진입" (모멘텀+관리가능 리스크, **강세장 불요**) |
| 하락장 진입 태도 | "보수적 유지" | **"선별적이되 적극적"** |
| 하락장 최소 진입 | 7점 + 강한 모멘텀 + 저평가 | **6점 + 모멘텀 + R/R 2.0+** |
| 강한 모멘텀 조건 | 4개 (외인·기관 순매수 포함) | **3개** (외인·기관 제거) |
| 모멘텀 보너스 | 기관/외국인 순매수 포함 | 제거, **어닝 서프라이즈** 추가 |
| 미진입 판단 | RSI 85+ AND 외인·기관 순매도 전환 | RSI 85+ AND **거래량 감소/갭 하락** |
| Score-Decision Override | 없음 | **있음** (점수 충족 시 강제 진입) |
| Decision State | 3-state (Enter/Watch/Skip) | **2-state** (entry/no_entry) |
| MCP 서버 | sqlite, perplexity, time | **yahoo_finance**, sqlite, perplexity, time |

### 10-4. 매도 판단

| 항목 | KR | US |
|------|----|----|
| 상승장 판단 조건 | 4개 (외인·기관 순매수 포함) | 3개 (외인·기관 제거) |
| 손절 예외 조건 | 5개 (기관/외인 순매수 포함) | 4개 (기관/외인 제거) |
| 주요 지지선 기준 | 20일선 | **50일선** (US 관행) |

### 10-5. 기타

| 항목 | KR | US |
|------|----|----|
| Watchlist 추적 | 미진입 종목 저장 안 함 | 저장 (7/14/30일 성과 추적) |
| 주문 방식 | 지정가 | 지정가 (예약주문 필수, 시차) |
| 통화 | KRW | USD |
| 시장 시간 | 09:00-15:30 KST | 09:30-16:00 EST |
| DB 테이블 | `stock_holdings`, `trading_history` | `us_stock_holdings`, `us_trading_history` |

### 10-6. US가 KR보다 관대한 이유

1. **min_score 1점 하향**: v2.4.1에서 US 0% 진입률 버그 해결을 위해 조정
2. **기관/수급 신호 전면 제거**: US 기관 데이터는 분기별이라 일별 매매 신호로 사용 불가. Perplexity 기반 13F/Form 4 검색도 신뢰도 부족 → 모든 신호를 yfinance 가격/거래량 기반(CAN SLIM)으로 통일 (v2.4.6)
3. **Score-Decision Override**: AI의 비일관적 판단 보정 장치 추가
4. **종목 풀 자체가 대형 우량주**: S&P 500 + NASDAQ-100으로 이미 필터링되어 종목 리스크가 상대적으로 낮음

---

## 11. O'Neil 페르소나의 판단 영향 분석

매수/매도 에이전트 모두 **William O'Neil (CAN SLIM 창시자)** 페르소나로 설정되어 있습니다.
이는 단순한 역할극이 아니라 매수/매도 로직 전체의 설계 원칙으로 작동합니다.

### 11-1. 핵심 철학 주입

프롬프트에 명시된 문장:

```
# 매수 에이전트
"손실은 7-8%에서 짧게 자르고, 수익은 길게 가져가라"

# 매도 에이전트
"손실은 7-8%에서 자른다, 예외 없다" (iron rule)
```

이 한 줄이 손절 체계, 익절 전략, 진입 기준 등 모든 규칙의 근거가 됩니다.

### 11-2. "손실은 짧게" → 손절 체계 전체를 지배

O'Neil의 실제 투자 원칙은 **"매수가 대비 -7~8%에서 무조건 손절"**입니다.

| 규칙 | 프롬프트 반영 | O'Neil 원칙 |
|------|-------------|-------------|
| 절대 손절선 | 손실 ≥ -7.1% → **무조건 매도, 예외 없음** | 7-8% rule 그대로 |
| 강세장 손절 | R/R ≥ 1.5 → -7%, R/R < 1.5 → **-5% (더 타이트)** | 손익비 낮으면 더 빨리 손절 |
| 약세장 손절 | 모든 트리거 -7% 이내 | 자본 보존 우선 |
| 유일한 예외 | -5%~-7% 구간에서 당일 반등 +3% + 거래량 2배 + 기관 순매수 → **1일만** 유예 | 예외적 상황 인정하되 극히 제한적 |

페르소나가 없을 경우 LLM은 "좀 더 기다려보자", "반등할 수 있다" 같은 판단을 쉽게 내릴 수 있습니다. O'Neil 페르소나는 **LLM의 낙관적 편향을 억제**하는 앵커 역할을 합니다.

### 11-3. "수익은 길게" → Trailing Stop + 추세 추종

O'Neil은 "큰 수익을 내는 종목을 너무 일찍 파는 것이 가장 흔한 실수"라고 했습니다. 매도 에이전트에 직접 반영:

```
Bull Market Mode → Trend Priority (Maximize Profit)
- 목표가는 최소 기준선, 추세가 살아있으면 계속 보유
- Trailing Stop: 고점 대비 -8% (노이즈 무시)
- 명확한 추세 약화 시에만 매도
```

페르소나가 없다면 LLM은 "목표가 도달 = 매도"라는 단순 로직을 택할 가능성이 높습니다. 대신 **"목표가는 최소선이고, 추세가 살아있으면 계속 가져간다"**는 판단을 유도합니다.

### 11-4. CAN SLIM → 매수 판단 기준 체계

O'Neil의 CAN SLIM 7가지 원칙이 프롬프트의 체크리스트와 점수 체계에 녹아있습니다:

| CAN SLIM | 의미 | 프롬프트 반영 |
|----------|------|-------------|
| **C** - Current Earnings | 최근 실적 | 기업 현황 분석 (ROE/ROA, 영업이익률) |
| **A** - Annual Earnings | 연간 실적 성장 | 기업 현황 분석 (실적 추이) |
| **N** - New Product/High | 신제품, 신고가 | 뉴스 분석 + "52주 고가 대비 95% 이상" 모멘텀 조건 |
| **S** - Supply/Demand | 수요공급 | "거래량 20일 평균 200% 이상", 기관/외인 순매수 |
| **L** - Leader or Laggard | 업종 내 리더 | "동종업계 대비 저평가" 체크, Perplexity로 밸류에이션 비교 |
| **I** - Institutional | 기관 보유 | "외국인/기관 3일 연속 순매수" (KR), 분기 보유현황 (US) |
| **M** - Market Direction | 시장 방향 | **0단계: 시장 환경 판단** (강세장/약세장 먼저 확인) |

특히 **M (Market Direction)**이 프롬프트에서 "0단계"로 **가장 먼저** 판단하도록 위치해 있습니다. O'Neil의 "시장이 하락장이면 아무리 좋은 종목도 사지 마라"는 원칙을 반영한 것이며, 시장 환경에 따라 min_score 자체가 달라지는 것도 이 때문입니다.

### 11-5. "다음 기회가 없다" → 강세장 진입 편향 유도

프롬프트에서 가장 O'Neil스러운 부분:

```
강세장 판단 원칙:
- 이 시스템은 "다음 기회" 없음 → 미진입 = 영구 포기
- 10% 오를 종목 미진입 = -10% 기회비용
- 판단 전환: "왜 사야 하나?" → "왜 사면 안 되나?" (부정 증명 요구)
- 명확한 부정 요소 없으면 → 진입이 기본
```

O'Neil의 **"강세장에서는 과감하게 매수하라"** 원칙의 구현입니다. LLM은 기본적으로 리스크를 나열하며 보수적 판단을 내리는 경향이 있는데, 이 프롬프트는 **입증 책임을 뒤집어서** "사지 않을 이유를 증명하라"고 요구합니다.

독립적 미진입은 2가지 경우만 허용됩니다:
1. 손절 지지선이 -10% 이상 깊은 경우 (유효한 손절가 설정 불가)
2. PER이 업종 평균의 2배 이상 (극단적 고평가)

그 외의 모호한 이유는 명시적으로 금지됩니다:

```
조건부 관망 금지:
- "21,600~21,800 지지 확인 반등 시 진입" ← 금지
- "92,700 돌파 후 2~3일 안착 확인이 선행돼야" ← 금지
```

### 11-6. 올인/올아웃 → 신중함 강제

```
이 시스템은 분할매매가 불가능합니다.
- 매수: 포트폴리오의 10% 비중(1슬롯)으로 100% 매수
- 매도: 1슬롯 보유분 100% 전량 매도
```

O'Neil은 확신 있는 종목에 집중 투자하는 스타일입니다. 분할매수/분할매도를 하지 않기 때문에 "진입이냐 미진입이냐"가 binary 결정이 되고, 이것이 "조건부 관망 금지" 규칙과 연결됩니다.

### 11-7. 가치투자 + 모멘텀 하이브리드

```
기본적으로는 가치투자 원칙을 따르되, 상승 모멘텀이 확인될 때는 보다 적극적으로 진입합니다.
```

O'Neil의 독특한 포지션입니다. 순수 가치투자자(버핏)도 아니고 순수 기술적 트레이더도 아닌, **"좋은 펀더멘탈 + 가격 브레이크아웃"**을 결합한 접근입니다. 프롬프트에서 Perplexity로 밸류에이션을 먼저 비교하고(가치), 거래량/추세를 체크하는(모멘텀) 순서가 이를 반영합니다.

### 11-8. O'Neil 페르소나 유무에 따른 판단 차이 예상

| 상황 | O'Neil 페르소나 있을 때 | 없을 때 예상되는 LLM 행동 |
|------|----------------------|------------------------|
| -6% 손실 종목 | "손절선 근접, 반등 없으면 내일 매도" | "기업 펀더멘탈은 좋으니 좀 더 기다려보자" |
| 강세장 + 점수 6점 | "명확한 부정 요소 없으면 진입" | "리스크 요소를 나열하며 보류 추천" |
| 목표가 도달 + 상승 추세 | "목표가는 최소선, 추세 유지 중이면 보유" | "목표 달성했으니 익절" |
| 지지 확인 전 종목 | "지금 진입 또는 미진입, 조건부 관망 금지" | "지지 확인 후 재진입 추천" |

**핵심 역할:** O'Neil 페르소나는 LLM의 고질적 문제인 **"리스크 나열 → 보수적 결론"** 패턴을 교정하고, **손절은 기계적으로, 매수는 강세장에서 적극적으로, 수익은 추세가 살아있는 한 가져가는** 일관된 의사결정 프레임워크를 제공합니다.

---

## 관련 파일 참조

| 파일 | 역할 |
|------|------|
| `docker/crontab` | 실행 스케줄 |
| `trigger_batch.py` | KR 트리거 |
| `prism-us/us_trigger_batch.py` | US 트리거 |
| `cores/analysis.py` | KR 리포트 생성 |
| `prism-us/cores/us_analysis.py` | US 리포트 생성 |
| `cores/agents/trading_agents.py` | KR 매수/매도 AI 에이전트 |
| `prism-us/cores/agents/trading_agents.py` | US 매수/매도 AI 에이전트 |
| `stock_tracking_agent.py` | KR 트래킹 (매수 실행 + 포트폴리오 관리) |
| `prism-us/us_stock_tracking_agent.py` | US 트래킹 |
| `stock_tracking_enhanced_agent.py` | KR 동적 손절/목표 |
| `trading/domestic_stock_trading.py` | KR KIS API 주문 |
| `trading/us_stock_trading.py` | US KIS API 주문 |
| `cores/agents/trading_journal_agent.py` | 매매 복기 + 피드백 루프 |
