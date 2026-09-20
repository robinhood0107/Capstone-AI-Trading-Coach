-- 사용자별 증권사 자격증명을 담을 자리.
--
-- 왜 있나
-- ------
-- 지금 KIS 앱키·시크릿·계좌번호는 운영자 파일 하나(.state-app/secrets/kis-mock.env)에만
-- 있다. 그래서 이 스택은 한 사람의 계좌로만 돈다. 사용자별 자동매매의 첫 칸이 이것이다.
--
-- 무엇을 하지 않나
-- ---------------
-- **아직 아무도 읽지 않는다.** 자격증명을 파일에서 DB 로 옮기는 것은 별개 작업이고, 그
-- 작업은 운용 중인 자동매매가 키를 읽는 경로를 바꾼다. 테이블만 먼저 세워 둔다.
--
-- 봉투 구조는 strong_llm_owner_credentials(V108)와 같다. 같은 종류의 비밀을 두 가지
-- 방법으로 보관하면 언젠가 한쪽만 고치게 된다.

CREATE TABLE public.user_broker_credentials (
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
  -- 모의와 실계좌를 한 테이블에 두되 절대 섞이지 않게 열쇠에 넣는다. 이 스택은 MOCK 만 쓴다.
  brokerage_mode text NOT NULL CHECK (brokerage_mode IN ('KIS_MOCK','KIS_LIVE')),
  kek_version text NOT NULL CHECK (kek_version ~ '^kek-v[1-9][0-9]{0,8}$'),
  wrap_nonce bytea NOT NULL CHECK (octet_length(wrap_nonce) = 12),
  wrapped_dek bytea NOT NULL CHECK (octet_length(wrapped_dek) = 32),
  wrap_tag bytea NOT NULL CHECK (octet_length(wrap_tag) = 16),
  -- 앱키·시크릿·계좌번호를 한 봉투에 넣는다. 셋은 항상 같이 쓰이고 따로 회전하지 않는다.
  secret_nonce bytea NOT NULL CHECK (octet_length(secret_nonce) = 12),
  secret_ciphertext bytea NOT NULL CHECK (octet_length(secret_ciphertext) BETWEEN 1 AND 8192),
  secret_tag bytea NOT NULL CHECK (octet_length(secret_tag) = 16),
  -- 화면이 "자격증명이 들어 있다"를 말하는 데 필요한 전부다. 그 이상은 돌려주지 않는다.
  app_key_last4 text NOT NULL CHECK (app_key_last4 ~ '^[A-Za-z0-9_-]{4}$'),
  account_no_last4 text NOT NULL CHECK (account_no_last4 ~ '^[0-9]{4}$'),
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (owner_user_id, brokerage_mode)
);

ALTER TABLE public.user_broker_credentials ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_broker_credentials FORCE ROW LEVEL SECURITY;

-- 키 행은 소유자 세션도 SELECT 하지 못한다. 읽는 길은 definer 함수뿐이다. 테이블을 열어
-- 두면 언젠가 조인 하나가 자격증명 봉투를 응답에 실어 나른다.
CREATE POLICY user_broker_credentials_definer_v184
ON public.user_broker_credentials TO PUBLIC
USING (current_user='flyway' AND session_user='decision_app')
WITH CHECK (current_user='flyway' AND session_user='decision_app');

