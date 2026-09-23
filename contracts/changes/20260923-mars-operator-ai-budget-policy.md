# MARS 운영자 AI 한도 설정 계약

full 제품의 절대 상한은 NAS의 비공개 `MARS_AI_DAILY_HARD_CAP_USD`로 주입한다.
값이 없거나 양의 USD 금액(센트 단위)이 아니면 full API는 기동하지 않는다. 사용자가
첫 절대 상한은 `$1.00/일`로 정했다. NAS의 비공개 환경에도 이 값을 주입한다.

Google ADMIN만 `GET/PUT /api/v1/admin/ai-budget`으로 일일 soft cap을 확인·변경한다.
PUT은 센트 정수와 직전 revision만 받으며, NAS 상한 초과와 경쟁 갱신을 거부한다.
DB는 현재 Google identity와 security version·ADMIN role을 다시 검사한다. USER에게
ADMIN 화면은 보이지 않으며 API·DB에서도 거부한다. V200의 seed는 0이고,
V203은 아직 수정하지 않은 새 설치만 [첫 1달러 정책](20260923-mars-ai-first-dollar-policy.md)으로 올린다.

V200은 정책과 감사 행을 추가한다. 이 변경은 설정 표면이며, Agent·매매 AI의 실제
비용 예약/차단 원장은 후속 변경에서 모든 과금 provider 경로에 연결한다. 그 연결과
검증이 끝나기 전까지 공개 AI 호출을 열 수 없다. root local API 계약은 유지하며
full 전용 schema는 `contracts/openapi/mars-full-operator-ai-budget.v1.openapi.json`이다.
