# Team A/B received preview와 단일 Compose 통합

## 이유

Team A Experience Dashboard와 Team B Return Engine 수신본을 버리지 않고 현재 저장소 배치에서 실행할
수 있어야 하며, 사용자는 `deploy/p1/compose.yml` 하나와 `./capstone preview`만 사용해야 한다.

## 변경 경계

- Team A는 production Next.js image, `/healthz`, same-origin `/api` rewrite와 live 계약 테스트를 사용한다.
- Team B 수신 CSV/PTH/소스/legacy payload는 receipt로 보존하고, SHA-256 및 `weights_only=True`를 강제한
  provider-free `LEGACY_RECEIVED_PREVIEW`만 실행한다.
- `*.pth`, 수신 CSV/JSON과 수신 `__pycache__`는 exact received-file allowlist에만 들어간다. 새로 들어온
  임의 binary/pickle/cache는 계속 거부한다.
- preview용 Dashboard ViewModel은 `SYNTHETIC_FAKE_E2E`로만 stage하며 `REAL_TEAM_B` 권위를 갖지 않는다.
- 공개 RAG Seed import는 squashed B86 DB에 pointer singleton이 0개일 때만 빈
  `NOT_MATERIALIZED` singleton을 복구한다. 비어 있지 않은 대상은 계속 fail-closed한다.
- 기존 raw 32-byte RAG KEK는 키를 회전하지 않고 container-local hex envelope로 읽는다. 새 state는
  처음부터 exact 64자리 lowercase hex를 생성한다.

## 불변식

- Team B Spring REST 호출 책임은 0개이고 교환 경계는 파일 계약이다.
- provider, 실계좌, 실주문 물리 호출은 0이다.
- preview 성공은 release authority가 아니다. exact Team B 10개 artifact가 오기 전까지
  `TEAM_B_REAL_ARTIFACT_MISSING`을 유지한다.
- volume 삭제, prune, 기존 release v1 bytes 변경은 없다.
