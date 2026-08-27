# Team A Dashboard handoff

## 1. 최종 프로그램 목표

사용자가 원칙, 위험 판정, 모의 주문, 자동운용 상태, 근거와 학습일지를 한 화면 흐름에서 정확히 이해하고
조작할 수 있는 production Dashboard를 완성합니다.

## 2. Owner가 이미 준비한 것

exact-33 catalog, generated typed client, same-origin `/api`, deterministic offline fixture, actor/JWT login,
비밀값 비노출 Playwright reporter와 `./capstone team-a acceptance` 명령이 준비돼 있습니다.

## 3. 수정할 것

`workspaces/experience-dashboard/` 안에서만 실제 production component를 완성합니다. 자동운용 status/arm/
disarm/runs, Journal 생성·목록, 주문·Kill Switch·동의·feedback, 증거 badge를 화면 동작에 연결합니다.

## 4. 실행 명령

```bash
./capstone up
./capstone team-a acceptance
cd workspaces/experience-dashboard
npm ci
npm run typecheck
npm run lint
npm test
npm run build
```

## 5. 완료 테스트

실제 same-origin Spring exact-33의 method/path/success status를 모두 확인하고 Playwright skip 0, 4xx/5xx
0, frontend fake production response 0을 증명합니다. synthetic/REAL_TEAM_B와 KIS_MOCK/INTERNAL_PAPER를
다르게 표시하고 LightGBM에는 `RESEARCH_ONLY` badge를 표시합니다.

## 6. 제출할 파일·commit·OCI digest

PR URL, commit SHA, `package-lock.json` SHA-256, production image digest, exact-33 success matrix,
typecheck/lint/unit/contract/build/Playwright 결과와 secret scan 결과를 제출합니다.

## 7. 하지 말아야 할 것

backend, OpenAPI, DB migration, Compose, Seed, provider나 model 코드를 수정하지 않습니다. 가짜 production
transport, 임의 endpoint, credential, KIS 장애 시 자동 INTERNAL_PAPER fallback을 추가하지 않습니다.
