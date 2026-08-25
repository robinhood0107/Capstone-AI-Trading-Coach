# P1 1.0.0 full-app v2 권위와 게이트

## 현재 판정

```text
P1_CORE=MERGED_REVALIDATION_REQUIRED
PUBLIC_RAG_SEED=NOT_MATERIALIZED
OWNER_RAG_BACKEND=IMPLEMENTATION_REQUIRED
BGE_OCR_CPU_INTEL=IMPLEMENTATION_REQUIRED
PROVIDER_LIVE_READ=HISTORICAL_PARTIAL_REVALIDATION_REQUIRED
TEAM_B_REAL_ARTIFACT=BLOCKED
SECURITY_RELEASE=INCOMPLETE
SUPPLY_CHAIN_RELEASE=NOT_RUN
COMPOSE_E2E=NOT_RUN
DASHBOARD_UI=PARTIAL_TEAM_A_ACTION_REQUIRED
CUDA=PENDING_USER_HARDWARE_VERIFICATION
SEARXNG=KNOWN_DEGRADED_NONBLOCKING
LIGHTGBM=RESEARCH_ONLY_NOT_APPLICABLE
LIVE_ORDER=FUTURE_NOT_IMPLEMENTED
P1_FINAL=NOT_READY
P1_1_0_0_RELEASED=FALSE
```

현재 계약은 `contracts/catalogs/p1-full-app-release-contract.v2.json`, manifest schema는
`deploy/p1/full-app-release-manifest.v2.schema.json`이다. 기존 `deploy/p1/release-manifest.schema.json`과
`.github/workflows/p1-offline-demo-release.yml`은 Core-only 역사 회귀이며 full-app `1.0.0` 발행 권위가
없다.

세부 실행 입력은 [Team A Dashboard 요청서](P1_TEAM_A_DASHBOARD_완료_요청서.md),
[Team B Return Engine 요청서](P1_TEAM_B_RETURN_ENGINE_완료_요청서.md),
[운영 후속 경계](P1_운영_후속_경계.md), [최종 테스트·증거 판정표](P1_최종_테스트_증거_판정표.md)를
따른다.

## G0 intake receipt

원본은 local-only이고 이 문서에 private path를 기록하지 않는다. `lstat` 기준 symlink와 nested Git은
각각 0개였고 secret signature 검사에서 실제 credential은 발견되지 않았다.

| bundle | regular files | canonical manifest SHA-256 | 판정 |
|---|---:|---|---|
| Experience Dashboard v2 | 51 | `56251f4b9990a2e128dbd7f073b411089e5484baad69328759439fa0dcaf2328` | byte copy verified, promotion blocked |
| Return Engine | 30 | `59295bd7a2ca21375fecf6777496695db78d70e403f5489f77c65cf3c4374ee2` | byte copy verified, cache excluded from promotion |

Return Engine 원본에는 `.pyc` 18개, raw CSV 1개, provenance 없는 pickle `.pth` 1개가 있다. Dashboard는
lockfile, 테스트와 Dockerfile이 없다. 파일별 mode, size와 SHA-256 manifest는 각 ignored intake root에
보존하며 production image, release archive와 Git에는 넣지 않는다.

## 종결 규칙

다음 아홉 gate는 모두 hard gate다. 하나라도 `PASS`가 아니면 v2 `FINAL` manifest 자체가 유효하지 않으며
tag와 GitHub Release를 만들지 않는다.

1. `P1_CORE`
2. `PUBLIC_RAG_SEED`
3. `OWNER_RAG_BACKEND`
4. `BGE_OCR_CPU_INTEL`
5. `PROVIDER_LIVE_READ`
6. `TEAM_B_REAL_ARTIFACT`
7. `SECURITY_RELEASE`
8. `SUPPLY_CHAIN_RELEASE`
9. `COMPOSE_E2E`

Dashboard UI, CUDA 실기기와 SearXNG만 계약에 열거된 비차단 상태를 가질 수 있다. 그 상태는 숨기지 않고
release manifest와 release note에 함께 공개한다. provider receipt는 content-free이며 Voyage query 1,
Google-grounded Vertex 1, KIS token 최대 1, KIS data 최대 2, ECOS 최대 2, retry/account/balance/order 0을
넘을 수 없다.
