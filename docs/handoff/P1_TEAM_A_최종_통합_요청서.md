# Team A 최종 요청서

안녕하세요. Dashboard는 이 문서 하나만 보고 진행해 주시면 됩니다.

## 한 줄 요청

현재 동작하는 API 연결은 살리고, 기존 9개 화면을 깔끔한 금융 서비스처럼 다시 정리한 뒤 빠진
사용자 흐름만 끝까지 연결해 주세요.

```text
로그인 → 원칙 설정 → 주문 검토·모의주문 → 자동운용 확인 → RAG·학습일지
```

새 route나 backend를 만드는 작업은 아닙니다. 이미 있는 화면과 component는 쓸 만한 부분만
재사용해도 됩니다.

## 이번에 완성할 것

### 1. 9개 화면 정리

현황, 내 원칙, 자동운용, 주문 검토, 모델 비교, 백테스트, 금융 가이드, 보고서, 설정 화면을 하나의
제품처럼 통일해 주세요.

원하는 느낌은 화려함보다 **신뢰감 있고 읽기 쉬운 금융 서비스**입니다. 한 번의 AI prompt로 만든
카드 템플릿처럼 보이지 않고, 실제 사용 흐름을 사람이 직접 다듬은 듯한 완성도가 필요합니다.

- 정보의 중요도에 따라 제목·요약·상세를 분명히 구분
- 표, 단계 표시, 상세 패널을 화면 목적에 맞게 사용
- 상태와 숫자는 모든 화면에서 같은 방식으로 표현
- 긴 한국어, 빈 데이터, 오류, 많은 후보와 citation에서도 깨지지 않게 처리
- desktop과 mobile에서 주요 기능 사용 가능

gradient, glassmorphism, neon, AI sparkle, 과도한 둥근 카드와 불필요한 animation은 피해주세요.

별도의 Figma, 편집 가능한 디자인 원본, 디자인 발표자료는 필요하지 않습니다. 구현 코드 안에서
공통 색상·간격·글자·button·table·badge를 일관되게 정리하고 실제 화면을 잘 완성하면 됩니다.

### 2. 빠진 사용자 흐름 연결

- **원칙:** 프리셋으로 생성, 값 수정, 새 버전 저장, 충돌 안내, Kill Switch 상태 확인
- **주문:** 종목·방향·수량·가격 입력 → Risk 판정 → 명시적 KIS 모의주문 → 상태·취소·체결 확인
- **Automation V3:** 예산, 손절·익절, 보유 세션, ATR, MODEL_SELL, AI ON/OFF와 thinking level 설정
- **자동운용 상세:** blocker, run, position, 부분체결, 대사 상태, 청산 이유와 검증된 AI 근거 표시
- **RAG·학습일지:** 동의·철회, 답변·인용·피드백, Decision·주문·run 연결 저장과 목록 조회

HOLD/BLOCK이면 주문 버튼을 막고, WARN이면 사용자가 경고를 확인한 뒤 한 번만 제출해야 합니다.
자동 시작·자동 재주문·자동 INTERNAL_PAPER 전환은 만들지 않습니다.

## 꼭 지켜 주세요

- same-origin `/api`, 현재 9개 route, generated client와 기존 테스트 유지
- loading, empty, error, stale, ABSTAIN, HALTED 상태를 숨기지 않음
- 숫자는 tabular numerals와 고정 단위로 정렬
- keyboard navigation과 WCAG AA 수준의 대비
- `REAL_TEAM_B`와 `SYNTHETIC_GOLDEN`, `KIS_MOCK`과 `INTERNAL_PAPER`, `RESEARCH_ONLY`를 구분
- 후보, 수량, ATR, 주문가격, Risk 판정을 브라우저에서 다시 계산하지 않음
- 서버의 `canArm`, blocker, citation 검증 결과를 그대로 표시
- 계좌번호, token, raw provider 응답을 화면이나 로그에 남기지 않음

## Team A가 하지 않는 일

Spring·Python·DB·OpenAPI·Compose, 모델 학습, Team B 산출물, KIS·Vertex credential과 외부 호출은
Owner가 담당합니다. API가 막혀 있으면 임시 mock이나 우회 코드를 만들지 말고 요청·응답과 blocker만
알려 주세요.

## 완료 확인

```bash
./capstone team-a acceptance

cd workspaces/experience-dashboard
npm ci
npm run typecheck
npm run lint
npm test
npm run build
```

완료 기준은 간단합니다.

- 9개 화면의 디자인과 상태 표현이 일관됨
- 위 사용자 흐름이 실제 API와 동적 ID로 이어짐
- Team A acceptance exact-45 통과, Playwright skip 0
- production 가짜 응답 0, blocker가 있으면 Start disabled
- 사용자 동작 한 번에 write 호출 한 번

완료 후에는 PR URL, commit SHA, 테스트 결과, 대표 desktop·mobile 화면, 남은 blocker만 보내 주세요.
별도 디자인 문서나 발표자료는 필요하지 않습니다.

## 구현 중 필요할 때만 보는 기술 참고

- [`p1-team-a-acceptance.v3` exact-45](../../contracts/catalogs/p1-team-a-acceptance.v3.json)
- [generated V3 client](../../workspaces/experience-dashboard/src/shared/api/generated/p1-team-a-client.v3.ts)
