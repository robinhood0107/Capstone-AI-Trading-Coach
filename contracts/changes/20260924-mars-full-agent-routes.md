# MARS full Agent의 공개 경로 경계

`mars-full`의 공개 게이트는 기존 owner 기반 RAG v2의 corpus 상태·외부 처리 동의,
세계 뉴스, ask, 이력 조회/삭제, Vertex 준비 경로만 정확한 method/path로 허용한다.
답변 이력 상세는 `rag_` ID 패턴 한 단계만 허용한다. 인증과 실제 owner 검증은
기존 Bearer SecurityFilterChain·AppPrincipal·RLS가 계속 수행한다.
`mars-demo`는 같은 경로를 모두 거부하고 고정 예제 Agent만 사용한다.

이 변경은 HTTP 게이트의 연결이다. Voyage/Vertex 비밀값·코퍼스 초기화·비용
한도·실서비스 Compose·두 사용자 이력 격리 smoke가 끝나기 전에는 제품 완료나
발행 준비로 표시하지 않는다.
