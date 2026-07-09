# S1.2 OpenDART Disclosure Risk Contract

세션: S1.2 (OpenDART 공시 위험 점수)

## 변경 이유

`disclosure_risk_score`는 Python OpenDART client에서 계산되지만, 판단 결과 계약(`risk_decision.schema.json`)에는 그 근거를 담을 곳이 없었다. Principle 계약에는 이미 `disclosure_risk_guard`/`disclosure_risk_score`(입력 축)가 있으나, 결과 계약이 이를 재현 가능하게 노출하지 못해 "점수는 계산되지만 어디에도 드러나지 않는" 상태였다. 이를 해소하기 위해 결과 계약에 범용 `riskItems[]`를 추가한다.

## 변경 내용

- `contracts/schemas/risk_decision.schema.json`
  - 최상위에 optional `riskItems` 배열 추가.
  - `$defs.riskItem` 추가: `metric`, `value`, `severity`, `source`(enum: OPENDART/KIS/NEWS/MACRO/INTERNAL), `eventCodes`, `mappingVersion`, `sourceRefs`.
  - OpenDART 전용 거대 객체를 박지 않고, 다른 원천(뉴스/거시 등)도 같은 형태로 표현할 수 있는 범용 구조로 설계했다.
  - required 4개: `metric`, `value`, `severity`, `source`. 나머지는 optional.
- `contracts/examples/risk_decision.valid.json`: `disclosure_risk_score` 기반 `riskItems` 예시 1건 추가.
- invalid 예시(`contracts/examples/invalid/risk_decision.invalid.json`)는 기존 `decision` enum 위반으로 그대로 무효 판정된다(추가 변경 없음).

## 영향 범위

- Decision Platform: RiskEngine 판단 결과에 disclosure 근거를 재현 가능하게 노출. 실제 Spring 소비 구현은 Decision/Risk 컨트롤러 세션 과제.
- Return Engine / Experience Dashboard: `riskItems`는 optional additive 필드라 기존 소비 코드에 영향 없음. 대시보드는 필요 시 `riskItems[].metric`으로 공시 위험 배지를 표시할 수 있다.
- CI: `uv run python contracts/validate.py`가 valid/invalid 예시를 계속 검증한다.

## 호환성 상태

Optional additive 변경이므로 breaking change 없음. `riskItems`가 없는 기존 payload도 그대로 유효하다.

## 서비스 경계 문서 계약

`docs/API_명세서.md` 13.5.1에 `MarketDataService.GetDisclosureEvents`의 request/response 문서 계약을 추가했다. 실제 gRPC proto 파일은 아직 없으며, 추가 시 `contracts/proto/`에 넣고 이 절차를 다시 따른다.

## 근거 문서

- 점수 원천·등급·의도적 제외: `docs/decision-platform/S1_2_OpenDART_공시위험점수_근거.md`
- Principle 입력 축: `contracts/schemas/principle.schema.json` (`disclosure_risk_guard`)
