# RAG 런타임 기본 트리

이미지에 구워 두고, 마운트된 런타임 루트가 비어 있을 때 entrypoint 가 채운다.
레포 없이 이미지만 받은 서버에서도 금융 Agent 가 뜨게 하는 것이 목적이다.

- `artifacts/voyage-context-4/tokenizer.json` — 정적 산출물. 매번 같다.
- `control/pre-s5-voyage-query-runtime.json` — 질의 런타임 기술서.

Vertex 자동 활성화 정책(상한)은 운영자가 정하는 값이라 여기 두지 않는다.
`P1_VERTEX_DAILY_GENERATE_CALL_CAP` 같은 env 에서 entrypoint 가 만들어 낸다.