CREATE OR REPLACE FUNCTION public.put_user_broker_credential_v1(
  p_owner_user_id text,
  p_brokerage_mode text,
  p_kek_version text,
  p_wrap_nonce bytea,
  p_wrapped_dek bytea,
  p_wrap_tag bytea,
  p_secret_nonce bytea,
  p_secret_ciphertext bytea,
  p_secret_tag bytea,
  p_app_key_last4 text,
  p_account_no_last4 text
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
AS $put_user_broker_credential_v1$
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_brokerage_mode NOT IN ('KIS_MOCK','KIS_LIVE') THEN
    RAISE EXCEPTION 'broker credential actor is invalid' USING ERRCODE='42501';
  END IF;
  INSERT INTO public.user_broker_credentials (
    owner_user_id, brokerage_mode, kek_version, wrap_nonce, wrapped_dek, wrap_tag,
    secret_nonce, secret_ciphertext, secret_tag, app_key_last4, account_no_last4,
    created_at, updated_at
  ) VALUES (
    p_owner_user_id, p_brokerage_mode, p_kek_version, p_wrap_nonce, p_wrapped_dek, p_wrap_tag,
    p_secret_nonce, p_secret_ciphertext, p_secret_tag, p_app_key_last4, p_account_no_last4,
    pg_catalog.now(), pg_catalog.now()
  )
  ON CONFLICT (owner_user_id, brokerage_mode) DO UPDATE SET
    kek_version=excluded.kek_version,
    wrap_nonce=excluded.wrap_nonce,
    wrapped_dek=excluded.wrapped_dek,
    wrap_tag=excluded.wrap_tag,
    secret_nonce=excluded.secret_nonce,
    secret_ciphertext=excluded.secret_ciphertext,
    secret_tag=excluded.secret_tag,
    app_key_last4=excluded.app_key_last4,
    account_no_last4=excluded.account_no_last4,
    updated_at=pg_catalog.now();
END;
$put_user_broker_credential_v1$;

CREATE OR REPLACE FUNCTION public.delete_user_broker_credential_v1(
  p_owner_user_id text,
  p_brokerage_mode text
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
AS $delete_user_broker_credential_v1$
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_brokerage_mode NOT IN ('KIS_MOCK','KIS_LIVE') THEN
    RAISE EXCEPTION 'broker credential actor is invalid' USING ERRCODE='42501';
  END IF;
  DELETE FROM public.user_broker_credentials
  WHERE owner_user_id=p_owner_user_id AND brokerage_mode=p_brokerage_mode;
END;
$delete_user_broker_credential_v1$;

-- 화면이 쓰는 읽기다. 봉투도 암호문도 나가지 않고 마지막 네 글자만 나간다.
CREATE OR REPLACE FUNCTION public.read_user_broker_credential_summary_v1(p_owner_user_id text)
RETURNS TABLE(brokerage_mode text, app_key_last4 text, account_no_last4 text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public
AS $read_user_broker_credential_summary_v1$
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id THEN
    RAISE EXCEPTION 'broker credential actor is invalid' USING ERRCODE='42501';
  END IF;
  RETURN QUERY
  SELECT item.brokerage_mode, item.app_key_last4, item.account_no_last4
  FROM public.user_broker_credentials item
  WHERE item.owner_user_id=p_owner_user_id
  ORDER BY item.brokerage_mode;
END;
$read_user_broker_credential_summary_v1$;

-- 복호화 재료를 돌려주는 유일한 길이다. 증권사 호출 직전에만 부른다.
CREATE OR REPLACE FUNCTION public.read_user_broker_credential_v1(
  p_owner_user_id text,
  p_brokerage_mode text
) RETURNS TABLE(
  kek_version text, wrap_nonce bytea, wrapped_dek bytea, wrap_tag bytea,
  secret_nonce bytea, secret_ciphertext bytea, secret_tag bytea
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public
AS $read_user_broker_credential_v1$
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_brokerage_mode NOT IN ('KIS_MOCK','KIS_LIVE') THEN
    RAISE EXCEPTION 'broker credential actor is invalid' USING ERRCODE='42501';
  END IF;
  RETURN QUERY
  SELECT item.kek_version, item.wrap_nonce, item.wrapped_dek, item.wrap_tag,
         item.secret_nonce, item.secret_ciphertext, item.secret_tag
  FROM public.user_broker_credentials item
  WHERE item.owner_user_id=p_owner_user_id AND item.brokerage_mode=p_brokerage_mode;
END;
$read_user_broker_credential_v1$;

REVOKE ALL ON FUNCTION public.put_user_broker_credential_v1(
  text, text, text, bytea, bytea, bytea, bytea, bytea, bytea, text, text
) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.delete_user_broker_credential_v1(text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.read_user_broker_credential_summary_v1(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.read_user_broker_credential_v1(text, text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION public.put_user_broker_credential_v1(
  text, text, text, bytea, bytea, bytea, bytea, bytea, bytea, text, text
) TO decision_app;
GRANT EXECUTE ON FUNCTION public.delete_user_broker_credential_v1(text, text) TO decision_app;
GRANT EXECUTE ON FUNCTION public.read_user_broker_credential_summary_v1(text) TO decision_app;
GRANT EXECUTE ON FUNCTION public.read_user_broker_credential_v1(text, text) TO decision_app;
