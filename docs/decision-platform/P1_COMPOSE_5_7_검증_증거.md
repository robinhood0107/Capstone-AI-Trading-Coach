# P1 단일 Compose 5/7개 검증 증거

## 2026-08-26 로컬 판정

```text
COMPOSE_AUTHORITY=deploy/p1/compose.yml
DEFAULT_PERSISTENT_CONTAINERS=5
MODELS_PERSISTENT_CONTAINERS=7
ONE_SHOT_EXITED_CONTAINERS=0
OLD_CAPSTONE_P1_PREVIEW_CONTAINERS=0
VOLUMES_REMOVED=0
PROVIDER_CALLS=0
KIS_LIVE_BROKERAGE_CALLS=0
```

기본 다섯 개는 `postgres`, `redis`, `actor-authority`, `decision-platform`,
`experience-dashboard`다. 모델 profile은 공식 BGE-M3 TEI와 공식 llama.cpp PaddleOCR-VL 두 개만
추가한다.

## 실제 검증 결과

| 검증 | 결과 |
|---|---|
| Compose config | PASS |
| 기본 `./capstone up` | 5/5 healthy |
| 전체 `./capstone up --models` | 7/7 healthy |
| 로그인·DB async `./capstone smoke` | PASS, Provider 0 |
| Dashboard live Playwright | PASS, 로그인 후 6개 주요 화면과 same-origin `/api` 실호출 |
| Team B prepare/verify | PASS, `LEGACY_RECEIVED_PREVIEW` |
| Dashboard `/healthz` | `UP` |
| Dashboard `/api/team-b/artifact` | JSON parse PASS |
| BGE-M3 실제 embedding | 1024 dimensions PASS |
| llama.cpp PaddleOCR-VL health | PASS |
| Python worker 강제 종료 | supervisor가 전체 컨테이너 재시작, 다시 healthy |
| actor 통신 | mTLS 경로로 로그인 smoke PASS |
| Redis health | 인증 secret으로 정확히 `PONG` 확인 |

bootstrap, migration, Seed, identity, Dashboard seed, Team B prepare, 모델 fetch는
`docker compose run --rm`으로 실행했다. 따라서 완료 뒤 project에 exited container가 남지 않는다.

## 2026-08-27 main 병합·fresh clone 추가 증거

```text
PR_173_MERGE_SHA=0eeb8825a309602fdf91a0454660c8b072b902cc
POST_MERGE_REQUIRED_WORKFLOWS=6_SUCCESS
FRESH_CLONE_DOCTOR=PASS
FRESH_CLONE_DEFAULT_UP=5_HEALTHY
FRESH_CLONE_SMOKE=PASS
FRESH_CLONE_ONE_SHOT_RESIDUAL=0
FRESH_CLONE_PROVIDER_CALLS=0
FRESH_CLONE_DOWN_VOLUMES_PRESERVED=TRUE
```

fresh clone은 기존 project와 다른 Compose project, state directory, API/Dashboard port를 사용했다. 이미
존재하던 같은 이름의 diagnostic volume은 삭제하지 않았고, 새 unique project로 재실행해 empty DB에서
B86+V87+V88+V89 migration과 Seed import를 확인했다.

## 아직 이 증거가 의미하지 않는 것

- Team B real artifact 완료가 아니다.
- Team A의 현재 연결 API live Playwright는 완료됐지만, 요청서의 미사용 명세 API 추가 연결 완료는 아니다.
- Intel 실기기 OCR 품질 gate 완료가 아니다.
- KIS 모의투자 physical certification 완료가 아니다. 확인 시각에는 거래시간이 끝났으며 호출은 0이다.
- Team A/B 실제 결과 병합, physical activation 또는 Release 완료가 아니다.

```text
TEAM_B_REAL_ARTIFACT_MISSING
KIS_MOCK_PHYSICAL_CERTIFICATION=NOT_RUN_MARKET_CLOSED
CODEX_SECURITY_DEEP_SCAN=NOT_RUN_USER_SCOPED_OUT
```
