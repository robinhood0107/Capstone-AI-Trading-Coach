# FULL 상태 전이와 DB 귀속 검증표

기준일: 2026-09-27 · 검증 후보: V218 · 검증 스택: 복제 DB의 `http://localhost:3012`

이 표는 사용자가 누르는 **서버 상태 변경 버튼**이 어떤 요청과 DB 상태를 바꾸는지 정리합니다. 페이지 이동, 표 펼치기, 보기 선택처럼 서버 상태를 바꾸지 않는 조작은 DB 변경 없음으로 구분합니다. 외부 KIS 주문은 모의계좌라도 실제 계좌 원장을 바꾸므로, 선택 항목인 1주 주문 시험은 실행하지 않았습니다.

## demo-user 계좌와 자동운용 데이터 귀속

| 데이터 | V218 적용 후 귀속 | 화면/시작 동작 |
|---|---|---|
| KIS 계좌 | App Key/계좌 원문 대신 owner별 KEK 기반 전체 계좌 식별 지문에 기존 `account_id`를 결속. 같은 실제 계좌를 다시 저장하면 동일 ID 사용 | KIS 키의 끝 4자리와 연결 상태만 표시. 끝 4자리만으로 계좌를 합치지 않음 |
| 현재 KIS 잔고와 검증된 체결 이력 | 읽기 잔고 스냅샷과 일치한 현재 KIS 계좌에만 귀속. 현재 잔고는 055550 45주이며, 이전 스냅샷에 증거가 있는 과거 KIS 포지션·체결은 같은 계좌 ID로 이어짐 | 열린 포지션은 `1 / 10`; 연결 확인은 읽기 전용이며 인증 영수증이나 `CERTIFIED`를 만들지 않음 |
| Offline history replay 10건 | `INTERNAL_PAPER`의 `HISTORICAL_PAPER`; 5 OPEN, 5 CLOSED의 수량·손익 보존 | 현재 KIS 포지션 수, 재투자, 자동매도에서 제외하고 내부 모의 계좌 이력으로 표시 |
| Team A 수용 시험 1건 | 충돌하던 paper account ID를 분리해 전용 `INTERNAL_PAPER` 계좌에 귀속. NEWS_VETOED 실행과 닫힌 005930 fixture를 보존 | 과거 paper 이력으로만 표시; KIS 잔고·포지션에 합산하지 않음 |
| 미대사 KIS 주문 005930 매수 1주 | 원래 SUBMITTED·체결 0주 행을 수정·취소·매도 처리하지 않고 격리 이벤트로 기록. reconciliation 확인 전 시작 차단 | `다른 계좌 미대사`에 주문 1건, 포지션 0건, 실행 0건; 자동운용 시작 비활성 |
| 자동운용 상태/예약 | `automation_control`은 `DISARMED v21`; V218에서 미래 ARMED 예약을 해제하고 기록 | 런타임을 켜도 demo-user를 자동 시작하지 않음. 미대사 주문 해소 전까지 시작 차단 |

검증 복제본 DB 결과: KIS VERIFIED 포지션 1 OPEN + 2 CLOSED, 내부 paper 포지션 5 OPEN + 6 CLOSED, paper 실행 70건(오프라인 replay 69 + Team A 1), 미연결 포지션 0건, 미대사 주문 1건, ARMED 예약 0건. 이는 원래 상태를 구분해 보존한 결과이며, 과거 모의 포지션을 현재 보유 주식인 것처럼 바꾸지 않습니다.

배포 전 실제 DB 읽기 확인: Flyway V216, demo-user `DISARMED v21`, runtime `false`였습니다. 다만 control은 정지인데 demo-user의 예약 행 하나가 `ARMED`로 남아 있었습니다. V218은 이 예약을 해제합니다. 배포 후 운영 runtime을 켜기 전에 예약 `ARMED 0건`과 demo-user `DISARMED`를 다시 확인합니다.

## 상태 변경 버튼과 영속 효과

