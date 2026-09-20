-- 일반 뉴스는 first_seen_at 30일 뒤 정리하되, 답변 이력의 실제 인용 최소 근거는 이력 만료까지 보존한다.
CREATE TABLE public.world_news_citation_pins_v1 (
  answer_id text NOT NULL,
  ordinal integer NOT NULL CHECK (ordinal BETWEEN 1 AND 5),
  document_version_id text NOT NULL,
  source_id text NOT NULL,
  canonical_url text NOT NULL,
  title text,
  bounded_quote text,
  content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  PRIMARY KEY (answer_id,ordinal),
  CHECK (num_nonnulls(title,bounded_quote)>=1),
  CHECK (expires_at>created_at)
);

CREATE TABLE public.world_news_retention_tombstones_v1 (
  document_version_id text PRIMARY KEY,
  document_id text NOT NULL,
  provider text NOT NULL,
  canonical_url_sha256 text NOT NULL CHECK (canonical_url_sha256 ~ '^[0-9a-f]{64}$'),
  content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  first_seen_at timestamptz NOT NULL,
  deleted_at timestamptz NOT NULL,
  reason_code text NOT NULL CHECK (reason_code='ORDINARY_RETENTION_30D'),
  CHECK (deleted_at>=first_seen_at)
);

CREATE INDEX world_news_citation_pins_expiry_idx
  ON public.world_news_citation_pins_v1(expires_at,document_version_id);
CREATE INDEX world_news_retention_first_seen_idx
  ON public.world_news_document_versions_v2(first_seen_at,document_version_id);

ALTER TABLE public.world_news_citation_pins_v1 OWNER TO flyway;
ALTER TABLE public.world_news_retention_tombstones_v1 OWNER TO flyway;

CREATE FUNCTION public.pin_world_news_answer_citation_v1()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $pin$
DECLARE item record;
DECLARE history_expiry timestamptz;
BEGIN
  IF NEW.citation_kind<>'PUBLIC_WEB' THEN RETURN NEW; END IF;
  SELECT expires_at INTO history_expiry FROM public.rag_v2_answer_history
  WHERE answer_id=NEW.answer_id AND owner_user_id=NEW.owner_user_id;
  SELECT version.document_version_id,version.title,version.bounded_quote,version.content_sha256
  INTO item
  FROM public.world_news_documents_v2 document
  JOIN public.world_news_document_versions_v2 version USING(document_id)
  WHERE document.source_id=NEW.source_id AND document.canonical_url=NEW.canonical_url
    AND version.available_at<=NEW.created_at
  ORDER BY version.available_at DESC,version.first_seen_at DESC,version.document_version_id DESC
  LIMIT 1;
  IF FOUND AND history_expiry>NEW.created_at THEN
    INSERT INTO public.world_news_citation_pins_v1(
      answer_id,ordinal,document_version_id,source_id,canonical_url,title,bounded_quote,
      content_sha256,expires_at,created_at
    ) VALUES (
      NEW.answer_id,NEW.ordinal,item.document_version_id,NEW.source_id,NEW.canonical_url,
      item.title,item.bounded_quote,item.content_sha256,history_expiry,NEW.created_at
    ) ON CONFLICT (answer_id,ordinal) DO NOTHING;
  END IF;
  RETURN NEW;
END $pin$;
ALTER FUNCTION public.pin_world_news_answer_citation_v1() OWNER TO flyway;
REVOKE ALL ON FUNCTION public.pin_world_news_answer_citation_v1() FROM PUBLIC;

CREATE TRIGGER rag_v2_answer_world_news_pin_v1
AFTER INSERT ON public.rag_v2_answer_citations
FOR EACH ROW EXECUTE FUNCTION public.pin_world_news_answer_citation_v1();

-- Flyway owner도 FORCE RLS를 우회하지 못한다. 기존 citation backfill 동안만 두 owner table을
-- 열고 같은 migration transaction 안에서 즉시 FORCE를 복원한다.
ALTER TABLE public.rag_v2_answer_history NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.rag_v2_answer_citations NO FORCE ROW LEVEL SECURITY;
INSERT INTO public.world_news_citation_pins_v1(
  answer_id,ordinal,document_version_id,source_id,canonical_url,title,bounded_quote,
  content_sha256,expires_at,created_at
)
SELECT citation.answer_id,citation.ordinal,version.document_version_id,citation.source_id,
  citation.canonical_url,version.title,version.bounded_quote,version.content_sha256,
  history.expires_at,citation.created_at
FROM public.rag_v2_answer_citations citation
JOIN public.rag_v2_answer_history history
  ON history.answer_id=citation.answer_id AND history.owner_user_id=citation.owner_user_id
JOIN LATERAL (
  SELECT item.document_version_id,item.title,item.bounded_quote,item.content_sha256
  FROM public.world_news_documents_v2 document
  JOIN public.world_news_document_versions_v2 item USING(document_id)
  WHERE document.source_id=citation.source_id AND document.canonical_url=citation.canonical_url
    AND item.available_at<=citation.created_at
  ORDER BY item.available_at DESC,item.first_seen_at DESC,item.document_version_id DESC LIMIT 1
) version ON true
WHERE citation.citation_kind='PUBLIC_WEB' AND history.expires_at>transaction_timestamp()
ON CONFLICT (answer_id,ordinal) DO NOTHING;
ALTER TABLE public.rag_v2_answer_history FORCE ROW LEVEL SECURITY;
ALTER TABLE public.rag_v2_answer_citations FORCE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION public.reject_world_news_v2_mutation()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $reject$
BEGIN
  IF TG_OP='DELETE' AND session_user='decision_worker'
     AND nullif(current_setting('app.world_news_retention',true),'')='apply'
     AND TG_TABLE_NAME IN ('world_news_document_versions_v2','world_news_observations_v2') THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'world-news v2 rows are append-only' USING ERRCODE='55000';
