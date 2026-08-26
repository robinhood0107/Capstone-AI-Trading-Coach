# P1 1.0.0 full-app v2 권위와 게이트

## 현재 판정

```text
P1_CORE=MERGED_REVALIDATION_REQUIRED
PUBLIC_RAG_SEED=IMPLEMENTED_REVALIDATED_GIT_AND_FRESH_DB
FULL_APP_INSTALLER=IMPLEMENTED_FAIL_CLOSED_MERGE_CANDIDATE
OWNER_RAG_BACKEND=IMPLEMENTATION_REQUIRED
BGE_OCR_CPU_INTEL=PARTIAL_CPU_BOOT_BGE_EMBED_PASS_INTEL_OCR_E2E_REQUIRED
PROVIDER_LIVE_READ=HISTORICAL_PARTIAL_REVALIDATION_REQUIRED
TEAM_B_REAL_ARTIFACT=BLOCKED
SECURITY_RELEASE=INCOMPLETE
SUPPLY_CHAIN_RELEASE=NOT_RUN
COMPOSE_E2E=BLOCKED_OWNER_AND_TEAM_B_WORK_REMAINING
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

## G3 public RAG Seed

V73 active public pointer에서 도달 가능한 공개 행만 data-only Seed로 export했다. 원문 PDF, 계정,
대화, owner 문서, provider ledger와 secret은 포함하지 않는다. canonical manifest는
`deploy/p1/seed/public-rag/public-rag-seed.v1.manifest.json`이며 source/chunk/dimension은 정확히
`142/7,871/1024`, archive는 SHA-256 결속된 32MiB 이하 2개 조각이다.

fresh V87 PostgreSQL 통합 검증에서 migration → hash 검증 → transaction staging → pointer-last
activation이 `IMPORTED_FULL_READY`로 통과했고, 같은 Seed 재실행은
`NOOP_MATCHING_ACTIVE_SEED`였다. 이 증거는 Seed 구현의 merge-candidate 판정이며 아직 Compose E2E,
owner RAG, 모델 설치, provider live gate 또는 전체 `PUBLIC_RAG_SEED=PASS`를 뜻하지 않는다.

## G2 installer와 Compose Seed dependency

repository root의 `./capstone`과 `capstone.ps1`은 동일한 여덟 운영 명령을 제공한다. full 기본 lane은
공개 Seed 조각을 host에서 다시 hash 검증하고 Team B real artifact가 없으면 0600 local stage marker를
남긴 뒤 종료한다. BGE-M3는 공식 Hugging Face TEI CPU 이미지와 `BAAI/bge-m3` exact revision을,
PaddleOCR-VL은 공식 `llama.cpp` server 이미지와 PaddlePaddle의 공식 1.6 GGUF/mmproj를 사용한다.
두 이미지와 모델 revision/SHA-256은 계약에 고정하고 named volume cache로 최초 다운로드를 재사용한다.
모델 서비스는 private service network에 연결하고 host port는 열지 않는다. 최초 exact-revision
다운로드를 위해 호스트 포트가 없는 전용 outbound bridge만 추가한다. 현재 상태에서 종료는 의도된
`BLOCKED_REQUIRED_ARTIFACTS`이며 full-app 실행 성공이 아니다.

현재 full-app Compose의 단일 권위는 `deploy/p1/compose.yml`이다. Core, migration, Seed, identity,
Spring/Python, BGE와 Paddle의 기동 순서는 `service_healthy`와
`service_completed_successfully` 조건으로 연결한다. 과거 Core-only offline bundle의
`compose.db.yml`, `compose.kafka.yml`, `compose.offline.*.yml`은 v1 회귀용 historical asset이며 현재
full-app를 조립하는 overlay가 아니다.

기존 provider-free Core는 명시적 `--degraded`에서만 실행하며 매 실행이
`CAPSTONE_RELEASE_AUTHORITY=NONE`을 출력한다. 새 Compose overlay의 실제 의존성은
`migrate -> seed-import -> identity-bootstrap`이고 BGE/OCR 공식 모델 overlay를 함께 합성한다. Seed
importer는 전용 Docker secret의 migration DSN과 read-only Seed mount만 받는다. G7 atomic restore가
없으므로 공개 `restore` 명령은
`BLOCKED_G7_ATOMIC_RESTORE_NOT_IMPLEMENTED`로 닫혀 있다. 따라서 이 단위는 설치기/Seed wiring의
merge candidate일 뿐 `COMPOSE_E2E=PASS`가 아니다.

세부 실행 입력은 [Team A Dashboard 요청서](P1_TEAM_A_DASHBOARD_완료_요청서.md),
[Team B Return Engine 요청서](P1_TEAM_B_RETURN_ENGINE_완료_요청서.md),
[우리 쪽 선행 완료 체크리스트](P1_OWNER_선행_완료_체크리스트.md),
[Git pull 동일환경 재현 가이드](P1_GIT_PULL_동일환경_재현_가이드.md),
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
