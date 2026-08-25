# P1 Team A Dashboard 완료 요청서

## 현재 상태

수신본 51개 파일은 원본 해시를 보존해 intake했고 정식 승격은 하지 않았다. lockfile, 테스트,
Dockerfile과 신규 운영 UI가 없으므로 현재 `DASHBOARD_UI=PARTIAL_TEAM_A_ACTION_REQUIRED`다. 이 상태는
1.0.0의 비차단 capability지만 release note에 공개해야 한다.

## 완료 체크리스트

- `package-lock.json` 또는 승인된 단일 lockfile과 `npm ci` 재현
- digest-pinned Node runtime, non-root/read-only production Dockerfile
- same-origin runtime config만 사용하고 브라우저에 내부 service URL이나 secret을 노출하지 않음
- 로그인 성공 뒤 인증된 health를 다시 조회하고 token은 memory-only로 유지
- RAG v2 multipart upload, import status, delete, profile reindex plan/execute
- 사용자 생성·비활성·reset과 관리자 단독 운용 UI
- backup create/validate/download, restore stage/execute/status
- instrument search, watchlist, quote/bars/ECOS와 provider 상태
- `evidenceMode`, Kill Switch와 주문 상태를 서로 다른 UI 의미로 표시
- LightGBM production 메뉴·카드 제거, Signal abstention은 그대로 표시
- typecheck, lint, unit, API contract, Playwright, production build PASS
- mock, dev intake, test, source map과 credential이 production image/release archive에 없음

공개 endpoint가 아직 없으면 mock으로 완료를 꾸미지 않고 해당 capability를 typed unavailable로 표시한다.