| 화면 / 버튼 | 요청 | 성공 시 DB 상태 변화 | 화면에서 확인할 결과 | 검증 |
|---|---|---|---|---|
| 가입하기 | `POST /api/v1/auth/signup` | 사용자와 암호 로그인 식별자 생성 | 로그인된 새 사용자 | 전체 제품 E2E |
| 원칙 시작·변경 저장 | 원칙 owner API | `principles` 현재 버전과 `principle_versions` 이력 추가; 이전 버전 불변 | 현재 버전 번호 증가 | 전체 제품 E2E: 생성·수정 |
| 학습일지 저장·수정·삭제 | `/api/v1/journals` owner API | 소유자 journal 생성/버전 갱신/삭제 표시; 링크는 소유권 검사 | 목록과 선택 상태 갱신 | 전체 제품 E2E: 생성·수정·삭제 |
| 내 주문 즉시 중지 / 중지 해제 | `POST /api/v2/risk/kill-switch` | owner별 `owner_kill_switch` generation과 append-only 이벤트 갱신; 해제는 주문을 실행하지 않음 | 켜짐/꺼짐과 변경 시각 | 전체 제품 E2E: 켜기·해제 |
| 전체 주문 즉시 중지 / 전역 중지 해제 (관리자) | `POST /api/v1/risk/kill-switch` | 시스템 전역 중지 상태와 이벤트 갱신; 개인 중지·자동운용 예약과 별도 | 전역 상태 문구와 버튼 반전 | 보강 E2E: 켜기·해제 |
| 자동운용 정책 저장 | `PUT /api/v3/automation/policy` | `automation_policy_versions`에 새 owner·계좌 정책 버전 추가; 실행 전 저장 상태와 applied version은 구분 | 새 정책 버전, 미적용 상태 표시 | 전체 제품 E2E |
| 재투자 설정 저장 | `PUT /api/v4/automation/capital-policy` | `automation_capital_policy_versions_v1`에 예상 버전 CAS로 새 설정 추가; 다음 유효 거래 세션부터 적용 | 저장 뒤 버전과 적용 시작 세션 표시 | 보강 E2E: OFF→ON 두 버전 저장 |
| 자동운용 시작 | `POST /api/v3/automation/arm` | 성공 시 `automation_control`을 ARMED로 전이하고 해당 owner·계좌·정책 버전의 예약 생성; 주문은 런타임이 켜지고 유효 세션이 와야 가능 | 차단 이유가 있으면 실제 blocker와 함께 비활성 | demo-user는 미대사 주문 때문에 비활성; 해당 계정에서 누르지 않음. SQL/통합 게이트와 API 상태 확인 |
| 자동운용 정지 | `POST /api/v1/automation/disarm` | control DISARMED, 미래 ARMED/CLAIMED 예약 HALTED, 정지 이벤트 기록; 과거 실행·포지션은 변경하지 않음 | 정지 상태 및 이력 유지 | DB 통합 및 V218 복제 DB 검사; demo-user는 이미 DISARMED |
| KIS 정보 저장·교체 | `PUT /api/v1/brokerage/mock/credential` | 키는 암호화 저장, credential revision 증가; 동일 계좌 지문이면 기존 account ID 재사용. 자동운용 중이면 거부 | 비밀값은 재표시하지 않고 끝 4자리만 표시 | 전체 제품 E2E: fake credential 저장 및 자동운용 guard |
| 읽기 연결 확인 | `POST /api/v1/brokerage/mock/credential/connect` | 실제 KIS 잔고를 읽어 balance observation과 credential revision 결속 증명 저장. 주문 없음 | 성공 시 현금·보유 개수·계좌 끝 4자리; 실패 시 원인과 `저장됨` 상태 | 이전 demo-user의 실제 잔고 증거 재사용; E2E fake key 실패 사유 |
| 선택: 모의주문 시험 | KIS 인증 API | 1주 주문 시도·접수·체결·잔고 대사 증거 기록; 읽기 연결과 별도 | 통과 때만 주문 경로 검증 표시 | **미실행**: 계획상 선택 기능이며 실제 모의계좌 주문 이력이 됨 |
| KIS 연결 해제 | 연결 삭제 API | 미체결·대사 상태에 따라 삭제 완료 또는 DISCONNECTING 유지; 과거 주문·포지션 이력 삭제 금지 | 서버의 완료/대기 결과를 그대로 표시 | 전체 제품 E2E: 가짜 계좌 삭제 완료 흐름 |
| 내 Vertex 키 저장·삭제 | `PUT /api/v2/strong-llm/settings` | 사용자 전용 암호화 자격증명 추가/삭제; 관리자는 타 사용자 비밀값을 읽지 못함 | 저장 여부·키 ID 일부만 표시, 본문은 비공개 | 전체 제품 E2E: 가짜 키 저장·삭제; 실제 QA 호출 후 삭제 확인 |
| 공용 Vertex 허용 스위치 (관리자) | `PUT /api/v1/admin/ai/operator-fallback` | 공용키 대체 허용 상태 및 변경 감사 기록 갱신 | 개인 키 우선, 허용 시에만 공용 키 대체 | 보강 E2E: OFF/ON 원상 복귀 |
| Agent 동의·철회 | `POST /api/v2/rag/consents` | owner별 동의 효력 기록; 철회 후 질문 전송 차단 | 동의 완료/필요로 바뀌고 답변 생성 버튼 게이트 갱신 | 보강 E2E: 동의·질문·철회 |
| Agent 질문 | `POST /api/v2/rag/ask` | owner 암호화 `rag_v2_answer_history`; 실제 LLM 호출은 `agent_ai_usage_events`에 `OWNER` 또는 `OPERATOR`로 구분 | `ANSWERED`는 설명, `RETRIEVAL_ONLY`는 설명 없이 검증된 근거만 표시 | demo-user QA: 개인 키 호출 1회 `ANSWERED`; 공용 키 호출 1회 `RETRIEVAL_ONLY`·근거 1건; 각 출처 이벤트 확인 |
| 관리자 사용자 검색·권한·정지 | `/api/v1/admin/users/{id}/access` | 대상 사용자 역할/상태 변경. self 계정은 변경 불가 | 사용자/관리자·사용 중/정지 상태 반영 | 전체 제품 E2E: 테스트 사용자 승격·복구·정지·복구 |
| 서비스 상한 저장 | `PUT /api/v1/admin/limits` | 가입자·동시 자동운용 상한 설정 저장 | 저장된 값 유지, 새 가입/새 시작 게이트에 반영 | 보강 E2E: 현재 값을 재저장하고 값 유지 확인 |

