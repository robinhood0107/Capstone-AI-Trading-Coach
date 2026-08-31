-- ADMIN 비동기 작업 목록이 항상 500으로 닫혀 있었다.
--
-- `list_async_job_status`는 한 페이지를 임시 표에 담아 두 번 읽는다 — 한 번은 감사 기록을 남기려고,
-- 한 번은 결과를 돌려주려고. 그런데 이 definer 함수의 소유자인 flyway에게는 이 데이터베이스의
-- TEMPORARY 권한이 없다. 그래서 `GET /api/v1/async-jobs`는 관리자 자격으로도 언제나
-- `permission denied to create temporary tables`로 죽었다. 목록을 한 번도 열어 본 적이 없어서
-- 지금까지 드러나지 않았다.
--
-- 고치는 방향은 둘이었다. 데이터베이스 수준 GRANT를 주는 것과, 임시 표를 쓰지 않는 것. 앞의 것은
-- 데이터베이스 소유자(postgres) 권한이 필요해 마이그레이션이 스스로 할 수 없고, 무엇보다 이 함수
-- 하나 때문에 역할에 새 권한을 넓히는 일이다. 그래서 뒤를 골랐다.
--
-- 데이터 변경 CTE는 참조 여부와 무관하게 정확히 한 번 끝까지 실행되므로, 감사 기록은 임시 표를
-- 쓸 때와 똑같이 남는다. 페이지 정의도 정렬도 상한도 그대로다. 바뀌는 것은 중간 저장 방식뿐이다.
--
-- 아래 본문은 실행 중인 데이터베이스의 `pg_get_functiondef` 출력을 그대로 가져와 위 블록만
-- 바꾼 것이다. 시그니처, search_path, 권한 검사, 반환 열은 손대지 않았다.

CREATE OR REPLACE FUNCTION public.list_async_job_status(p_actor_user_id text, p_security_version bigint, p_status text, p_job_type text, p_before_created_at timestamp with time zone, p_before_job_id text, p_limit integer)
 RETURNS TABLE(job_id text, job_type text, status text, requested_at timestamp with time zone, started_at timestamp with time zone, completed_at timestamp with time zone, source_id text, artifact_id text, result_ref text, error_code text, error_class text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE actor_role text; actor_status text; actor_security_version bigint;
BEGIN
  IF session_user <> 'decision_app' THEN
    RAISE EXCEPTION 'async status role denied' USING ERRCODE = '42501';
  END IF;
  IF p_limit < 1 OR p_limit > 101
     OR (p_status IS NOT NULL AND p_status NOT IN ('REQUESTED', 'RUNNING', 'COMPLETED', 'FAILED', 'NEEDS_REVIEW'))
     OR (p_job_type IS NOT NULL AND p_job_type NOT IN ('RAG_INDEX', 'ARTIFACT_INGEST', 'MODEL_EVAL'))
     OR ((p_before_created_at IS NULL) <> (p_before_job_id IS NULL)) THEN
    RAISE EXCEPTION 'invalid async status query' USING ERRCODE = '22023';
  END IF;
  SELECT role, users.status, security_version INTO actor_role, actor_status, actor_security_version
  FROM public.users WHERE user_id = p_actor_user_id FOR SHARE;
  IF NOT FOUND OR actor_status <> 'ACTIVE' OR actor_role <> 'ADMIN'
     OR actor_security_version <> p_security_version THEN
    RETURN;
  END IF;
  RETURN QUERY
  WITH page AS (
    SELECT item.job_id, item.job_type, item.status, item.created_at, item.started_at, item.completed_at,
           item.payload_json ->> 'sourceId' AS source_id,
           item.payload_json ->> 'artifactId' AS artifact_id,
           item.result_json ->> 'resultRef' AS result_ref,
           item.error_code, item.error_class, item.requested_by
    FROM public.async_job item
    WHERE (p_status IS NULL OR item.status = p_status)
      AND (p_job_type IS NULL OR item.job_type = p_job_type)
      AND (
        p_before_created_at IS NULL
        OR (item.created_at, item.job_id) < (p_before_created_at, p_before_job_id)
      )
    ORDER BY item.created_at DESC, item.job_id DESC
    LIMIT p_limit
  ), audited AS (
    INSERT INTO public.async_job_admin_read_audit(actor_user_id, target_owner_user_id, job_id, read_kind)
    SELECT p_actor_user_id, page.requested_by, page.job_id, 'LIST'
    FROM page
    WHERE page.requested_by IS DISTINCT FROM p_actor_user_id
    RETURNING 1
  )
  SELECT page.job_id, page.job_type, page.status, page.created_at, page.started_at, page.completed_at,
         page.source_id, page.artifact_id, page.result_ref, page.error_code, page.error_class
  FROM page
  ORDER BY page.created_at DESC, page.job_id DESC;
END
$function$;

ALTER FUNCTION public.list_async_job_status(text, bigint, text, text, timestamp with time zone, text, integer) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.list_async_job_status(text, bigint, text, text, timestamp with time zone, text, integer) FROM PUBLIC;
-- 이 함수는 decision_app이 직접 부르지 않는다. S7 보안 폐쇄가 그 권한을 일부러 회수했고,
-- 호출은 definer wrapper인 list_async_job_status_authorized만 한다. 그래서 다시 주지 않는다.
