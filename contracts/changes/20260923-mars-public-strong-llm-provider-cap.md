# MARS 공개 Strong LLM provider·출력 상한 결속

공개 제품은 운영자 Vertex 서비스계정으로만 Agent를 실행한다. 기존 Strong LLM provider
chain은 API key 입력과 2차 provider를 허용했고, Python Vertex 구현의 출력 상한
32,768토큰은 Spring의 기본 4,096토큰 설정과 달랐다. 이 상태에서 host의 공용 비용
예약액을 계산하면 실제 provider 상한과 어긋난다.

`MARS_PUBLIC_SURFACE_MODE=FULL` 또는 `DEMO`에서는 primary provider가 Vertex여야 하며
API key·base URL·fallback provider 설정이 있으면 chain 생성 전에 거부한다.
`RAG_LLM_MAX_OUTPUT_TOKENS`를 Spring과 loopback Python에 같은 값으로 전달한다.
기본 4,096, 허용 범위 256~32,768이며 Python Vertex 호출에도 적용한다. 임의 provider
호출을 허용하는 공개 경로는 없다.

이 변경은 provider 호출별 V201 예약을 위한 입력 계약이다. 실제 Strong LLM host permit의
공용 비용 예약은 별도 변경에서 연결한다. 그 전에는 공개 Agent 과금 경로를 열지 않는다.
