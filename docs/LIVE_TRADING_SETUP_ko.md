# 실전 매매 전환 가이드

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
