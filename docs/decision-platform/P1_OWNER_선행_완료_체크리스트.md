# P1 우리 쪽 선행 완료 체크리스트

## 현재 결론

```text
OWNER_HANDOFF_READY=FALSE
GIT_PULL_FULL_REPRODUCIBLE=FALSE
PUBLIC_RAG_SEED_GIT_REPRODUCIBLE=TRUE
P1_FINAL=NOT_READY
```

공개 Seed DB, 공식 모델 Compose와 fail-closed 설치기는 준비됐지만 “내가 할 수 있는 일 전부 완료” 상태는
아니다. 사용자가 정한 순서대로라면 아래 우리 쪽 hard gate를 먼저 끝내고 Team A/B 요청서를 전달한다.

## 완료한 우리 쪽 항목

- full-app v2 계약, Seed manifest/schema와 Team B artifact schema
- 공개 Seed 2개 Git 조각의 파일·크기·SHA-256 검증
- fresh V87 PostgreSQL import `IMPORTED_FULL_READY`와 재실행 `NOOP_MATCHING_ACTIVE_SEED`
- 공식 TEI `BAAI/bge-m3` exact revision container 기동과 실제 1024차원 embedding 확인
- 공식 `llama.cpp` + PaddlePaddle GGUF/mmproj exact hash 기동과 health 확인
- Linux/WSL·PowerShell 공통 `install/start/stop/status/doctor/backup/restore/verify` 진입점
- Team B 실물 artifact가 없을 때 full 설치를 중단하는 fail-closed preflight

## 우리가 더 끝내야 하는 항목

| 순서 | 우리 쪽 잔여 작업 | 완료 기준 |
|---:|---|---|
| 1 | owner 문서 업로드/OCR/profile 변경/delete E2E | browser부터 DB까지 실제 파일로 PASS |
| 2 | Paddle OCR 실제 품질과 CPU·Intel lane | born-digital/scan fixture, CPU/Intel hard gate PASS |
| 3 | 계정 bootstrap·강제 비밀번호 변경·격리 | owner/admin, foreign owner 404, reset/session revoke PASS |
| 4 | 시장데이터·Google/SearXNG 제품 경계 | 저장값 read, 승인형 refresh, provider 미설정 degrade PASS |
| 5 | G7 원자 backup/restore | 실패 rollback, session revoke, secret/Seed 제외 PASS |
| 6 | gateway와 전체 application Compose/image | digest-pinned clean pull과 same-origin 기동 PASS |
| 7 | 전체 Python/Kotlin과 contract 회귀 | clean frozen full suite PASS |
| 8 | 일반 보안·dependency/image scan과 공급망 | secret scan, SBOM, provenance, signature PASS |
| 9 | Linux/WSL·Windows clean Compose E2E | pull→install→Seed→restart→backup/restore PASS |
| 10 | 원격 반영 | push, PR merge, `origin/main`, post-merge CI 확인 |

provider live read 재검증은 별도 사용자 승인 범위가 필요한 hard gate다. 실계좌·잔고·주문 호출은 계속
0이며 LightGBM production과 live order는 이 체크리스트의 구현 대상이 아니다.

## 상대방에게 요청할 외부 항목

- Team A: [Dashboard 완료 요청서](P1_TEAM_A_DASHBOARD_완료_요청서.md)
- Team B: [Return Engine 완료 요청서](P1_TEAM_B_RETURN_ENGINE_완료_요청서.md)

두 요청서는 이미 쉬운 말과 copy-paste 메시지로 준비하되, 사용자의 순서 요구에 따라 이 문서가
`OWNER_HANDOFF_READY=TRUE`로 갱신되기 전에는 “우리 쪽 완료”라고 전제해 보내지 않는다.

## 최종 재현 문서

DB volume을 공유하지 않고 같은 공개 상태를 만드는 방법은
[P1 `git pull` 동일환경 재현 가이드](P1_GIT_PULL_동일환경_재현_가이드.md)를 따른다.
