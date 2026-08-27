# Team A Dashboard 완료 요청

기존 Dashboard 구조와 작업 결과를 보존해 주세요. 기능 연결에 그치지 말고 실제 금융기관 서비스처럼 신뢰감 있고 정돈된 화면으로 완성해 주세요. Figma 또는 v0로 주요 화면 시안을 먼저 만든 뒤 production UI에 반영해 주세요.

## 1. 기존 작업 중 보존할 것

- 수정 범위는 `workspaces/experience-dashboard/`뿐입니다. 기존 source, component, route, test와 `package-lock.json`을 재사용하고 전면 재작성하지 않습니다.
- Owner가 준비한 exact-61 backend, exact-38 same-origin acceptance, Seed/reset과 typed client를 복사하거나 우회하지 않습니다.
- 현재 API 목록은 [p1-team-a-acceptance.v2 catalog](../../contracts/catalogs/p1-team-a-acceptance.v2.json)와
  [generated v2 client](../../workspaces/experience-dashboard/src/shared/api/generated/p1-team-a-client.v2.ts)만 참조합니다. v1 exact-56/33
  artifact는 역사 회귀이므로 수정하지 않습니다. 요청서에 endpoint 38개를 다시 적지 않습니다.

## 2. 추가할 사용자 흐름과 디자인

다음 다섯 흐름을 실제 데이터·동적 ID로 연결합니다.

1. 로그인 → Overview → 판단 근거
2. 투자 원칙 → Signal → Risk
3. 모의주문 검토 → 명시적 제출 → 상태·취소·체결
4. 최대 자동운용 금액·손절·익절 → 정책 저장 → 실제 status/blocker → positions/runs → arm/disarm
5. RAG → Journal → model/backtest evidence

화면은 navy, white, neutral gray를 중심으로 하고 숫자는 tabular alignment를 사용합니다. 손익·위험·상태 색상을 일관되게 유지하며 정보 밀도, 표와 차트 가독성을 우선합니다. gradient, glassmorphism, AI sparkle, neon, 과도한 round card, 불필요한 hero 문구와 애니메이션은 사용하지 않습니다. desktop/mobile 반응형, keyboard navigation, WCAG AA contrast와 loading/empty/error/stale/ABSTAIN 상태를 완성합니다.

`KIS_MOCK | INTERNAL_PAPER`, `REAL_TEAM_B | SYNTHETIC_GOLDEN`, `RESEARCH_ONLY`를 badge와 설명으로 명확히 구분합니다. Figma/v0 결과는 그대로 붙이지 말고 현재 domain과 component에 맞게 다듬습니다.

Automation 화면은 입력을 정확히 세 개만 둡니다.

- 최대 자동운용 금액: 1만원~100억원, 1만원 단위
- 손절률: 1~15%
- 익절률: 2~30%이며 손절률보다 커야 함

빠른 선택은 보수 `3/5%`, 균형 `5/10%`, 공격 `8/15%`를 채우고 수동 수정은 `custom`으로
표시합니다. 이 값은 연구 기반 고정 기본값이지 수익 보장이나 프로젝트 데이터 최적값이 아님을 설명하고,
Owner가 제공한 세 근거 링크를 유지합니다. 최대 5개 포지션, 세션당 신규 주문 1개, 09:30 평가,
09:40 BUY cutoff, 15:20 취소·대사를 읽기 쉽게 표시합니다.

현재 status의 `BLOCKED_INCOMPLETE_RISK_BALANCE`를 코드와 한국어 설명으로 반드시 보여 주세요.
blocker가 하나라도 있으면 Start는 disabled이고 UI arm 요청은 0이어야 합니다. provider-free exact-38
backend acceptance만 v2 arm을 한 번 호출해 expected 409를 확인합니다. 기존 v1 arm/disarm 200 복구 흐름은
그대로 유지합니다.

## 3. 실행 명령

```bash
./capstone up
./capstone team-a acceptance
cd workspaces/experience-dashboard
npm ci
npm run typecheck
npm run lint
npm test
npm run build
npx playwright test tests/e2e/automation-policy.spec.ts
```

`tests/e2e/automation-policy.spec.ts`는 Team A가 새로 추가하는 파일입니다. 세 입력 검증, 빠른 선택과
`custom` 전환, blocker 노출과 Start disabled, positions/runs 표시를 한 흐름으로 덮고 UI arm 요청은
0으로 유지합니다. 기존 preview와 기존 테스트도 계속 PASS해야 하며 Playwright skip은 0이어야 합니다.

## 4. 제출물 네 가지

1. PR URL과 commit SHA
2. `package-lock.json` SHA-256
3. typecheck, lint, unit, contract, build, 단일 Automation UI Playwright 결과
4. Figma 링크·export 또는 v0 시안과 Automation desktop/mobile screenshot

## 5. 하지 말아야 할 것

- backend, OpenAPI, migration, Compose, Seed, model/provider 코드 변경
- 새 endpoint, mock server, fake production response 또는 client-side gate 추가
- 기존 source/component 삭제·전면 재작성과 디자인 시스템 교체
- KIS credential 입력, certification, 실제 provider/account/order 호출
- 4xx/5xx를 성공처럼 처리하거나 KIS 실패를 `INTERNAL_PAPER`로 자동 fallback
- blocker를 숨기거나 임의로 `canArm=true`를 만들기, UI에서 수량·종목을 직접 결정하기
