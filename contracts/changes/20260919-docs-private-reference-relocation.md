# 2026-09-19 중간 개발 기록 37건의 저장소 외부 이관

## 범위

- `docs/` 아래 md 83개 가운데 37개를 `private-reference/docs-archive/` 로 옮긴다. 옮기는 것은
  특정 날짜의 팀 요청서(4), 한 번 쓰고 끝난 검증 증거와 일회성 운영 가이드(12), 세션별 테스트
  관측 기록(13), 판정·시나리오(4), 루트 잡문서(4)다. 공개 저장소의 `docs/` 에는 스펙과 지금도
  유효한 운영 문서 46개가 남는다.
- `verify_pre_s5_doc_truth_freeze.py` 의 `P1_CURRENT_MUTABLE_DOCUMENTS` 에 이관 대상 37개를
  더한다. `immutable_history_diff_errors` 가 `--diff-filter=MDT` 로 삭제를 잡고
  `classify_markdown` 이 `docs/**.md` 를 `HISTORICAL_SUPERSEDED` 로 분류하므로, 이 목록에 없는
  문서는 옮기는 순간 게이트가 막는다. 봉인의 목적은 증거를 다시 쓰지 못하게 하는 것이지
  저장소 밖으로 내보내지 못하게 하는 것이 아니다.
- 남는 문서에서 이관분을 가리키던 마크다운 링크와 백틱 경로 참조를 끊는다. 대상은
  `README.md`, `docs/README.md`, `docs/test/README.md`, `docs/test/P1_TEAM_수신.md`,
  `docs/금융공학_공식_및_자동매매_로직_설명서.md` 와 decision-platform 문서 5개다.
  문장은 지우지 않고 가리키던 경로만 뺀다. 문장을 지우면 무엇을 했는지가 함께 사라진다.
- `docs/test/README.md` 의 상태 요약 표는 이관분 11개의 색인이었다. 표를 빼고 기록이 저장소
  밖에 있다는 사실과 다시 돌릴 수 있는 실행 절차만 남긴다.
- `.github/workflows/repo-hygiene.yml` 의 `required_paths` 에서 삭제된 규칙 파일을 뺀다.
  `CONTRIBUTING.md` 가 작업 규칙의 단일 진실 소스다.

## 권한과 비권한

- 이관 대상에서 뺀 것이 있다. `docs/adr/ADR-039-strong-llm-judgement-authority.md` 는 Strong LLM
  의 판단 권한 경계를 적은 설계 결정이라 스펙 성격이고, `s8-user-test-kit` 의
  `operator-guide.md`·`consent-and-data-notice.md` 는 그 킷의 실체다. 계약이 붙잡는
  `s8-user-test-kit/README.md` 가 바로 이 둘을 구성물로 설명한다.
- 계약이 파일명으로 붙잡는 문서 43개는 옮기지 않는다. `ACTIVE_PUBLIC_PATHS`,
  `FULL_APP_DOCUMENTS`, `repo-hygiene` 의 `required_paths`, 개별 테스트가 경로로 읽는 것들이며,
  `docs/adr/ADR-027` 은 s1-4x contract-manifest 에 sha256 으로 박혀 있어 이동 자체가 불가능하다.
- `private-reference/` 는 `.gitignore` 가 막고 있어 추적 파일 수가 0이다. 이관 뒤에도 0이다.
- 이 변경은 문서의 위치만 바꾼다. 계약 스키마, OpenAPI, 카탈로그, 마이그레이션은 건드리지
  않는다. `docs/README.md` 의 hidden 마커 블록과 상태 표도 그대로다.

## 외부 실행 상한

외부 provider 호출이 없다. 파일 이동과 텍스트 편집뿐이며 예상 비용은 0달러다.
