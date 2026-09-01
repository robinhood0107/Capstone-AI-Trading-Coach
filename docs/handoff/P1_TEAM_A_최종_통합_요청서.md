# Team A 최종 요청서

안녕하세요. 이 문서 하나만 보시고 Dashboard 작업을 진행해 주시면 됩니다.
이전 Dashboard 요청서와 Automation V3 추가 요청의 필요한 내용은 여기로 합쳤습니다.

## 이번에 부탁드리는 한 가지

현재 Dashboard의 API 연결과 검증된 기능 코드는 재사용하되, 9개 화면의 정보 구조와 시각 디자인은
금융 서비스 수준으로 전면 재설계해 주세요. 동시에 사용자가 아래 흐름을 실제 API로 끝까지 사용할 수
있게 연결해 주세요.

```text
로그인
→ 내 투자 원칙 확인·수정
→ 주문 위험 검토
→ KIS 모의주문 제출·상태·취소·체결 확인
→ 자동운용 정책·상태·근거 확인
→ 필요한 결과를 학습일지에 저장
```

route URL을 늘릴 필요는 없습니다. 대신 현재 화면 배치와 component 모양은 보존 대상이 아닙니다.
정확한 API 호출·상태 처리 코드는 살리고, 9개 화면 전체와 공통 디자인 시스템은 새로 설계해 주세요.

## 디자인 작업 범위

다음 9개 화면을 하나의 완성된 제품처럼 전부 다시 설계합니다.

1. 현황
2. 내 원칙
3. 자동운용
4. 주문 검토
5. 모델 비교
6. 백테스트 리포트
7. 금융 가이드 RAG
8. 보고서 캡처
9. Strong LLM 설정

먼저 현재 9개 화면의 정보 우선순위·중복·사용자 막힘을 짧게 점검하고, 아래 공통 체계를 만든 뒤
화면을 구현해 주세요.

- 색상: 배경·표면·본문·보조문자·border·ALLOW/WARN/HOLD/BLOCK·success/error 토큰
- 글자: 제목·본문·label·숫자·코드의 크기, 굵기, 행간과 tabular numeral 규칙
- 공간: 4/8px 계열 spacing, grid, 최대 폭, desktop/mobile breakpoint
- 공통 component: navigation, page header, card, form, button, badge, alert, table, chart, drawer/modal
- 공통 상태: loading, empty, error, stale, disabled, ABSTAIN, HALTED, partial fill, reconciliation
- 데이터 표현: 통화·비율·시각·상태·출처·인용문의 정렬과 길이 제한

Figma 또는 동등한 편집 가능한 설계 원본에 디자인 토큰, 핵심 component와 9개 화면의 desktop 시안을
남겨 주세요. 핵심 사용자 흐름은 mobile 시안도 포함합니다. 생성형 UI 도구를 쓸 수는 있지만 한 번의
prompt 결과를 그대로 제출하지 말고, 실제 데이터 길이·오류 상태·반응형 화면을 넣어 최소 두 차례
검토한 최종안을 구현해야 합니다.

새 route, 거대한 animation, 장식용 3D, 마케팅 페이지는 만들지 않습니다. 이번 전면 재설계는 화면 수를
늘리는 일이 아니라 기존 9개 화면의 정보 위계·일관성·사용성을 완성하는 일입니다.

## 그대로 살려 주세요

- 현재 Next.js Dashboard와 same-origin `/api` 연결
- 현재 9개 route URL과 검증된 API 호출·상태 전이
- 올바르게 동작하는 form·table·chart 로직과 generated client
- 기존 테스트
- loading, empty, error, stale, ABSTAIN, HALTED 상태를 숨기지 않는 방식

현재 component를 그대로 쓸 의무는 없습니다. 새 디자인 시스템에 맞지 않는 component와 CSS는
교체해도 되지만, 동작하는 API 계약을 시각 개선 때문에 다시 구현하거나 우회하지 마세요.

## 완성할 사용자 흐름

### 1. 내 원칙과 안전장치

- 원칙이 없으면 프리셋으로 새 원칙을 만들 수 있어야 합니다.
- 기존 원칙은 값을 바꾸고 새 버전으로 저장할 수 있어야 합니다.
- 다른 탭에서 먼저 저장한 경우 충돌을 알려 주고 자동으로 덮어쓰지 않습니다.
- Kill Switch의 현재 상태와 변경 결과를 보여 줍니다.

원칙과 Risk 판정을 브라우저에서 다시 계산하지 마세요. 서버가 준 결과를 그대로 설명하면 됩니다.

### 2. 주문 검토와 KIS 모의주문

사용자가 종목·방향·수량·예상가격을 입력하면 다음 순서가 한 화면 흐름으로 이어져야 합니다.

```text
주문 후보 입력
→ Decision/Risk 판정
→ ALLOW·WARN·HOLD·BLOCK과 이유 확인
→ 사용자가 명시적으로 제출
→ 주문 상태 조회
→ 필요하면 미체결 취소
→ 체결 내역 확인
```

- HOLD/BLOCK은 제출 버튼을 막습니다.
- WARN은 경고를 확인한 뒤에만 제출할 수 있게 합니다.
- 주문 제출은 사용자가 버튼을 누른 경우 한 번만 호출합니다.
- KIS 장애를 INTERNAL_PAPER로 자동 전환하지 않습니다.
- 계좌번호, token, raw provider 응답을 화면이나 로그에 남기지 않습니다.

### 3. Automation V3

현재 Automation 화면을 V3 API에 연결하고 아래 항목을 추가해 주세요.

