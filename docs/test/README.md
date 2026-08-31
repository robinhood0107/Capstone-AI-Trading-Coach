# P1 테스트 기록 색인

이 폴더는 **무엇을 어떻게 확인했고 무엇을 확인하지 못했는지**를 남긴다. 통과했다는 주장이 아니라
관측 기록이다. 관측하지 않은 것은 PASS로 적지 않는다.

## 상태 요약

| 문서 | 대상 | 결과 |
|---|---|---|
| [P1_관통_파이프라인.md](P1_관통_파이프라인.md) | 번들→신호→판단→위험→주문→체결→손익 | **PASS** (28단계) |
| [P1_RAG_V2.md](P1_RAG_V2.md) | RAG v2 검색 경계와 Vertex 생성 | **PASS** (기본값·옵트인 두 구성) |
| [P1_API_표면.md](P1_API_표면.md) | 관통 밖 REST 표면 | **PASS** (19단계) |
| [P1_브로커리지.md](P1_브로커리지.md) | paper·mock 두 원장 | **PASS** (9단계) |
| [P1_자동운용.md](P1_자동운용.md) | 자동운용 v1·v2 표면과 경계 | **PASS** (8단계) |
| [P1_비동기.md](P1_비동기.md) | DB 비동기 레인, Kafka 범위 | **PASS** (3단계) |
| [P1_비활성_기능.md](P1_비활성_기능.md) | 꺼져 있어야 하는 것 | **PASS** (5단계) |
| [P1_AI_판단.md](P1_AI_판단.md) | AI 판단이 자동매매에 닿는 경로 | **PASS** (6단계) |
| [P1_TEAM_수신.md](P1_TEAM_수신.md) | Team B 10파일 수신 경로 | **PASS** (5단계) |
| [P1_검증_러너.md](P1_검증_러너.md) | 배포 검증 스크립트 | 아래 문서 참조 |
| [P1_장시간_의존_항목.md](P1_장시간_의존_항목.md) | 거래시간이 필요한 항목 | 이전 기록 + drift 명시 |
| [P1_CI.md](P1_CI.md) | 워크플로 11종 | 아래 문서 참조 |

## 실행 순서

스택이 떠 있어야 한다(`./capstone up`). 각 runner는 시작 시 스냅샷을 찍고 끝에서 **차집합만**
지운다. DB 볼륨은 삭제하지 않는다.

```bash
cd workspaces/decision-platform/python-services

P1_FULL_PIPELINE_E2E=1 .venv/bin/python -m tests.e2e.full_pipeline_e2e \
  --out ../../../artifacts/decision-platform/e2e/full-pipeline.json
P1_API_SURFACE_E2E=1 .venv/bin/python -m tests.e2e.api_surface_e2e \
  --out ../../../artifacts/decision-platform/e2e/api-surface.json
P1_BROKERAGE_E2E=1 .venv/bin/python -m tests.e2e.brokerage_e2e \
  --out ../../../artifacts/decision-platform/e2e/brokerage.json
P1_AUTOMATION_V1_E2E=1 .venv/bin/python -m tests.e2e.automation_v1_e2e \
  --out ../../../artifacts/decision-platform/e2e/automation-v1.json
P1_RAG_V1_E2E=1 .venv/bin/python -m tests.e2e.rag_v1_e2e \
  --out ../../../artifacts/decision-platform/e2e/rag-v1.json
P1_ASYNC_E2E=1 .venv/bin/python -m tests.e2e.async_e2e \
  --out ../../../artifacts/decision-platform/e2e/async.json
P1_DISABLED_FEATURES_E2E=1 .venv/bin/python -m tests.e2e.disabled_features_e2e \
  --out ../../../artifacts/decision-platform/e2e/disabled-features.json
P1_TEAM_INTAKE_E2E=1 .venv/bin/python -m tests.e2e.team_intake_e2e \
  --out ../../../artifacts/decision-platform/e2e/team-intake.json
P1_AI_JUDGEMENT_E2E=1 .venv/bin/python -m tests.e2e.ai_judgement_e2e \
  --out ../../../artifacts/decision-platform/e2e/ai-judgement.json
P1_RAG_V2_BOUNDARY_CHECK=1 .venv/bin/python -m tests.e2e.rag_v2_boundaries --with-live-query \
  --out ../../../artifacts/decision-platform/e2e/rag-v2-boundaries-default.json
```

**순서가 중요하다.** `full_pipeline_e2e`는 brokerage 어댑터를 켠 구성으로 컨테이너를 다시 세우고
끝에서 되돌린다. 예전에는 그 복원이 compose 기본값으로 돌아가 RAG를 끄고 끝났고, 뒤이어 도는
RAG 검사가 "로컬 루트가 없다"로 죽었다. 원인이 제품이 아니라 복원 누락이라 러너가 지금 도는
컨테이너에게 물어 그대로 되돌리게 고쳤다. 그래도 위 순서대로 도는 것이 가장 안전하다.

마지막 줄은 provider 물리 호출을 한 번 쓴다. 생성형 답변까지 보려면
`--with-vertex-generation`을 더하고 스택을 옵트인 설정으로 띄운다
([P1_RAG_V2.md](P1_RAG_V2.md) 참조).

## 규약

- runner 파일명에 `test_` 접두사를 쓰지 않는다. pytest가 수집하면 스택 없이 돌다가 깨진다.
- 모든 runner는 명시적 opt-in 환경변수가 있어야 돈다. 실수로 도는 일이 없어야 한다.
- 판정표 JSON은 `artifacts/decision-platform/e2e/`에 남고 git에 추적된다.
- 정리에 실패하면 runner 전체가 FAIL이다. 남은 흔적이 다음 실행의 거짓 통과를 만들기 때문이다.
