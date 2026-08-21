# S6 무료 API strict PIT 가용성 판정

## 결론

2026-08-21의 bounded feasibility probe 결과, 무료 API에서 과거 가격·수급 값은 일부
조회할 수 있었지만 각 과거 행이 당시 실제로 공개·수신된 시각을 증명하는 metadata는
확보하지 못했다. 현재 조회 시각이나 정기 공개 일정을 과거 `availableAt`으로 소급하지
않는다.

따라서 현재 무료 자료는 `NON_PIT_HISTORICAL` 참고 자료로만 분류하며 S6.6의
`REAL_PIT`, 실제 `AVAILABLE + BUY` candidate, validation-only threshold freeze 증거로
사용하지 않는다. 그 입력을 전제로 하는 S6.6/S6.7 실행 capability는 별도 versioned
retirement 계약에 따라 현행 authority에서 제거한다.

S4.8의 offline 계약, fixture producer, deterministic scorer, append-only sanitized
evidence storage와 bounded reader는 이 판정과 독립적이므로 유지한다. S4.8은
`VERIFIED_OFFLINE_STORED`이고 Decision, Signal, RiskDecision, order authority는 없다.

## Sanitized probe receipt

| Provider | 물리 호출 | 결과 | strict PIT 판정 |
|---|---:|---|---|
| KIS | 10 | OAuth 1회와 해외·국내 역사 조회 9회 성공 | 과거 행별 실제 `availableAt` 미증명 |
| KRX | 1 | 과거 KOSPI 일별 값 조회 성공 | 과거 행별 실제 `availableAt` 미증명 |
| ECOS | 1 | 과거 원/달러 값 조회 성공 | 과거 행별 실제 `availableAt` 미증명 |
| 공공데이터 주식대차 | 1 | 현재 service key 권한으로 HTTP 403 | 자료 미확보 |
| 공공데이터 금융투자협회 통계 | 1 | 현재 service key 권한으로 HTTP 403 | 자료 미확보 |

- 총 물리 호출: 14
- retry: 0
- 비용: 0원
- live account 호출: 0
- order 호출: 0
- raw provider body/header 영속화: 0
- runtime provider 호출: 0

## Public source references

- KIS 해외주식 기간별시세:
  <https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/overseas_stock/inquire_daily_chartprice/inquire_daily_chartprice.py>
- KRX 정보데이터시스템 서비스 목록:
  <https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd>
- 한국은행 ECOS:
  <https://www.bok.or.kr/portal/submain/submain/sts.do?menuNo=200094&viewType=SUBMAIN>
- 예탁결제원 주식대차정보:
  <https://www.data.go.kr/data/15059612/openapi.do>
- 금융투자협회 종합통계정보:
  <https://www.data.go.kr/data/15094809/openapi.do>

## 재개 조건

퇴역 capability를 다시 설계하려면 무료 여부와 무관하게 다음을 모두 새 계약으로
승인해야 한다.

1. 최소 3년의 immutable 자료와 행별 historical `availableAt` 증명
2. revision과 source artifact checksum manifest
3. 실제 qualification을 통과한 `AVAILABLE + BUY + REAL_PIT` candidate
4. validation-only threshold freeze와 untouched test receipt

이 조건이 충족되기 전에는 synthetic fixture, 현재 재조회 값 또는 공개 일정 추론을
실제 성과 증거로 승격하지 않는다.
