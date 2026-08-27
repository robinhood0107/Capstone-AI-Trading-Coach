# Dashboard 마무리 부탁드립니다

안녕하세요. 백엔드 쪽 작업이 정리돼서 이제 Dashboard를 마지막으로 완성할 수 있는 상태가 됐습니다.

지금까지 만들어 주신 구조와 코드를 그대로 살려서 이어가 주시면 됩니다. 다만 이번에는 "기능이 연결된다" 수준에서 멈추지 않았으면 합니다. 실제 금융기관 서비스를 쓰는 것처럼 신뢰감 있고 정돈된 화면이 목표입니다. 바로 코드부터 손대는 대신 Figma나 v0로 주요 화면 시안을 먼저 잡아 보시고, 그다음에 production UI에 반영하는 순서를 권합니다.

## 건드리지 않으셔도 되는 것

작업 범위는 `workspaces/experience-dashboard/` 안쪽뿐입니다. 기존 source, component, route, test와 `package-lock.json`은 그대로 재사용해 주세요. 전면 재작성은 필요 없습니다.

백엔드는 제가 exact-61 operation까지 올려 뒀고, same-origin `/api` acceptance는 38개로 고정했습니다. Seed와 reset, typed client도 준비돼 있으니 복사하거나 우회하지 말고 그대로 쓰시면 됩니다.

API 목록은 문서에 다시 적지 않았습니다. 아래 두 곳만 보시면 됩니다.

- [p1-team-a-acceptance.v2 catalog](../../contracts/catalogs/p1-team-a-acceptance.v2.json)
- [generated v2 client](../../workspaces/experience-dashboard/src/shared/api/generated/p1-team-a-client.v2.ts)

v1 쪽 exact-56/33 artifact는 과거 기록으로 동결돼 있어서 수정하면 CI가 막습니다. 참고만 해 주세요.

## 채워 주셔야 하는 흐름

실제 데이터와 동적 ID로 다음 다섯 갈래가 끝까지 이어져야 합니다.

1. 로그인 → Overview → 판단 근거
2. 투자 원칙 → Signal → Risk
3. 모의주문 검토 → 명시적 제출 → 상태·취소·체결
4. 최대 자동운용 금액·손절·익절 → 정책 저장 → 실제 status와 blocker → positions·runs → arm·disarm
5. RAG → Journal → model·backtest 근거

## 화면 톤

navy, white, neutral gray를 축으로 가고 숫자는 tabular alignment로 정렬해 주세요. 손익·위험·상태 색상은 화면마다 흔들리지 않게 한 벌로 통일하고, 정보 밀도와 표·차트 가독성을 예쁜 장식보다 먼저 두시면 좋겠습니다.

gradient, glassmorphism, AI sparkle, neon, 과하게 둥근 카드, 굳이 없어도 되는 hero 문구와 애니메이션은 넣지 말아 주세요. 대신 desktop과 mobile 반응형, keyboard navigation, WCAG AA contrast, 그리고 loading·empty·error·stale·ABSTAIN 상태를 빠짐없이 채워 주시면 됩니다.

`KIS_MOCK | INTERNAL_PAPER`, `REAL_TEAM_B | SYNTHETIC_GOLDEN`, `RESEARCH_ONLY`는 badge와 짧은 설명으로 확실히 구분되게 해 주세요. Figma나 v0 결과물을 그대로 붙이면 현재 domain과 안 맞는 부분이 생기니, 지금 component 체계에 맞게 다듬어서 반영해 주시면 좋겠습니다.

## Automation 화면만 조금 자세히

여기가 이번에 새로 붙는 부분이라 조건을 구체적으로 적습니다.

사용자 입력은 정확히 세 개만 둡니다. 그 이상은 만들지 말아 주세요.

- 최대 자동운용 금액: 1만원~100억원, 1만원 단위
- 손절률: 1~15%
- 익절률: 2~30%이고 손절률보다 커야 합니다

빠른 선택 버튼은 보수 `3/5%`, 균형 `5/10%`, 공격 `8/15%` 세 가지이고, 값을 채운 뒤 사용자가 직접 수정하면 `custom`으로 바뀌게 해 주세요. 이 숫자들은 연구 근거를 보고 고정한 기본값이지 수익을 보장하거나 우리 데이터에서 최적화한 값이 아닙니다. 그 점이 화면에서 분명히 읽히도록 문구를 넣고, 제가 붙여 둔 근거 링크 세 개는 지워지지 않게 유지해 주세요.

동작 규칙도 사용자가 화면만 보고 알 수 있어야 합니다. 최대 5개 포지션, 세션당 신규 주문 1건, 09:30 평가, 09:40 이후 신규 매수 없음, 15:20 미체결 취소와 대사입니다.

status에 지금 `BLOCKED_INCOMPLETE_RISK_BALANCE`가 떠 있을 텐데, 이걸 숨기지 말고 코드와 한국어 설명을 같이 보여 주세요. blocker가 하나라도 있으면 Start는 disabled여야 하고, UI에서 arm 요청이 나가는 횟수는 0이어야 합니다. v2 arm은 provider-free backend acceptance에서 한 번만 호출해서 409가 오는지 확인하는 용도로 쓰시면 됩니다. 기존 v1 arm·disarm의 200 복구 흐름은 지금처럼 그대로 살아 있어야 합니다.

## 확인은 이렇게

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

마지막 `tests/e2e/automation-policy.spec.ts`는 아직 없는 파일이라 새로 만들어 주셔야 합니다. 세 입력 검증, 빠른 선택과 `custom` 전환, blocker 노출과 Start disabled, positions·runs 표시까지 한 흐름으로 덮어 주시면 충분합니다. UI arm 요청은 0을 유지해 주세요. 기존 preview와 기존 테스트도 계속 통과해야 하고 Playwright skip은 0이어야 합니다.

## 다 되면 알려 주세요

PR 주소와 commit SHA, `package-lock.json`의 SHA-256, 그리고 위 명령들(typecheck·lint·unit·contract·build·Automation Playwright) 결과를 같이 주시면 제가 이어서 확인하겠습니다. Figma 링크나 export, 없으면 v0 시안과 Automation 화면 desktop·mobile 스크린샷도 함께 보여 주시면 좋겠습니다.

## 이건 피해 주세요

- backend, OpenAPI, migration, Compose, Seed, model·provider 코드 수정
- 새 endpoint나 mock server 추가, production response를 흉내낸 가짜 응답, client-side gate
- 기존 source·component 삭제나 전면 재작성, 디자인 시스템 교체
- KIS credential 입력, certification 시도, 실제 provider·계좌·주문 호출
- 4xx나 5xx를 성공처럼 처리하거나, KIS 실패를 `INTERNAL_PAPER`로 자동 전환
- blocker를 숨기거나 임의로 `canArm=true`를 만드는 것, UI에서 수량이나 종목을 직접 정하는 것

진행하면서 막히는 부분 있으면 편하게 물어봐 주세요.
