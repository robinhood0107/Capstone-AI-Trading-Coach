# MARS DEMO 화면·컨트롤 대조표

DEMO는 FULL 화면을 별도 디자인으로 다시 그리지 않습니다. `AppShell`, 로그인 소개, `OverviewView`, `PrinciplesView`, `StrategyView`, `AutomationView`, `OrderReviewView`, `RagGuideView`, `JournalView`, `ReportView`, `AdminConsole` 등 FULL 컴포넌트를 그대로 마운트합니다. 데이터 호출은 같은 저장소 안의 DEMO 전용 `client.ts`와 `/api/v1/*`, `/api/v2/*`, `/api/v3/*` route adapter가 받아 fixture와 방문자별 SQLite overlay에서 응답합니다. 브라우저에서 FULL API 호스트, KIS, PostgreSQL 또는 Redis로 요청하지 않습니다.

## 종단 동선

1. FULL의 MARS 소개·로그인 프레임에서 `로그인` 버튼 한 번으로 무작위 서명 세션 쿠키를 발급합니다. 아이디·비밀번호·OAuth는 입력하지 않습니다.
2. FULL 현황과 상단 상태바에서 계좌 요약, 자동운용 상태, 보유 종목과 거래 내역을 확인합니다.
3. FULL 원칙 화면에서 preset 또는 한도를 바꾸고 저장합니다. 변경은 현재 방문자 overlay에만 적용됩니다.
4. FULL 자동운용 화면에서 정책·위험 기준을 검토하고 arm/disarm 합니다. 주문 검토는 FULL `OrderTicket`을 그대로 사용하며 제출 결과는 DEMO 세션 원장에만 남습니다.
5. FULL 금융 Agent 화면에서 외부 처리 동의 뒤 자유 질문을 보냅니다. 서버가 공개 근거를 검색하고 DEMO 전용 Vertex 자격으로 답변을 생성합니다.
6. FULL 학습일지와 보고서 화면에서 사건을 검색하고 방문자별 메모를 저장하거나 결과물을 내려받습니다.

## 노출 컨트롤 전수 조사

