-- Strong LLM을 화면에서 고르게 한다.
--
-- provider는 계속 바뀐다. 지금 잘 답하는 모델이 여섯 달 뒤에도 최선이라는 보장이 없고, 그때
-- 배포 환경변수를 고쳐야만 바꿀 수 있다면 그 선택은 사용자의 것이 아니라 운영자의 것이 된다.
-- 그래서 provider·2차 provider·답변 언어·하루 호출 상한을 소유자별로 저장한다.
--
-- API 키는 원문으로 두지 않는다. RAG 답변 이력과 같은 KEK 봉투 방식으로 감싸고, 화면에는
-- 마지막 네 글자만 돌려준다. 복호화된 키는 provider 호출 직전에만 만들어지고 응답 DTO·로그·
-- 오류 어디에도 실리지 않는다.

CREATE TABLE public.strong_llm_owner_settings (
  owner_user_id text PRIMARY KEY REFERENCES public.users(user_id) ON DELETE CASCADE,
  provider text NOT NULL CHECK (
    provider IN ('vertex','openai','anthropic','google_genai','custom')
  ),
  -- 1차가 실패했을 때만 쓴다. 비어 있으면 1차 실패가 곧 판단 실패이고, 그때 자동매매는
  -- AI 미참여로 계속한다.
  fallback_provider text CHECK (
    fallback_provider IS NULL
    OR fallback_provider IN ('vertex','openai','anthropic','google_genai','custom')
  ),
  model_id text CHECK (model_id IS NULL OR model_id ~ '^[a-z][a-z0-9._-]{2,127}$'),
  fallback_model_id text CHECK (
    fallback_model_id IS NULL OR fallback_model_id ~ '^[a-z][a-z0-9._-]{2,127}$'
  ),
  -- 사용자가 직접 넣는 OpenAI 호환 endpoint. https만 받는다.
  base_url text CHECK (base_url IS NULL OR base_url ~ '^https://[A-Za-z0-9._~:/?#@!$&()*+,;=%-]{3,256}$'),
  fallback_base_url text CHECK (
    fallback_base_url IS NULL
    OR fallback_base_url ~ '^https://[A-Za-z0-9._~:/?#@!$&()*+,;=%-]{3,256}$'
  ),
  answer_language text NOT NULL CHECK (answer_language IN ('ko','en')),
  -- 통제는 출력 상한이 아니라 횟수로 한다. 상한을 좁게 두면 답이 잘리고, 잘린 답은 계약
  -- 위반으로 통째로 버려져 사용자는 돈만 쓰고 아무것도 받지 못한다.
  daily_generate_call_cap integer NOT NULL CHECK (daily_generate_call_cap BETWEEN 1 AND 500),
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  CONSTRAINT strong_llm_owner_settings_custom_base_url_check CHECK (
    provider <> 'custom' OR base_url IS NOT NULL
  ),
  CONSTRAINT strong_llm_owner_settings_fallback_custom_base_url_check CHECK (
    fallback_provider IS DISTINCT FROM 'custom' OR fallback_base_url IS NOT NULL
  )
);

-- 키는 슬롯마다 한 행이다. 한 행에 두 벌을 담으면 컬럼이 열둘이 되고, 어느 것이 어느 슬롯의
-- nonce인지 이름으로만 구분해야 한다.
CREATE TABLE public.strong_llm_owner_credentials (
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
  slot text NOT NULL CHECK (slot IN ('PRIMARY','FALLBACK')),
  kek_version text NOT NULL CHECK (kek_version ~ '^kek-v[1-9][0-9]{0,8}$'),
  wrap_nonce bytea NOT NULL CHECK (octet_length(wrap_nonce) = 12),
  wrapped_dek bytea NOT NULL CHECK (octet_length(wrapped_dek) = 32),
  wrap_tag bytea NOT NULL CHECK (octet_length(wrap_tag) = 16),
  key_nonce bytea NOT NULL CHECK (octet_length(key_nonce) = 12),
  key_ciphertext bytea NOT NULL CHECK (octet_length(key_ciphertext) BETWEEN 1 AND 8192),
  key_tag bytea NOT NULL CHECK (octet_length(key_tag) = 16),
  -- 화면이 "키가 들어 있다"를 말하려면 이것만 있으면 된다. 그 이상은 돌려주지 않는다.
  key_last4 text NOT NULL CHECK (key_last4 ~ '^[A-Za-z0-9_-]{4}$'),
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (owner_user_id, slot)
);

