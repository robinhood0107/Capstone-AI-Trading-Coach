# MARS Vertex 공개가격 예약 단가 보정

2026-09-23 [Google Cloud 공식 Agent Platform 가격표](https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing)는
현재 기본 모델 Gemini 3.5 Flash의 global 일반 호출을 입력 $2.70·출력(추론 포함)
$16.20/백만 토큰으로 표시한다. 기존 배포 예시의 2·9 마이크로달러/토큰은 이 가격보다
낮아 V201의 공개가격 기준 예약액을 과소 계산할 수 있었다.

새 배포 예시·RAG 정책 seed·Compose의 기본값을 각각 3·17 마이크로달러/토큰으로
올림 처리한다. Python 매매 AI 공개 모드는 이보다 낮은 입력 단가 또는 출력 단가를
거부한다. 모델이나 Google 가격이 바뀌면 운영자는 공식 가격을 다시 확인하고 두 단가를
함께 올려야 한다. 이 고정 하한은 다른 모델의 가격까지 자동으로 보장하지 않는다.

기존 NAS 런타임 볼륨의 정책 파일은 seed가 덮어쓰지 않는다. 공개 제품 활성화 전에
현재 정책의 모델·단가를 다시 검증하고 오래된 2·9 정책을 교체해야 한다. Google Search
grounding은 별도 쿼리 가격과 한도가 있으므로 Agent permit의 총액 예약에서 따로 다룬다.