## DB를 바꾸지 않는 조작과 실행하지 않은 경로

- 페이지 이동, 탭/선택 보기, 정책 프리셋 선택, 정렬·검색 입력은 서버 데이터 변경이 없습니다. 검색 버튼만 관리자 사용자 목록 조회를 다시 합니다.
- Agent의 답변 기록 접기/펼치기는 읽기이며, 개별 이력 삭제는 확인 대화상자 뒤 owner 전용 DELETE입니다. V2 Agent에는 피드백 버튼이 없습니다.
- Google·Kakao 연결은 이미 demo-user에서 연결된 상태를 확인했으므로 다시 연결하지 않았습니다.
- 모의계좌 실제 주문, demo-user 자동운용 시작, 미대사 005930 주문 변경은 실행하지 않았습니다. 각각 선택 주문, 기존 주문 원장 변경, unresolved-order guard에 해당합니다.

## 반복 검증 명령과 결과

- `npm run test:e2e:full-product`: 3 passed. demo-user 관리자 쓰기 허용 설정으로 실행해 상한 저장, 전역 주문 중지/해제, 개인 Vertex 등록/삭제를 포함합니다.
- 이 E2E는 새 가입자와 demo-user 두 역할로 로그인 폼, 앱의 모든 route, 원칙·일지, 개인 킬스위치, 자동운용 정책/재투자, KIS 저장·연결 실패·삭제, Agent 동의·질문·철회, 관리자 권한·사용자 상태·상한·공용 Vertex를 검사합니다.
- 별도 실제 provider 호출은 복제 DB에서 개인 키 1회와 공용 키 1회만 수행했습니다. 개인 호출은 `ANSWERED`; 공용 호출은 `RETRIEVAL_ONLY`로 근거는 1건 찾았으나 설명 문장은 만들지 않았습니다. 두 번째 결과를 생성 답변 성공으로 집계하지 않습니다.
- 실계좌 KIS 주문 경로의 접수·체결·잔고 대사는 아직 관찰하지 않았습니다. demo-user가 버튼으로 시작할 수 있기 전, 미대사 주문 해소가 필요합니다.
