# Team B 최종 요청 — Automation V3 경계 확인

이번 V3 변경으로 Team B 산출물 계약은 바뀌지 않는다. 기존 preview, LSTM, rule baseline,
preprocessing, backtest를 유지하면서 이미 요청된 production 산출물을 완성하면 된다.

## 그대로 제출할 것

- 당일 accepted daily shard에 대한 exact-31 Rule+LSTM 신호
- exact-10 artifact와 `p1-return-engine-manifest.v2.json`
- `model.safetensors`, 종목별 train-only scaler, fixed ABI, round-trip 35bps
- 동일 input·commit·lock·image 두 실행의 byte identity
- metric·split·scaler·비용 독립 재계산과 leakage 0
- 모델 품질이 기준 미달이면 `modelQuality=BELOW_BASELINE`을 그대로 공개

## V3에서도 추가하지 않을 것

- 뉴스, citation, evidence span, ATR, peak, trailing stop 필드
- 자본·수량·LIMIT 가격·손절·익절·보유 만기 계산
- RiskDecision, account, order, intent hash, provider 호출
- 후보 집합 밖 종목이나 동시 보유 5개 상한 변경

AI는 Team B가 만든 후보 집합을 늘리지 않는다. Decision Platform이 선택 전에 후보 전체를
screening하고, 검증된 근거가 있을 때만 별도 JUDGE를 수행한다. AI OFF이면 기존 결정론 경로를
사용한다. AI ON 상태에서 Google/provider 전체가 실패하면 그 run의 신규 매수는 0이다. 이 차이는
Decision Platform의 실행 정책이며 Team B 신호와 exact-10 bytes를 바꾸지 않는다.

## 매일 인계

학습 산출물은 한 번 만들고, 각 XKRX 거래일에는 고정된 모델과 scaler로 daily inference만 다시
실행한다. `sessionDate`가 당일 거래 세션과 다르면 자동운용이 신호를 사용하지 않으므로 exact-31
daily bundle과 manifest의 날짜·hash를 함께 확인한다.

## 완료 제출물

- PR URL과 commit SHA
- input manifest SHA-256과 output manifest SHA-256
- exact-10 hash 표와 두 실행 비교 결과
- unit·golden·independent metric 결과
- daily inference 재실행 명령과 sample manifest

Team B의 provider·KIS·계좌·주문·Vertex·GDELT 호출은 0으로 유지한다.