사용자 정책:

- 최대 자동운용 금액
- 손절률·익절률
- 최대 보유 세션. `0`은 “무제한”으로 표시
- ATR 기간과 배수
- MODEL_SELL ON/OFF
- 보수·균형·공격 프리셋과 직접 입력

AI 설정:

- AI 판단 ON/OFF
- thinking level `minimal | low | medium`
- AI OFF: 규칙과 RiskEngine만 사용
- AI ON에서 provider 실패: 이번 run 신규 매수 없음

실행 상태:

- 서버의 `canArm`과 blocker를 그대로 표시
- 시작은 사용자의 최종 확인 뒤 한 번만 호출
- 중지는 기존 disarm 사용
- 자동 시작, polling, 백그라운드 arm 없음

run·position:

- run 목록에서 실제 `runId` 상세를 열 수 있어야 합니다.
- position에 진입가, peak, ATR, trailing stop, 보유 만기와 진입 당시 정책을 표시합니다.
- 청산 이유를 손절, ATR 트레일링, 모델 매도, 익절, 보유 만기로 구분합니다.
- 부분체결과 미해결 대사는 정상 상태와 다른 경고로 보여 줍니다.

AI 근거:

- 후보별 상태, 점수, veto 여부, 판단 이유
- 출처, 날짜, 짧은 인용문, `VERIFIED` 여부
- 오래된 근거의 경고
- 실제 Grounding metadata가 있는 run에서만 Google 검색 정보를 표시

화면은 판단 결과를 보여 주기만 합니다. 후보, 수량, ATR, 주문가격, RiskEngine 결과를 다시 계산하지
마세요.

### 4. RAG와 학습일지

- 외부 처리 동의·철회 상태를 보여 줍니다.
- 답변, 인용, guardrail 경고와 피드백을 표시합니다.
- RAG 답변, Decision, 주문, 자동운용 run을 학습일지에 연결해 저장할 수 있게 합니다.
- 학습일지 목록과 연결된 항목을 다시 볼 수 있게 합니다.

RAG 답변은 설명용이며 주문 판단을 바꾸지 않는다는 문구를 유지해 주세요.

## 화면에서 지켜야 할 것

- 숫자는 tabular numerals와 고정 단위로 정렬
- 상태 색은 화면 전체에서 동일하게 사용
- desktop과 mobile에서 주요 버튼과 표를 사용할 수 있어야 함
- keyboard navigation과 WCAG AA 수준의 대비
- gradient, glassmorphism, neon, AI sparkle, 과한 애니메이션은 사용하지 않음
- 카드만 반복하는 생성형 템플릿처럼 보이지 않도록 화면 목적에 맞는 표·단계·상세 패널을 사용
- 실제 긴 한국어 문구, 0건, 오류, stale, 31개 후보, 다수 citation을 넣어 깨짐 확인
- `REAL_TEAM_B`와 `SYNTHETIC_GOLDEN`, `KIS_MOCK`과 `INTERNAL_PAPER`, LightGBM의
  `RESEARCH_ONLY`를 명확히 구분

현재 navy·white 톤을 그대로 복제할 필요는 없지만, 신뢰감 있는 금융 서비스 톤과 절제된 색을
유지해 주세요. 새 디자인 시스템은 9개 화면에서 실제 같은 component와 token으로 사용되어야 합니다.

## Team A가 하지 않는 일

- Spring, Python, DB migration, OpenAPI, Compose 수정
- 모델 학습, 백테스트 계산, Team B 산출물 생성
- KIS·Vertex credential 설정 또는 실제 외부 호출
- 후보 선정, 주문 수량, LIMIT 가격, ATR, RiskDecision 계산
- 새 endpoint, mock server, production 가짜 응답 추가
- 서버 blocker를 숨기거나 클라이언트에서 `canArm=true`로 바꾸기
- 자동 arm, 자동 주문 재시도, 자동 INTERNAL_PAPER fallback

막힌 API나 계약 문제가 있으면 우회 코드를 만들지 말고 blocker와 요청·응답 모양만 Owner에게 알려
주세요. Owner가 backend를 수정합니다.

## 완료 확인

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

완료 기준:

- Team A acceptance exact-45 통과
- Playwright skip 0
- production fake response 0
- 9개 화면 모두 새 디자인 시스템 적용
- Figma 또는 동등한 설계 원본과 구현 화면이 일치
- 주요 네 흐름을 동적 ID로 확인
- blocker가 있으면 Start disabled
- 명시적 사용자 동작 한 번에 write 한 번
- 기존 화면과 테스트 회귀 없음

## 완료 후 보내 주세요

- PR URL과 commit SHA
- 변경 파일 목록
- `package-lock.json` SHA-256
- 위 테스트 결과
- 디자인 토큰·공통 component 목록과 편집 가능한 설계 원본 링크
- 9개 화면 desktop 캡처와 핵심 흐름 mobile 캡처
- 1차 시안에서 무엇을 고쳤는지 짧은 비교
- 아직 남은 blocker

production image 생성과 배포, 공급망 검증은 Owner가 진행합니다.

## 기술 참고

구현 중 정확한 method·path·type이 필요할 때만 아래 두 파일을 보시면 됩니다. 별도의 추가 요청서는
아닙니다.

- [`p1-team-a-acceptance.v3` exact-45](../../contracts/catalogs/p1-team-a-acceptance.v3.json)
- [generated V3 client](../../workspaces/experience-dashboard/src/shared/api/generated/p1-team-a-client.v3.ts)
