# MARS full Agent 호출 전 운영자 총액 예약

Strong LLM Agent의 실제 Vertex 소켓은 Python에서 열리지만, provider 호출별
`ProviderCallPermit`은 Kotlin host만 보낸다. full 제품에서 host는 이 permit을 보내기
전에 검증된 RAG owner·run·planned call ID로 V201의 `FULL_AGENT` 예약을 한 번 만든다.
예산 부족·DB 장애·중복 ID·제품 모드 오류면 permit을 보내지 않는다. 데모 제품에서 이
owner-bound full Agent 경로는 열리지 않는다.

예약액은 시작 gRPC frame 크기, host가 본 tool/grounding frame 크기, 이전 provider
출력의 보수적 재입력 여유, prompt template 여유 8,192, 배포 출력 상한과 Vertex
입출력 공개가격 단가로 계산한다. Google Search가 붙은 discovery 호출에는 기존 월간
정책의 `reservePerPrompt`(최대 8쿼리)에 쿼리당 14,000마이크로달러를 곱해 더한다.
2026-09-23 [Google Cloud 공식 가격표](https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing)는
Gemini 3 계열 Google Search grounding을 최초 월 5,000쿼리 무료, 초과분
$14/1,000쿼리로 안내한다. MARS 원장은 무료 쿼리도 공개가격으로 예약한다.

예약은 실제 청구액 확정값이 아니라 최대 노출 추정치이며 예약 성공 뒤 실패한 호출에도 환불하지
않는다. provider 응답이 불명확해도 예산을 되돌려 다시 과금 호출을 열지 않기 위해서다.
공개 데모의 익명 Agent와 두 제품 간 같은 날 총액 배분은 별도 작업이다.
