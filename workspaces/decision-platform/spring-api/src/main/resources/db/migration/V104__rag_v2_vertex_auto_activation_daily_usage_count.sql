-- 활성화 패킷을 운영자가 매번 저술하던 동안에는 사람이 곧 호출 한도였다. 배포 정책으로
-- 자동 저술하게 되면 그 한도가 사라지므로, 소유자별 하루 생성 예약 수를 셀 방법이 필요하다.
--
-- 예약 표에는 어떤 런타임 역할도 직접 권한이 없다. 다른 모든 접근과 같은 규칙으로 definer
-- 함수 하나만 추가하고, 그 함수는 개수만 돌려준다. 질문·근거·nonce·패킷 해시는 나가지 않는다.

CREATE OR REPLACE FUNCTION public.count_rag_v2_immutable_vertex_usage_today(p_owner_user_id text)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  reserved integer;
BEGIN
  IF p_owner_user_id IS NULL OR p_owner_user_id !~ '^usr_[0-9a-z_]{1,60}$' THEN
    RAISE EXCEPTION 'RAG_V2_VERTEX_DAILY_USAGE_OWNER_INVALID' USING ERRCODE = '22023';
  END IF;

  SELECT count(*)
    INTO reserved
    FROM public.rag_v2_immutable_vertex_usage_reservations
   WHERE owner_user_id = p_owner_user_id
     AND created_at >= date_trunc('day', now());

  RETURN reserved;
END;
$$;

ALTER FUNCTION public.count_rag_v2_immutable_vertex_usage_today(text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.count_rag_v2_immutable_vertex_usage_today(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.count_rag_v2_immutable_vertex_usage_today(text) TO decision_app;
