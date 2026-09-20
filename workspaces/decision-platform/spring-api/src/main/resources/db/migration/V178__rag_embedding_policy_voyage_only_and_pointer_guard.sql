-- RAG 검색이 매번 실패하던 원인을 닫는다. forward-only.
--
-- 증상: 금융 Agent 가 무엇을 물어도 "충분한 근거를 찾지 못했습니다" 만 돌려줬다.
-- 로그에는 `rag v2 retrieval failed closed: RAG_RETRIEVAL_CHANNEL_UNAVAILABLE` 만 남았다.
--
-- 원인: 두 진실이 어긋나 있었다.
--   - rag_embedding_policy_state.effective_profile_id = 'bge_m3_local_1024_v1'
--   - rag_v2_immutable_public_bundle_pointers 의 유일한 ACTIVE 행 = 'voyage_context_4_1024_v1'
-- Spring 은 정책의 effective_profile_id 를 읽어 scope claim 에 박고
-- (JdbcRagRetrievalScopeRepository), resolve_rag_v2_retrieval_scope_v2 는 그 프로필로
-- 포인터를 찾는다(`embedding_profile_id = claim_row.embedding_profile_id`). BGE 로 찾으니
-- 맞는 포인터가 없어 'public pointer changed' 로 닫히고, 파이썬이 그 예외를
-- CHANNEL_UNAVAILABLE 로 바꿔 돌려줬다. 적재된 코퍼스 7,871 chunk 는 전부 Voyage 였다.
--
-- 조치 1: 정책을 실제 코퍼스와 같은 Voyage 로 맞춘다.
-- 조치 2: 같은 어긋남이 다시 생기지 못하게 트리거로 막는다. 인스턴스가 아니라 부류를 닫는다.
--
-- BGE 는 이 스택에서 기본 경로가 아니다. compose 의 bge-m3 는 `profiles: [models]` 라
-- `--models` 없이는 뜨지 않으므로, 정책만 BGE 를 가리키면 질의가 전부 죽는다.
-- 별도로 활성화하려면 BGE 코퍼스를 적재해 ACTIVE 포인터를 만든 뒤 정책을 바꿔야 한다.
-- 아래 트리거가 그 순서를 강제한다.

SET LOCAL row_security = on;

-- 유효 프로필은 같은 프로필의 ACTIVE 공개 포인터를 가져야 한다.
CREATE OR REPLACE FUNCTION public.rag_embedding_policy_pointer_guard_v1()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS
$rag_embedding_policy_pointer_guard_v1$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_public_bundle_pointers AS pointer
    WHERE pointer.state = 'ACTIVE'
      AND pointer.embedding_profile_id = NEW.effective_profile_id
  ) THEN
    RAISE EXCEPTION
      'rag embedding policy profile % has no ACTIVE public bundle pointer',
      NEW.effective_profile_id
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$rag_embedding_policy_pointer_guard_v1$;

REVOKE ALL PRIVILEGES ON FUNCTION public.rag_embedding_policy_pointer_guard_v1()
  FROM PUBLIC, decision_app, decision_worker, decision_automation_runtime;

-- 정책을 실제 코퍼스에 맞춘다. 트리거보다 먼저 한다 - 트리거는 이 값을 통과시킨다.
UPDATE public.rag_embedding_policy_state
   SET policy_id = 'voyage_only_v1',
       effective_profile_id = 'voyage_context_4_1024_v1',
       version = version + 1,
       changed_at = statement_timestamp(),
       changed_by_audit_ref = 'v178-rag-policy-voyage-alignment'
 WHERE state_id = 'default'
   AND effective_profile_id <> 'voyage_context_4_1024_v1';

DROP TRIGGER IF EXISTS rag_embedding_policy_pointer_guard
  ON public.rag_embedding_policy_state;
CREATE TRIGGER rag_embedding_policy_pointer_guard
  BEFORE INSERT OR UPDATE OF effective_profile_id
  ON public.rag_embedding_policy_state
  FOR EACH ROW EXECUTE FUNCTION public.rag_embedding_policy_pointer_guard_v1();