| FULL 화면·컨트롤 | 판정 | DEMO 결과 |
|---|---|---|
| MARS 로그인 `로그인` | `동작` | 공통 FULL 로그인 프레임에서 무작위 DEMO 쿠키를 발급합니다. 아이디·비밀번호 입력은 렌더하지 않습니다. |
| FULL 소개의 `대시보드 바로 보기`·`어떻게 막는지 보기`·`시작하기`·본문 이동 | `동작` | 공통 FULL `IntroExperience`가 같은 위치로 스크롤합니다. 세션 발급은 로그인 버튼에서만 합니다. |
| FULL 테마 토글 | `동작` | 밝게·어둡게·시스템 테마를 바꿉니다. |
| FULL `AppShell` 사이드바·모바일 메뉴·UtilityNav·새로고침·로그아웃 | `동작` | 같은 route와 같은 상태바를 사용합니다. 로그아웃은 현재 쿠키만 만료합니다. |
| FULL 현황 자동 갱신·주문 상태·종목 행·Agent 바로가기 | `동작` | FULL `OverviewView`가 fixture projection과 현재 세션 overlay를 표시하고 5초마다 새로 조회합니다. |
| FULL 원칙 preset·임계값·규칙 on/off·`변경 사항 저장`·되돌리기 | `동작` | 같은 `PrinciplesView`와 endpoint contract를 사용합니다. version과 규칙을 현재 방문자 overlay에 저장하며 FULL 데이터베이스는 변경하지 않습니다. |
| FULL 전략 검증 tab, 모델 종목 선택, 백테스트 지표·차트·필터 | `동작` | 같은 FULL `StrategyView`, `ModelEvaluationView`, `BacktestReportView`가 version 고정 receipt로 계산한 값을 읽습니다. 아직 근거가 없는 모델은 `ABSTAIN`으로 처리합니다. |
| FULL 자동운용 preset·정책 입력·청산 기준·자본 재투자·중지·arm/disarm·실행 상세 | `동작` | 동일한 FULL `AutomationView`가 세션별 설정 overlay를 변경합니다. 자동화·KIS API 작업은 시작하지 않고 주문·현금·포지션 계산은 fixture event log와 같은 세션 overlay에서 처리합니다. |
| FULL 주문 검토 종목·매수/매도·수량·예상가·원칙 평가·확인·제출·취소 | `동작` | 동일한 FULL `OrderTicket`을 사용합니다. `evaluateOrder`, mock buyable, submit/cancel contract는 DEMO route adapter 안에서 처리하고 세션 원장에 기록합니다. 네트워크로 KIS나 FULL API에 보내지 않습니다. |
| FULL 최근 주문 판정·체결 상세·읽기 전용 링크 | `동작` | 현재 공개 가능한 event projection만 표시하며 다른 방문자 메모·주문은 조회되지 않습니다. |
| FULL RAG 외부 처리 동의·철회 | `동작` | 공통 RAG UI가 adapter에서 받은 DEMO Vertex 처리자·고지 문구를 표시하고 동의를 현재 세션에 기록합니다. 동의 전 질문은 Vertex로 보내지 않습니다. |
| FULL 자유 질문·짧게/자세히·질문 예시·`물어보기` | `설명 후 제한` | 같은 `RagGuideView`가 서버 adapter를 호출합니다. 세션 5회/일, 전체 50회/일, 동시 1건, 분당 요청·입력 byte·출력 token 한도를 적용합니다. 자료 검색은 이미지 안의 공개 근거 인덱스만 사용합니다. |
| Agent 응답·인용·질문 사용량·비용 추정 | `동작` | 실제 응답 token/cost가 있으면 표시하고 예약 상방을 함께 보여 줍니다. 서비스 계정이 없으면 provider에 요청하지 않고 사용량 0으로 실패 상태를 표시합니다. |
| FULL 학습일지 검색·추가·수정·삭제·연결 사건 | `동작` | 동일한 `JournalView`가 최대 25개 메모를 방문자별 named-volume overlay에 보관합니다. event log와 메모를 합쳐 기록하지 않습니다. |
| FULL 보고서 차트·상세·`인쇄 / PDF` 및 내려받기 | `동작` | 동일한 `ReportView`가 시장자료 receipt와 event projection에서 읽습니다. 파일에는 검증용 source hash·기간·가정이 남습니다. |
| FULL 설정의 설명 방식·서비스 상태 | `동작` | 공통 `ExplainModeSettings`와 `SystemHealthView`를 사용합니다. |
| KIS App Key·Secret·계좌번호 입력, 계좌 로그인 설정, 개인 Vertex key 입력 | `제거` | FULL 제품 구성요소를 DEMO route에서 가져오지 않습니다. 공급자 키와 세션 서명 키는 DEMO secret file에서만 읽습니다. |
| FULL AdminConsole의 운영 상한 변경·공용 Vertex 전환·계정 역할 변경 | `제거` | FULL `AdminConsole`은 읽기 전용 모드로 마운트합니다. 정적/현재 세션 정보 검색과 페이지 이동만 동작하며 변경 요청 route는 거부합니다. |
| 실시간 KIS 잔고·주문·체결 API와 FULL API host | `제거` | 이미지 adapter는 같은 origin의 DEMO route에만 응답합니다. KIS 물리 요청과 FULL API host 요청은 없습니다. |
| 실시간 뉴스·공시·외부 검색 | `제거` | 새 공급자 연결은 적용하지 않습니다. 기사·시세의 외부 수집 호출은 0입니다. |

화면에서 보이는 버튼은 실제 FULL 컴포넌트의 동작을 그대로 따릅니다. product adapter는 읽기 계약을 fixture로 처리하고 쓰기 계약은 방문자 overlay에만 반영하며, 미구현 FULL 관리자 쓰기 route는 403으로 종료합니다.
