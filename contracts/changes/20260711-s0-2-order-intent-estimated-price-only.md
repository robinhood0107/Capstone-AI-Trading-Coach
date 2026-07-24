# S0.2 Order Intent `estimatedPrice` 단일 계약

## 변경 이유

공개 명세와 P0 필드 결정은 주문 평가 가격의 canonical 필드를 `estimatedPrice` 하나로 확정했지만, JSON Schema에는 구현되지 않은 `price` 호환 alias가 남아 있었다. 런타임 controller가 생기기 전에 schema를 단일 계약으로 맞춰 모호한 payload 수용을 차단한다.

## 영향 범위

- `order_intent.schema.json`은 `estimatedPrice`를 필수로 요구하고 `price`를 unknown property로 거부한다.
- Decision Platform, Return Engine, Experience Dashboard의 새 payload·adapter·fixture는 `estimatedPrice`만 사용한다.
- `price`만 포함한 otherwise-valid negative fixture로 contracts CI 회귀를 고정한다.

## 호환성 상태

Schema 초안에 남아 있던 alias를 제거하는 breaking cleanup이다. 해당 alias를 소비하는 런타임 구현은 아직 없으므로 S0.2 계약 단계에서 교정하며, 외부 호환 shim이나 deprecated alias는 추가하지 않는다.

## S2.3 명확화

2026-07-24 S2.3 계약 잠금은 이 결정을 현물 v1 MARKET/LIMIT 모두에 적용한다. `limitPrice`도
현물 v1 alias로 허용하지 않으며 exact 8개 field와
`HASH-CANONICALIZATION-S22-V2`를 사용한다. P2 `derivativeOrderIntent.limitPrice`와 S3 provider
wire mapping은 별도 namespace다. 자세한 내용은
[`20260724-s2-3-decision-contract-lock.md`](20260724-s2-3-decision-contract-lock.md)를 따른다.