ALTER TABLE public.strong_llm_owner_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.strong_llm_owner_settings FORCE ROW LEVEL SECURITY;
ALTER TABLE public.strong_llm_owner_credentials ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.strong_llm_owner_credentials FORCE ROW LEVEL SECURITY;

CREATE POLICY strong_llm_owner_settings_owner_v108 ON public.strong_llm_owner_settings TO PUBLIC
USING (
  session_user='decision_app' AND public.actor_rls_scope_is_open_v1()
  AND (current_user='flyway' OR owner_user_id=pg_catalog.current_setting('app.actor_user_id',true))
)
WITH CHECK (
  session_user='decision_app' AND public.actor_rls_scope_is_open_v1()
  AND (current_user='flyway' OR owner_user_id=pg_catalog.current_setting('app.actor_user_id',true))
);

-- 키 행은 소유자 세션도 SELECT하지 못한다. 읽는 길은 definer 함수 하나뿐이고 그 함수는
-- 복호화 재료를 provider 호출 경로에만 돌려준다. 화면이 쓰는 마지막 네 글자는 별도 함수가
-- 준다. 테이블을 직접 열어 두면 언젠가 조인 하나가 키 봉투를 응답에 실어 나른다.
CREATE POLICY strong_llm_owner_credentials_definer_v108
ON public.strong_llm_owner_credentials TO PUBLIC
USING (current_user='flyway' AND session_user='decision_app')
WITH CHECK (current_user='flyway' AND session_user='decision_app');

GRANT SELECT, INSERT, UPDATE ON TABLE public.strong_llm_owner_settings TO decision_app;

