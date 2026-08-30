# RAG v2 검색 경계와 Vertex 생성

`tests/e2e/rag_v2_boundaries.py`
증거 `artifacts/decision-platform/e2e/rag-v2-boundaries-default.json`(기본값),
`rag-v2-boundaries.json`(옵트인)

기대값을 코드에 박지 않는다. 생성이 켜져 있는지를 **컨테이너 환경에서 읽어** 그 값에 따라
기대 상태를 정한다.

## 기본 배포 (Vertex off)

| 기능 | 방식 | 결과 |
|---|---|---|
| 검색 역할 권한 | stack | PASS — 직접 표 권한 0, 함수 EXECUTE 12 |
| Voyage 문서 배치 계획 | stack | PASS — `COMPLETE`, 7,871 청크 |
| 로컬 루트 경계 | stack | PASS — 0700/0600, 소유자 일치 |
| 공식 tokenizer 해시 | stack | PASS |
| 전송 설정 | stack | PASS — reflection off, loopback 고정 |
| 동의 없는 질의 | live | PASS — HTTP 409 `EXTERNAL_AI_CONSENT_REQUIRED`, 예약 증가 0 |
| 실검색 1회 | live | PASS — `RETRIEVAL_ONLY`, 인용 5건, 예약 +1 |

## 옵트인 배포 (Vertex on + 자동 활성화)

| 기능 | 방식 | 결과 |
|---|---|---|
| 동의 없는 질의 | live | PASS — 준비 단계 consent에서 409, 예약 증가 0 |
| 실검색 1회 (패킷 없음) | live | PASS — 검색은 돌고 생성만 `GENERATION_UNAVAILABLE` |
| 생성형 답변 1회 (패킷 저술) | live | PASS — **`ANSWERED`**, 인용커버리지 1.0 |
| 대시보드 경로 생성 (헤더 없음) | live | PASS — **`ANSWERED`**, 인용 1건 |

```bash
# 옵트인 구성으로 스택 띄우기
cd deploy/p1
P1_RAG_V2_ENABLED=true P1_RAG_V2_VERTEX_ENABLED=true P1_RAG_V2_VERTEX_AUTO_ACTIVATION_ENABLED=true \
  P1_RAG_V2_VERTEX_HEAD_COMMIT=... P1_RAG_V2_VERTEX_TREE_DIGEST=... \
  P1_RAG_V2_VERTEX_CI_DIGEST=... P1_RAG_V2_VERTEX_SECURITY_DIGEST=... \
  docker compose --project-name capstone-p1 --env-file .state-app/runtime.env -f compose.yml \
  up -d --no-deps --force-recreate --wait decision-platform
```

## 이 실행에서 드러나 고친 것

1. **중첩 `maxItems`** — `responseSchema`의 `sentences.items` 안쪽 배열 셋에 `maxItems`가 있으면
   Vertex가 요청 전체를 `INVALID_ARGUMENT` 400으로 거절한다. 셋 다 제거했고 상한은
   `RagV2VertexResponseValidator`가 응답 검증에서 그대로 강제한다.
2. **thinking 예산** — `gemini-3.5-flash`는 thinking이 기본이고 그 토큰이 `maxOutputTokens`를 같이
   먹는다. 그래서 JSON이 `MAX_TOKENS`로 잘려(86 bytes) 검증이 늘 닫혔다. `thinkingBudget=0`으로
   고정하니 `STOP`, 1,301 bytes로 통과했다.
3. **예약 만료 정밀도** — 자동 저술 패킷의 `expiresAt`을 나노초로 두면 timestamptz 왕복에서
   잘려 예약이 닫혔다. 초 단위로 자른다.
4. **V103** — 근거 조회의 actor scope가 `RAG_ANSWER`였는데 실제 요구는 `RAG_SCOPE`였다.
5. **V104** — 자동 저술의 소유자별 하루 상한을 세는 definer 함수를 추가했다.

## 자동 활성화가 바꾼 것과 바꾸지 않은 것

바뀐 것은 승인의 위치뿐이다. 호출마다의 사람 승인이 배포 시점의 정책 승인으로 내려갔다.
모델, 비용 상한, evidence 해시, 코드 바인딩, 물리 호출 상한, 단일 사용 nonce, 5분 만료는
그대로 강제된다. 사람이 곧 호출 한도이던 자리는 소유자별 하루 상한이 대신한다.

## 아직 이 증거가 의미하지 않는 것

- 실제 Team B 산출물이 아니다. 여기서 쓴 번들은 합성 golden이고 `evidence_mode`가 그 사실을 말한다.
- 실제 Team A UI가 아니다. 대시보드는 현재 tree의 화면이며 최종본이 아니다.
- 실계좌 거래가 아니다. KIS Live 호출은 영구 금지이고 이 기록 어디에도 그 경로가 없다.
- 시장이 열린 시간의 실체결이 아니다. 그 항목은 `P1_장시간_의존_항목.md`가 따로 다룬다.
