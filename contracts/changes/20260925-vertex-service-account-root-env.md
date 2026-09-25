# Vertex service-account credential source: project-root `.env`

## 상태

구현 변경. 이 기록은 20260812의 path-based credential runtime을 이후 실행 코드에서 대체한다. 이전 변경 기록과
배포 이력은 감사 목적으로 그대로 보존한다.

## 변경

- 프로젝트 운영자가 직접 편집하는 환경 파일은 프로젝트 루트 `.env` 하나로 고정한다. 권한은 `0600`이어야 한다.
- Google service-account JSON 전체는 `MARS_VERTEX_SERVICE_ACCOUNT_JSON_B64` 한 줄에 canonical Base64로 저장한다.
- 프로젝트와 모델은 같은 파일의 `MARS_VERTEX_PROJECT_ID`, `MARS_VERTEX_MODEL_ID`로 설정한다. Project ID는
  인코딩된 account의 `project_id`와 일치해야 한다.
- Kotlin과 Python provider는 Base64 값을 메모리에서 검증·파싱하고 service-account OAuth를 사용한다. 실행 시
  service-account JSON 파일 경로, file mount, ambient ADC 또는 API-key fallback은 사용하지 않는다.
- Public DEMO/FULL 조립기는 루트 `.env` 전체를 전달하지 않고 각 제품의 명시적 key allowlist만 내보낸다.
  FULL의 RAG runtime seed에는 credential을 주지 않고 decoded bytes의 SHA-256 fingerprint만 전달한다.
- `compose.yml`과 생성된 `compose.release.yml`은 프로젝트 루트 `.env`를 Compose interpolation에 사용하고,
  설정한 Vertex 값만 해당 API/seed service의 환경으로 넘긴다.

## 보안·호환성

- 루트 `.env`는 Git에서 제외된 비밀값이며 secret·계좌·토큰·credential은 저장소에 추가하지 않는다.
- generated `mars-public-{demo,full}.env`는 루트 `.env`에서 만든 제품별 provider-secret bundle이다.
  운영자가 별도로 편집하는 설정 원본이 아니다. Compose metadata env는 컨테이너에 전달하지 않는다.
- 이전 JSON 입력에서 루트 `.env`로 옮기는 importer는 일회성 도구다. 런타임은 기존 파일을 읽지 않는다.
- API/OpenAPI/proto, database schema 및 persisted data 계약에는 변경이 없다.

## 검증 기준

- secret assembly 결과에 credential JSON 파일이 없고 필요한 Base64 key만 제품별 provider env에 포함된다.
- Compose rendered model에서 API의 JSON-file secret mount가 사라지고 FULL seed에는 credential 대신 SHA-256만 전달된다.
- Kotlin/Python provider가 메모리 입력을 읽으며 malformed/noncanonical Base64를 거부한다.
- repository scan에서 활성 runtime 코드·Compose·current API spec에 credential JSON path reader가 남지 않는다.
