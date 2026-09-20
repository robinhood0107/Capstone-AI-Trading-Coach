# 프리셋의 건당 주문 한도 — DB 이탈을 계약 v1 동결 아래에서 인정한다

작성일: 2026-09-08

## 무엇이 어긋났나

`V143__disable_preset_single_order_amount_limit.sql` 이 세 프리셋의
`max_single_order_amount` 를 `{"enabled":false,"severity":"ALLOW"}` 로 바꿨다. 파일 첫 줄이
성격을 적어 뒀다 — *"Owner-authorized removal of the per-order KRW ceiling for BUY and
SELL. Keep the historical rule tuple and the eight-rule API contract; disable enforcement."*
후속 `V144__reevaluate_unsubmitted_amount_limit_block.sql` 이 그 한도로 막혀 있던 미제출
주문을 다시 판정했다. 의도된 승인 변경이다.

계약 카탈로그 `contracts/catalogs/s2-1-principle-contract.v1.json` 은 세 프리셋 모두
`BLOCK`/`enabled:true` 를 그대로 들고 있었고, 거기서 생성되는
`contracts/examples/principle-presets.valid.json` 도 같았다.

## 어떻게 드러났나

Kotlin 테스트 셋이 붉었고, 셋 다 V143 이 들어온 커밋 `516af8fc` 이후로 계속 그랬다.
HEAD 에서 재현해 확인했다.

| 테스트 | 무엇을 기대했나 |
|---|---|
| `PrincipleContractMigrationIntegrationTest` · `V8 database preset seed is semantically identical to the generated catalog fixture` | 시드 == 생성 예시 |
| `DecisionApiIntegrationTest` · `complete stored source still BLOCKs an oversized order` | 700,000원 주문이 BLOCK |
| `DecisionApiIntegrationTest` · `violation insert failure rolls back the complete BLOCK graph` | 같은 주문이 BLOCK 되어 위반 삽입 실패 경로에 닿음 |

실측 차이는 정확히 세 프리셋의 같은 규칙 하나였다.

    conservative  max_single_order_amount  severity BLOCK -> ALLOW, enabled true -> false
    balanced      max_single_order_amount  severity BLOCK -> ALLOW, enabled true -> false
    aggressive    max_single_order_amount  severity BLOCK -> ALLOW, enabled true -> false

## 왜 계약을 바꾸지 않았나

먼저 계약을 DB 에 맞추는 쪽을 실제로 해 봤다. 카탈로그·생성기 불변식·생성 예시를 고치고
`contracts/run_openapi_gate.py --write` 로 재생성하니 root OpenAPI 에서 한 줄이 바뀌었다.

    687e97767beb78d9a58e65de113d323817522975a929099c6991ec8185d83784  (이전)
    4ada21ed0d7a529bcce8e55d92976ab59d396df9fea1966a1eb900f76ae8861e  (이후)

그 한 줄이 전이 검증 체인을 깼다. `verify_p1_return_signal_v3_openapi_transition.py` 가
현재 root 를 pre-V3 기준으로 투영해 바이트 동일을 요구하고, 다이제스트 확장은 그 additive
표면 밖이다.

    ContractValidationError: Signal v3 changed root bytes outside its additive surface

계약 테스트가 기준선 414개 전부 통과에서 **5 실패 · 22 오류**로 떨어졌고(전이 체인을 타는
모듈이 import 단계에서 죽어 수집 개수 자체가 399로 줄었다), 그래서 되돌렸다. 이 벽은 전에도
같은 자리에서 만났다 — `PRINCIPLE_VERSION_DRIFT` 블로커 이름을 넣지 못한 이유와 같다.
반쯤 동기화된 계약 게이트를 남기지 않는다.

되돌릴 수 없는 쪽은 V143 이다. 유니버스에 주당 1,737,000원(000660) 같은 종목이 있어
300,000원 상한 아래에서는 한 주도 살 수 없다. 한도를 되살리면 매수가 전부 막힌다.
임계값만 올리는 것도 같은 드리프트다 — 카탈로그가 프리셋별 임계값 셋을 고정하고 있다.

## 무엇을 고쳤나

**계약 v1 은 동결한 채, 이탈을 테스트에서 이름으로 인정한다.**

* `PrincipleContractMigrationIntegrationTest` — 대조 직전에 계약 픽스처의
  `max_single_order_amount` 하나에만 V143 의 값을 적용하고 나머지 23개 규칙과 모든
  메타데이터는 그대로 대조한다. 그리고 DB 에서 그 규칙이 실제로 세 프리셋 모두
  `enabled=false, severity=ALLOW` 인지 `jsonb_path_exists` 로 직접 확인한다 — 그러지 않으면
  정규화가 "규칙이 다시 켜졌다"는 변화를 조용히 덮는다.
* `DecisionApiIntegrationTest` — `insertPrinciple` 에 `enforceSingleOrderAmount` 인자를
  넣어, 그 규칙으로 BLOCK 을 확인하려는 두 테스트가 스스로 켠다. 두 테스트는 프리셋
  기본값에 숨어 의존하고 있었고 그것이 조용히 붉어진 이유다. ORDER_SIZE 판정 코드는
  그대로 살아 있고 사용자가 직접 켤 수 있으므로, 그 경로를 계속 지킨다.

스키마·열거값·규칙 tuple·8규칙 계약은 바뀌지 않았다. 마이그레이션도 추가하지 않았다.

## 프론트

바꿀 것이 없다. 규칙 카드는 `enabled` 를 데이터로 받아 렌더하므로 이미 꺼진 상태로 보인다.
"건당 한도가 적용된다"고 단정하는 문구는 `experience-dashboard` 전체에 없다.

## 남는 것

* 문서된 API 예시(`contracts/examples/principle-presets.valid.json`)는 이 규칙 하나에서
  실제 응답과 다르다. 정확히 하려면 root OpenAPI 동결을 푸는 전이 승인이 필요하다.
  그때까지 이 파일이 유일한 근거다.
* 건당 원화 상한은 이제 프리셋 기본값에서 집행되지 않는다. 주문 크기를 묶는 것은 자산 비중
  한도(`max_position_per_asset`)와 자동운용의 사이징 한도다. 다시 켜려면 유니버스 최고
  주가보다 큰 임계값을 골라야 한다.
