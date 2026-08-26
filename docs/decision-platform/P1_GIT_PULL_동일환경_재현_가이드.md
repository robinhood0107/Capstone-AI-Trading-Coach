# 새 PC에서 같은 환경 실행하기

## 결론

공개 DB는 Docker volume을 Git에 올리지 않는다. 대신 migration과 공개 전용 Seed를 Git에 넣고 새
PostgreSQL에서 다시 만든다. 이 방식은 계정, 비밀번호, 질문 기록, 개인 문서, provider ledger와 secret을
배포하지 않으면서 모든 설치에서 같은 공개 RAG 상태를 만든다.

현재 Seed는 다음 세 Git 파일로 고정돼 있다.

```text
deploy/p1/seed/public-rag/public-rag-seed.v1.manifest.json
deploy/p1/seed/public-rag/public-rag-seed.v1.jsonl.gz.part-0001
deploy/p1/seed/public-rag/public-rag-seed.v1.jsonl.gz.part-0002
```

기대 상태는 `sources=142`, `chunks=7871`, `embeddingDimension=1024`, public pointer `FULL_READY`다.
fresh V87 PostgreSQL import는 `IMPORTED_FULL_READY`, 같은 Seed 재실행은
`NOOP_MATCHING_ACTIVE_SEED`로 검증했다.

## Git에 들어가는 것과 들어가지 않는 것

| Git/Compose로 재현하는 것 | Git에 넣지 않는 것 |
|---|---|
| Flyway migration과 공개 Seed | 실행 중인 PostgreSQL/Redis volume |
| Compose와 설치 명령 | 사용자 계정과 비밀번호 |
| BGE/Paddle exact revision·hash | 개인 문서와 질문 기록 |
| application source와 dependency lock | API key, token, 계좌 정보 |
| 검증된 OCI image digest | model download cache와 로컬 output |

따라서 “같은 상태”는 같은 공개 Seed·schema·image·model을 뜻한다. 두 사람의 개인 계정, secret과 로컬
운영 기록까지 복제한다는 뜻이 아니다.

## 새 PC에서 실행하는 최종 흐름

통합 담당자가 main 병합 완료를 안내한 뒤 실행한다. Git, Docker Compose v2, WSL/Linux의 Python 3와
OpenSSL이 필요하다. Java와 Node.js는 별도 설치하지 않아도 된다.

```bash
git switch main
git pull --ff-only origin main
./capstone up
```

Windows PowerShell에서는 `.\capstone.ps1 up`을 사용하며 WSL이 필요하다. 기본은 정확히 5개 컨테이너이고
`./capstone up --models`는 공식 BGE-M3와 llama.cpp PaddleOCR-VL을 더한 정확히 7개다. 최초 설치는
Docker image를 만들고 DB를 `role-bootstrap -> migrate -> seed-import -> identity-bootstrap` 순서로
만든 뒤 Dashboard Seed와 Team B 미리보기를 준비한다. 모든 준비 작업은 `docker compose run --rm`으로
실행되어 종료 컨테이너를 남기지 않는다. 두 번째 실행은 같은 Seed를 덮어쓰지 않는다.

```bash
./capstone status
./capstone smoke
./capstone logs
./capstone down     # volume 보존
```

## 지금 당장 가능한 확인

```bash
./deploy/p1/verify-public-rag-seed \
  deploy/p1/seed/public-rag/public-rag-seed.v1.manifest.json
./capstone doctor
```

현재 기대 결과는 doctor PASS다. `./capstone up` 뒤 Dashboard `/healthz`는
`teamBPreview=LEGACY_RECEIVED_PREVIEW`를 반환한다. `TEAM_B_REAL_ARTIFACT_MISSING`은 preview 실패가 아니라
Team B의 고정 결과 파일 10개를 아직 받지 않았다는 뜻이다.

## 원격 재현의 필수 조건

로컬 commit만 있으면 상대방은 `git pull`로 받을 수 없다. 다음이 모두 끝나야 한다.

1. 현재 full-app branch를 원격에 push하고 검토한다.
2. PR을 `main`에 병합하고 post-merge CI를 확인한다.
3. Team A/B production source와 lockfile/Dockerfile을 병합한다.
4. clean Linux/WSL과 Windows Docker Desktop에서 위 명령을 다시 실행한다.
5. FINAL 배포 시 application image를 public registry에 digest-pinned로 발행한다.

이 조건 전에는 다른 팀원에게 “main을 pull하면 완전히 재현된다”고 안내하지 않는다.