CREATE OR REPLACE FUNCTION public.put_strong_llm_owner_settings_v1(
  p_owner_user_id text,
  p_provider text,
  p_fallback_provider text,
  p_model_id text,
  p_fallback_model_id text,
  p_base_url text,
  p_fallback_base_url text,
  p_answer_language text,
  p_daily_generate_call_cap integer
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
AS $put_strong_llm_owner_settings_v1$
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id THEN
    RAISE EXCEPTION 'strong llm settings actor is invalid' USING ERRCODE='42501';
  END IF;
  INSERT INTO public.strong_llm_owner_settings (
    owner_user_id, provider, fallback_provider, model_id, fallback_model_id,
    base_url, fallback_base_url, answer_language, daily_generate_call_cap,
    created_at, updated_at
  ) VALUES (
    p_owner_user_id, p_provider, p_fallback_provider, p_model_id, p_fallback_model_id,
    p_base_url, p_fallback_base_url, p_answer_language, p_daily_generate_call_cap,
    pg_catalog.now(), pg_catalog.now()
  )
  ON CONFLICT (owner_user_id) DO UPDATE SET
    provider=excluded.provider,
    fallback_provider=excluded.fallback_provider,
    model_id=excluded.model_id,
    fallback_model_id=excluded.fallback_model_id,
    base_url=excluded.base_url,
    fallback_base_url=excluded.fallback_base_url,
    answer_language=excluded.answer_language,
    daily_generate_call_cap=excluded.daily_generate_call_cap,
    updated_at=pg_catalog.now();
END;
$put_strong_llm_owner_settings_v1$;

CREATE OR REPLACE FUNCTION public.put_strong_llm_owner_credential_v1(
  p_owner_user_id text,
  p_slot text,
  p_kek_version text,
  p_wrap_nonce bytea,
  p_wrapped_dek bytea,
  p_wrap_tag bytea,
  p_key_nonce bytea,
  p_key_ciphertext bytea,
  p_key_tag bytea,
  p_key_last4 text
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
AS $put_strong_llm_owner_credential_v1$
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_slot NOT IN ('PRIMARY','FALLBACK') THEN
    RAISE EXCEPTION 'strong llm credential actor is invalid' USING ERRCODE='42501';
  END IF;
  INSERT INTO public.strong_llm_owner_credentials (
    owner_user_id, slot, kek_version, wrap_nonce, wrapped_dek, wrap_tag,
    key_nonce, key_ciphertext, key_tag, key_last4, created_at, updated_at
  ) VALUES (
    p_owner_user_id, p_slot, p_kek_version, p_wrap_nonce, p_wrapped_dek, p_wrap_tag,
    p_key_nonce, p_key_ciphertext, p_key_tag, p_key_last4, pg_catalog.now(), pg_catalog.now()
  )
  ON CONFLICT (owner_user_id, slot) DO UPDATE SET
    kek_version=excluded.kek_version,
    wrap_nonce=excluded.wrap_nonce,
    wrapped_dek=excluded.wrapped_dek,
    wrap_tag=excluded.wrap_tag,
    key_nonce=excluded.key_nonce,
    key_ciphertext=excluded.key_ciphertext,
    key_tag=excluded.key_tag,
    key_last4=excluded.key_last4,
    updated_at=pg_catalog.now();
END;
$put_strong_llm_owner_credential_v1$;

CREATE OR REPLACE FUNCTION public.delete_strong_llm_owner_credential_v1(
  p_owner_user_id text,
  p_slot text
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
AS $delete_strong_llm_owner_credential_v1$
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_slot NOT IN ('PRIMARY','FALLBACK') THEN
    RAISE EXCEPTION 'strong llm credential actor is invalid' USING ERRCODE='42501';
  END IF;
  DELETE FROM public.strong_llm_owner_credentials
  WHERE owner_user_id=p_owner_user_id AND slot=p_slot;
END;
$delete_strong_llm_owner_credential_v1$;

-- 화면이 쓰는 읽기다. 봉투도 암호문도 나가지 않고 마지막 네 글자만 나간다.
CREATE OR REPLACE FUNCTION public.read_strong_llm_owner_key_last4_v1(p_owner_user_id text)
RETURNS TABLE(slot text, key_last4 text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public
AS $read_strong_llm_owner_key_last4_v1$
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id THEN
    RAISE EXCEPTION 'strong llm credential actor is invalid' USING ERRCODE='42501';
  END IF;
  RETURN QUERY
  SELECT item.slot, item.key_last4
  FROM public.strong_llm_owner_credentials item
  WHERE item.owner_user_id=p_owner_user_id
  ORDER BY item.slot;
END;
$read_strong_llm_owner_key_last4_v1$;

-- 복호화 재료를 돌려주는 유일한 길이다. provider 호출 직전에만 부른다.
CREATE OR REPLACE FUNCTION public.read_strong_llm_owner_credential_v1(
  p_owner_user_id text,
  p_slot text
) RETURNS TABLE(
  kek_version text, wrap_nonce bytea, wrapped_dek bytea, wrap_tag bytea,
  key_nonce bytea, key_ciphertext bytea, key_tag bytea
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public
AS $read_strong_llm_owner_credential_v1$
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_slot NOT IN ('PRIMARY','FALLBACK') THEN
    RAISE EXCEPTION 'strong llm credential actor is invalid' USING ERRCODE='42501';
  END IF;
  RETURN QUERY
  SELECT item.kek_version, item.wrap_nonce, item.wrapped_dek, item.wrap_tag,
         item.key_nonce, item.key_ciphertext, item.key_tag
  FROM public.strong_llm_owner_credentials item
  WHERE item.owner_user_id=p_owner_user_id AND item.slot=p_slot;
END;
$read_strong_llm_owner_credential_v1$;

ALTER FUNCTION public.put_strong_llm_owner_settings_v1(
  text,text,text,text,text,text,text,text,integer
) OWNER TO flyway;
ALTER FUNCTION public.put_strong_llm_owner_credential_v1(
  text,text,text,bytea,bytea,bytea,bytea,bytea,bytea,text
) OWNER TO flyway;
ALTER FUNCTION public.delete_strong_llm_owner_credential_v1(text,text) OWNER TO flyway;
ALTER FUNCTION public.read_strong_llm_owner_key_last4_v1(text) OWNER TO flyway;
ALTER FUNCTION public.read_strong_llm_owner_credential_v1(text,text) OWNER TO flyway;

REVOKE ALL ON FUNCTION public.put_strong_llm_owner_settings_v1(
  text,text,text,text,text,text,text,text,integer
) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.put_strong_llm_owner_credential_v1(
  text,text,text,bytea,bytea,bytea,bytea,bytea,bytea,text
) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.delete_strong_llm_owner_credential_v1(text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.read_strong_llm_owner_key_last4_v1(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.read_strong_llm_owner_credential_v1(text,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION public.put_strong_llm_owner_settings_v1(
  text,text,text,text,text,text,text,text,integer
) TO decision_app;
GRANT EXECUTE ON FUNCTION public.put_strong_llm_owner_credential_v1(
  text,text,text,bytea,bytea,bytea,bytea,bytea,bytea,text
) TO decision_app;
GRANT EXECUTE ON FUNCTION public.delete_strong_llm_owner_credential_v1(text,text) TO decision_app;
GRANT EXECUTE ON FUNCTION public.read_strong_llm_owner_key_last4_v1(text) TO decision_app;
GRANT EXECUTE ON FUNCTION public.read_strong_llm_owner_credential_v1(text,text) TO decision_app;
