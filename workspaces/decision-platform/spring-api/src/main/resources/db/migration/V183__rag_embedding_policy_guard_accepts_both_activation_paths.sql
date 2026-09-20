-- V178 가드가 너무 좁았다. forward-only.
--
-- 증상: RagSourceApiIntegrationTest 세 건이 DataIntegrityViolationException 으로 죽었다.
--   ERROR: rag embedding policy profile bge_m3_local_1024_v1 has no ACTIVE public bundle pointer
--
-- 원인: V178 은 유효 프로필이 rag_v2_immutable_public_bundle_pointers 의 ACTIVE 행을
-- 가져야 한다고만 봤다. 그런데 적재 경로가 둘이다 - v2 불변 번들 포인터와, 그보다 오래된
-- rag_corpus_generations 활성화다. 후자로 코퍼스를 올린 뒤 정책을 맞추는 순서는 정당한데
-- V178 은 그것을 없는 것으로 취급했다.
--
-- 막으려던 것은 "적재되지 않은 프로필을 정책이 가리키는 것" 하나다. 그 판정에 두 경로를
-- 모두 인정한다. 어느 쪽으로도 활성 코퍼스가 없으면 여전히 거부한다.

SET LOCAL row_security = on;

CREATE OR REPLACE FUNCTION public.rag_embedding_policy_pointer_guard_v1()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS
$rag_embedding_policy_pointer_guard_v1$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_public_bundle_pointers AS pointer
    WHERE pointer.state = 'ACTIVE'
      AND pointer.embedding_profile_id = NEW.effective_profile_id
  ) THEN
    RETURN NEW;
  END IF;

  IF EXISTS (
    SELECT 1
    FROM public.rag_corpus_generations AS generation
    WHERE generation.status = 'ACTIVE'
      AND generation.embedding_profile_id = NEW.effective_profile_id
  ) THEN
    RETURN NEW;
  END IF;

  RAISE EXCEPTION
    'rag embedding policy profile % has no active corpus on either activation path',
    NEW.effective_profile_id
    USING ERRCODE = '23514';
END;
$rag_embedding_policy_pointer_guard_v1$;

REVOKE ALL PRIVILEGES ON FUNCTION public.rag_embedding_policy_pointer_guard_v1()
  FROM PUBLIC, decision_app, decision_worker, decision_automation_runtime;
