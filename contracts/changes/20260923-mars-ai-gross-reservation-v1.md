# MARS 운영자 AI 공개가격 기준 예약 원장

V201은 `Asia/Seoul` 날짜의 운영자 AI 최대 노출액을 한 원장에 원자 예약한다.
`DEMO_AGENT`, `FULL_AGENT`, `TRADE_AI`, `RAG_VERTEX`, `RAG_VOYAGE`가 같은 합계를
공유한다. 각 요청은 provider 호출 전에 공개 가격 기준 최대 비용을 예약하며, 잔액
부족·DB 장애·중복 예약이면 호출하지 않는다. 예약은 환불하지 않는다. 전송 결과가
불명확할 때 과금 여부를 추측해 예산을 되돌리지 않기 위해서다.

비교 상한은 NAS `MARS_AI_DAILY_HARD_CAP_USD`와 ADMIN 웹 soft cap 중 작은 값이다.
운영자가 첫 NAS 상한을 `$1.00/일`로 정했다. DB soft cap 기본 0은 공개 과금 정지를
뜻하며, 관리자 설정 뒤에만 예산이 생긴다. 원장의 수치는 **실제 청구액이 아니라
최대 공개가격 환산 사용량**이다. 무료 크레딧이나 공급자별 무료 토큰을 차감하지
않는다. [Google Cloud Vertex 가격](https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing)과
[Voyage 가격](https://docs.voyageai.com/docs/pricing)은 모델·시점에 따라 달라지므로
provider 경로가 사용하는 모델/토큰 상한으로 보수적인 최대값을 계산해야 한다.

이번 단계에서 기존 Pre-S5 RAG Vertex의 승인 패킷 `costCapMicrousd`를 같은 트랜잭션에
연결했다. Strong LLM Agent·Voyage·매매 AI의 outbound 경로와 공급자별 호출/토큰
제한은 후속 PR에서 이어 붙인다. 모두 검증되기 전에는 공개 과금 API를 열지 않는다.
