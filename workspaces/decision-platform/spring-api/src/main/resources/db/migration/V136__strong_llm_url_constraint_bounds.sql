-- baseline 복원 후 남은 row_security=off를 상속하지 않는다.
SET LOCAL row_security = on;
-- PostgreSQL 정규식 반복 상한은 255다. 기존 {3,256}은 값 검사 시 2201B로 실패한다.
-- 허용 문자와 길이 의미는 유지하되 길이는 별도 식으로 검사한다.
ALTER TABLE public.strong_llm_owner_settings DROP CONSTRAINT strong_llm_owner_settings_base_url_check;
ALTER TABLE public.strong_llm_owner_settings DROP CONSTRAINT strong_llm_owner_settings_fallback_base_url_check;
ALTER TABLE public.strong_llm_owner_settings ADD CONSTRAINT strong_llm_owner_settings_base_url_check
 CHECK(base_url IS NULL OR (char_length(base_url) BETWEEN 11 AND 264
  AND base_url ~ '^https://[A-Za-z0-9._~:/?#@!$&()*+,;=%-]+$'));
ALTER TABLE public.strong_llm_owner_settings ADD CONSTRAINT strong_llm_owner_settings_fallback_base_url_check
 CHECK(fallback_base_url IS NULL OR (char_length(fallback_base_url) BETWEEN 11 AND 264
  AND fallback_base_url ~ '^https://[A-Za-z0-9._~:/?#@!$&()*+,;=%-]+$'));
