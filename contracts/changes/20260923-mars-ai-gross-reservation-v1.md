# MARS 운영자 AI 공개가격 기준 예약 원장

V201은 `Asia/Seoul` 날짜의 운영자 AI 최대 노출액을 한 원장에 원자 예약한다.
`DEMO_AGENT`, `FULL_AGENT`, `TRADE_AI`, `RAG_VERTEX`, `RAG_VOYAGE`가 같은 합계를
공유한다. 각 요청은 provider 호출 전에 공개 가격 기준 최대 비용을 예약하며, 잔액
부족·DB 장애·중복 예약이면 호출하지 않는다. 예약은 환불하지 않는다. 전송 결과가
불명확할 때 과금 여부를 추측해 예산을 되돌리지 않기 위해서다.

비교 상한은 NAS `MARS_AI_DAILY_HARD_CAP_USD`와 ADMIN 웹 soft cap 중 작은 값이다.
운영자가 첫 NAS 상한을 `$1.00/일`로 정했다. V203은 새 설치의 수정되지 않은 DB
soft cap도 `$1.00/일`로 맞춘다. ADMIN이 0으로 바꾸면 추가 호출을 멈춘다.
원장의 수치는 **실제 청구액이 아니라
최대 공개가격 환산 사용량**이다. 무료 크레딧이나 공급자별 무료 토큰을 차감하지
않는다. [Google Cloud Vertex 가격](https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing)과
[Voyage 가격](https://docs.voyageai.com/docs/pricing)은 모델·시점에 따라 달라지므로
provider 경로가 사용하는 모델/토큰 상한으로 보수적인 최대값을 계산해야 한다.
Voyage는 무료 토큰 뒤 사용량에 가격을 적용한다고 공식 문서에 명시한다. Google Cloud의
일반 예산 알림은 자동 차단이 아니며, 지원되는 서비스의 Spend Cap도 처리 지연 중 초과
청구가 가능하다. 따라서 공급자 콘솔의 무료 제공량·속도 제한은 MARS 원장을 대체하지
않는다. [Voyage 요금](https://docs.voyageai.com/docs/pricing),
[Google Cloud 예산](https://docs.cloud.google.com/billing/docs/how-to/budgets),
[Spend Cap](https://docs.cloud.google.com/billing/docs/how-to/budgets-spend-caps)을 확인한다.

Pre-S5 RAG Vertex의 승인 `costCapMicrousd`와 S4.9 runtime Voyage query의
authorization owner·`costCapMicrousd`를 각각 provider 호출 전에 같은 트랜잭션으로
연결했다. 매매 뉴스 Vertex 판정도 검증된 자동운용 claim의 owner/run으로 예약 ID를
만들고, 요청 JSON 바이트 수에 1,024의 여유를 더한 입력 토큰 대용치와 출력 상한
1,024토큰을 운영자 입출력 단가로 환산해 호출 전에 같은 V201 원장에 예약한다.
DB/정책 장애나 상한 초과는
소켓을 열기 전에 ABSTAIN으로 닫는다. 이 수치는 실제 청구액 보장이 아니라 최대 노출액
예약 추정치다. full Strong LLM Agent도 Kotlin host의 provider permit 전에 검증된
owner/run별 `FULL_AGENT` 최대 노출액을 예약한다
([변경 근거](20260923-mars-full-agent-gross-permit.md)). 데모 Agent outbound는 후속
작업에서 연결한다.
공개 데모용 예산 helper는 `DEMO_AGENT` 예약에서 owner를 `NULL`로 보내며 Google Search
grounding을 거부한다. [익명 ask API](20260924-mars-anonymous-demo-agent.md)는 예제
근거와 분당 제한을 연결했다. 빈 DB·전용 이미지 기동과 이력 비저장 end-to-end 검증 전에는
데모 Agent를 배포하지 않는다.
모두 검증되기 전에는 공개 과금 API를 열지 않는다.
