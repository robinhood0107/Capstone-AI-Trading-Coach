# Team A 최종 요청서

안녕하세요. 이 문서 하나만 보시고 Dashboard 작업을 진행해 주시면 됩니다.
이전 Dashboard 요청서와 Automation V3 추가 요청의 필요한 내용은 여기로 합쳤습니다.

## 이번에 부탁드리는 한 가지

현재 Dashboard 구조와 코드를 유지하면서, 사용자가 아래 흐름을 실제 API로 끝까지 사용할 수 있게
연결해 주세요.

```text
로그인
→ 내 투자 원칙 확인·수정
→ 주문 위험 검토
→ KIS 모의주문 제출·상태·취소·체결 확인
→ 자동운용 정책·상태·근거 확인
→ 필요한 결과를 학습일지에 저장
```

화면 전체를 다시 디자인하거나 route를 새로 만들 필요는 없습니다. 현재 9개 route와 component를
최대한 재사용해 주세요. 기능을 읽기 쉽게 정리하는 정도의 UI 보완이면 충분합니다.

## 그대로 살려 주세요

- 현재 Next.js Dashboard와 same-origin `/api` 연결
- Overview, 원칙, 주문 검토, 자동운용, 모델 비교, 백테스트, RAG, 보고서, 설정 화면
- 현재 navy·white·neutral gray 톤과 표·차트
- generated client와 기존 테스트
- loading, empty, error, stale, ABSTAIN, HALTED 상태를 숨기지 않는 방식

Figma나 v0로 전면 시안을 새로 만들 필요는 없습니다. 배치 판단이 필요한 부분만 간단한 와이어프레임
또는 구현 화면 캡처로 설명해 주세요.

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
- `REAL_TEAM_B`와 `SYNTHETIC_GOLDEN`, `KIS_MOCK`과 `INTERNAL_PAPER`, LightGBM의
  `RESEARCH_ONLY`를 명확히 구분

새 디자인 시스템을 만들거나 모든 화면을 다시 배치할 필요는 없습니다.

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
- 주요 네 흐름을 동적 ID로 확인
- blocker가 있으면 Start disabled
- 명시적 사용자 동작 한 번에 write 한 번
- 기존 화면과 테스트 회귀 없음

## 완료 후 보내 주세요

- PR URL과 commit SHA
- 변경 파일 목록
- `package-lock.json` SHA-256
- 위 테스트 결과
- desktop·mobile 주요 흐름 캡처
- 아직 남은 blocker

production image 생성과 배포, 공급망 검증은 Owner가 진행합니다.

## 기술 참고

구현 중 정확한 method·path·type이 필요할 때만 아래 두 파일을 보시면 됩니다. 별도의 추가 요청서는
아닙니다.

- [`p1-team-a-acceptance.v3` exact-45](../../contracts/catalogs/p1-team-a-acceptance.v3.json)
- [generated V3 client](../../workspaces/experience-dashboard/src/shared/api/generated/p1-team-a-client.v3.ts)