END $reject$;
ALTER FUNCTION public.reject_world_news_v2_mutation() OWNER TO flyway;

CREATE FUNCTION public.reject_tombstoned_world_news_version_v1()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $guard$
BEGIN
  IF EXISTS(
    SELECT 1 FROM public.world_news_retention_tombstones_v1 tombstone
    WHERE tombstone.document_version_id=NEW.document_version_id
      AND tombstone.content_sha256=NEW.content_sha256
  ) THEN
    RAISE EXCEPTION 'world-news retained identity was already deleted' USING ERRCODE='55000';
  END IF;
  RETURN NEW;
END $guard$;
ALTER FUNCTION public.reject_tombstoned_world_news_version_v1() OWNER TO flyway;
REVOKE ALL ON FUNCTION public.reject_tombstoned_world_news_version_v1() FROM PUBLIC;

CREATE TRIGGER world_news_tombstone_recollection_guard_v1
BEFORE INSERT ON public.world_news_document_versions_v2
FOR EACH ROW EXECUTE FUNCTION public.reject_tombstoned_world_news_version_v1();

CREATE FUNCTION public.p1_world_news_retention_v1(
  p_apply boolean,p_now timestamptz,p_limit integer
) RETURNS TABLE(scanned_count bigint,eligible_count bigint,pinned_count bigint,deleted_count bigint)
LANGUAGE plpgsql SECURITY DEFINER VOLATILE SET search_path=pg_catalog AS $retention$
DECLARE candidate_ids text[];
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_worker' OR p_apply IS NULL
     OR p_now IS NULL OR p_limit NOT BETWEEN 1 AND 1000 THEN
    RAISE EXCEPTION 'world-news retention arguments invalid' USING ERRCODE='22023';
  END IF;
  SELECT count(*) INTO scanned_count FROM public.world_news_document_versions_v2 version
  WHERE version.first_seen_at < p_now-interval '30 days';
  SELECT count(DISTINCT pin.document_version_id) INTO pinned_count
  FROM public.world_news_citation_pins_v1 pin
  JOIN public.world_news_document_versions_v2 version USING(document_version_id)
  WHERE version.first_seen_at < p_now-interval '30 days' AND pin.expires_at>p_now;
  SELECT COALESCE(array_agg(candidate.document_version_id ORDER BY candidate.first_seen_at,candidate.document_version_id),ARRAY[]::text[])
  INTO candidate_ids
  FROM (
    SELECT version.document_version_id,version.first_seen_at
    FROM public.world_news_document_versions_v2 version
    WHERE version.first_seen_at < p_now-interval '30 days'
      AND NOT EXISTS(
        SELECT 1 FROM public.world_news_citation_pins_v1 pin
        WHERE pin.document_version_id=version.document_version_id AND pin.expires_at>p_now
      )
    ORDER BY version.first_seen_at,version.document_version_id LIMIT p_limit
  ) candidate;
  eligible_count:=cardinality(candidate_ids);
  deleted_count:=0;
  IF NOT p_apply OR eligible_count=0 THEN RETURN NEXT;RETURN; END IF;
  PERFORM set_config('app.world_news_retention','apply',true);
  INSERT INTO public.world_news_retention_tombstones_v1(
    document_version_id,document_id,provider,canonical_url_sha256,content_sha256,
    first_seen_at,deleted_at,reason_code
  )
  SELECT version.document_version_id,version.document_id,document.provider,
    document.canonical_url_sha256,version.content_sha256,version.first_seen_at,p_now,
    'ORDINARY_RETENTION_30D'
  FROM public.world_news_document_versions_v2 version
  JOIN public.world_news_documents_v2 document USING(document_id)
  WHERE version.document_version_id=ANY(candidate_ids)
  ON CONFLICT (document_version_id) DO NOTHING;
  DELETE FROM public.world_news_observations_v2 observation
  WHERE observation.document_version_id=ANY(candidate_ids);
  DELETE FROM public.world_news_document_versions_v2 version
  WHERE version.document_version_id=ANY(candidate_ids);
  GET DIAGNOSTICS deleted_count=ROW_COUNT;
  DELETE FROM public.world_news_citation_pins_v1 pin WHERE pin.expires_at<=p_now;
  RETURN NEXT;
END $retention$;
ALTER FUNCTION public.p1_world_news_retention_v1(boolean,timestamptz,integer) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_world_news_retention_v1(boolean,timestamptz,integer) FROM PUBLIC;

REVOKE ALL ON TABLE public.world_news_citation_pins_v1,public.world_news_retention_tombstones_v1
  FROM PUBLIC,decision_app,decision_market_writer,decision_rag_query,decision_worker;
GRANT EXECUTE ON FUNCTION public.p1_world_news_retention_v1(boolean,timestamptz,integer)
  TO decision_worker;
