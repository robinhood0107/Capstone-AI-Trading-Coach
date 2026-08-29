-- P1 공개 RAG seed는 코퍼스는 가져오지만 그 코퍼스를 만든 Voyage 문서 배치의 출처 기록은
-- 가져오지 않는다. `app/release/public_rag_seed.py`의 `TABLE_SPECS`는 아홉 표뿐이고
-- `rag_v2_immutable_voyage_document_batch_plans`가 빠져 있다.
--
-- 그 결과 `reserve_s4_9_runtime_voyage_query_usage`가 활성 계획에서 공식 tokenizer 해시를 읽지
-- 못한다. 0행이면 `NULL IS DISTINCT FROM '<64hex>'`가 참이라 모든 Voyage 질의 예약이
-- ERRCODE 55000으로 닫히고, 호출자에게는 `RAG_QUERY_PROFILE_UNAVAILABLE`이라는 엉뚱한 이유로
-- 보인다. 즉 코퍼스는 적재돼 있는데 질의 경로가 구조적으로 막혀 있다.
--
-- 이 행은 지어내는 값이 아니다. 전부 레포에 있는 실제 산출물
-- `capstone-rag/runtime/pre-s5-fresh/local-corpus/batch-plans/public-voyage-batches.v1.json`
-- 에서 나온다. tokenizer 해시는 같은 트리의 `artifacts/voyage-context-4/tokenizer.receipt.json`
-- (`state: ACQUIRED`)과 교차 확인된다.
--
--   planSha256      a5ec4001…      batch_plan_sha256
--   tokenizerSha256 c0382117…      official_tokenizer_sha256
--   sourceCount     142            expected_source_count
--   chunkCount      7,871          expected_chunk_count
--   tokenCount      3,243,555      expected_token_count
--   batchCount      63             expected_batch_count
--
-- 아티팩트의 `state`는 `PREPARED`이지만 예약 함수는 `COMPLETE`를 요구한다. 이 DB에서 그 배치의
-- 결과물은 실제로 활성화까지 끝나 있으므로(EXACT30 30/30, OA112 112/7,841이 모두 ACTIVE·PASSED)
-- `COMPLETE`가 사실에 맞고, `completed_at`도 지어내지 않고 그 활성화 시각을 그대로 쓴다.
--
-- 코퍼스가 실제로 그 모양일 때만 넣는다. 아니면 아무것도 하지 않는다. 이 행이 DB가 뒷받침하지
-- 않는 출처를 주장하는 일이 없어야 한다.

INSERT INTO public.rag_v2_immutable_voyage_document_batch_plans (
  batch_plan_sha256,
  embedding_profile_id,
  official_tokenizer_sha256,
  expected_source_count,
  expected_chunk_count,
  expected_token_count,
  expected_batch_count,
  owner_scope_sha256,
  owner_private_ordered_group_count,
  state,
  created_at,
  completed_at
)
SELECT
  'a5ec40010296f0f2a8935bf283e54296972db85963f1db89c7f7d83e5fb5d66c',
  'voyage_context_4_1024_v1',
  'c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539',
  142,
  7871,
  3243555,
  63,
  NULL,
  0,
  'COMPLETE',
  min(generation.created_at),
  max(generation.activated_at)
FROM public.rag_v2_immutable_component_generations AS generation
WHERE generation.owner_partition_key = '__PUBLIC__'
  AND generation.embedding_profile_id = 'voyage_context_4_1024_v1'
  AND generation.state = 'ACTIVE'
  AND generation.evaluation_status = 'PASSED'
  AND generation.activated_at IS NOT NULL
HAVING count(*) = 2
   AND sum(generation.actual_source_count) = 142
   AND sum(generation.actual_chunk_count) = 7871
   AND (SELECT count(*) FROM public.rag_v2_immutable_chunks) = 7871
ON CONFLICT (batch_plan_sha256) DO NOTHING;
