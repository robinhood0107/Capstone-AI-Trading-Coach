# Dashboard 마무리 부탁드립니다

안녕하세요. 백엔드 쪽 작업이 정리돼서 이제 Dashboard를 마지막으로 완성할 수 있는 상태가 됐습니다.

지금까지 만들어 주신 구조와 코드를 그대로 살려서 이어가 주시면 됩니다. 다만 이번에는 "기능이 연결된다" 수준에서 멈추지 않았으면 합니다. 실제 금융기관 서비스를 쓰는 것처럼 신뢰감 있고 정돈된 화면이 목표입니다. 바로 코드부터 손대는 대신 Figma나 v0로 주요 화면 시안을 먼저 잡아 보시고, 그다음에 production UI에 반영하는 순서를 권합니다.

## 건드리지 않으셔도 되는 것

작업 범위는 `workspaces/experience-dashboard/` 안쪽뿐입니다. 기존 source, component, route, test와 `package-lock.json`은 그대로 재사용해 주세요. 전면 재작성은 필요 없습니다.

백엔드는 제가 exact-68 operation까지 올려 뒀고, same-origin `/api` acceptance는 38개로 고정했습니다. Seed와 reset, typed client도 준비돼 있으니 복사하거나 우회하지 말고 그대로 쓰시면 됩니다.

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

## 화면 개수와 배치

지금 route는 8개입니다. Overview, 투자 원칙, 모의주문 검토, Automation, Signal·모델 평가, 백테스트, RAG, 리포트. **이 개수를 늘리지 말아 주세요.** 새 기능이 생기면 새 화면을 파는 대신 해당 화면 안에서 자리를 찾는 쪽이 좋겠습니다. 화면이 늘어날수록 "필요한 게 어디 있는지 모르겠다"가 빨리 옵니다.

화면마다 1차 정보 하나를 정하고 그걸 위쪽 왼편에 두시면 됩니다. 나머지는 2차입니다.

| 화면 | 1차 | 2차 |
|---|---|---|
| Overview | 지금 상태와 blocker | 최근 판단, 최근 주문 |
| 투자 원칙 | 활성 원칙의 규칙 | 프리셋, 수정 이력 |
| 모의주문 검토 | 주문 의도와 위험 판정 | 잔고, 매수가능금액 |
| Automation | 정책 세 값과 status | positions, runs |
| Signal·모델 평가 | 종목별 신호 | 모델 메타 |
| 백테스트 | 성과 지표 표 | 구간별 상세 |
| RAG | 답변과 근거 | 소스 목록 |
| 리포트 | 저널 | 내보내기 |

정렬은 왼쪽 기준선 하나로 통일해 주세요. 숫자만 오른쪽 정렬이고, 표 안에서 단위와 소수 자릿수는 열마다 고정입니다. 카드 안에 카드를 넣지 않는 편이 좋겠습니다. 여백은 4의 배수 한 벌만 쓰시고 화면마다 다른 값이 섞이지 않게 해 주세요.

## 참고하실 만한 화면

AI로 뽑은 시안은 대체로 같은 얼굴을 하고 있습니다. 보라색 그라데이션 hero, 유리 카드, 과한 라운드, 이모지 아이콘, 큰 여백에 정보는 적은 화면. 그런 방향이 아니라 아래 다섯 곳을 참고해 주시면 좋겠습니다. 실제로 매일 쓰이는 서비스들이고, 지금 우리 화면이 필요한 성격과 가깝습니다.

- **Linear** (linear.app) — 절제와 밀도. 색을 거의 쓰지 않고 타이포 위계와 간격만으로 구조를 잡습니다. 모션은 짧고 목적이 분명합니다.
- **Stripe Dashboard** (dashboard.stripe.com) — 금융 숫자 표시의 기준. tabular numerals, 통화·단위 표기, 상태 색 규칙을 그대로 배울 만합니다.
- **Mercury** (mercury.com) — 뱅킹 톤. 차분한 팔레트에서 잔고와 거래 내역을 어떻게 읽히게 하는지 보시면 좋습니다.
- **Vercel Dashboard** (vercel.com/dashboard) — 위계와 빈 상태. 데이터가 없을 때 화면이 무너지지 않게 만드는 방식이 참고됩니다.
- **TradingView** (tradingview.com) — 도메인 밀도. 정보가 많아도 조잡해지지 않게 묶는 방식을 보시면 좋습니다. 다만 차트 장식은 우리 화면에 그대로 가져오지 말아 주세요.

그대로 베끼시라는 뜻은 아니고, 밀도와 정렬 감각을 이쪽에 맞춰 주시면 됩니다.

## API는 이만큼만

화면에서 쓸 operation은 [p1-team-a-acceptance.v2 catalog](../../contracts/catalogs/p1-team-a-acceptance.v2.json)의 38개로 고정입니다. 여기 없는 operation은 백엔드에 있어도 호출하지 말아 주세요. root에 68개가 있지만 나머지는 Owner 운영용이거나 RAG 화면 전용이라 화면에서 부르면 권한에서 막히거나 의미 없는 응답이 옵니다.

화면 하나가 쓰는 operation 수도 최소로 가 주시면 좋겠습니다. 같은 데이터를 두 화면에서 각각 부르지 말고 route 진입 시 한 번만 부른 뒤 공유하시고, 목록과 상세를 같이 부르는 대신 목록에서 넘어갈 때 상세를 부르는 쪽이 낫습니다. 폴링은 넣지 말아 주세요. status가 바뀌는 시점은 사용자가 버튼을 눌렀을 때뿐입니다.

정리하면 이렇습니다.

- 38개 catalog 밖 호출 0
- 화면 진입 1회 fetch, 같은 응답 재사용
- 자동 새로고침·폴링·백그라운드 재요청 0
- Automation arm 요청은 UI에서 0 (acceptance에서만 1회)

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
