-- 세계 뉴스는 기사 원문이나 provider raw body 없이 bounded metadata/version/observation만 보존한다.
CREATE TABLE public.world_news_documents_v2 (
  document_id text PRIMARY KEY,
  source_id text NOT NULL,
  provider text NOT NULL,
  canonical_url text NOT NULL,
  canonical_url_sha256 text NOT NULL,
  republication_of_document_id text REFERENCES public.world_news_documents_v2(document_id) ON DELETE RESTRICT,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT world_news_document_id_check CHECK (document_id ~ '^news_doc_[0-9a-f]{32}$'),
  CONSTRAINT world_news_source_id_check CHECK (source_id ~ '^src_[a-z0-9][a-z0-9_-]{2,95}$'),
  CONSTRAINT world_news_provider_check CHECK (provider IN ('GDELT_GQG','GDELT_GEMG','FINNHUB_MARKET_NEWS')),
  CONSTRAINT world_news_url_check CHECK (
    octet_length(canonical_url) BETWEEN 9 AND 2048
    AND canonical_url ~ '^https://[^/#?]+(?:/[^#]*)?$'
  ),
  CONSTRAINT world_news_url_hash_check CHECK (canonical_url_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT world_news_no_self_lineage_check CHECK (republication_of_document_id IS DISTINCT FROM document_id),
  CONSTRAINT world_news_provider_url_unique UNIQUE (provider, canonical_url_sha256)
);

CREATE TABLE public.world_news_document_versions_v2 (
  document_version_id text PRIMARY KEY,
  document_id text NOT NULL REFERENCES public.world_news_documents_v2(document_id) ON DELETE RESTRICT,
  provider_document_id text,
  identity_status text NOT NULL,
  title text,
  bounded_quote text,
  bounded_passage text,
  canonical_content text NOT NULL,
  language text NOT NULL,
  published_at timestamptz,
  publication_status text NOT NULL,
  first_seen_at timestamptz NOT NULL,
  available_at timestamptz NOT NULL,
  rights_profile text NOT NULL,
  external_llm_allowed boolean NOT NULL,
  lookup_allowed boolean NOT NULL,
  rag_retrieval_allowed boolean NOT NULL,
  prompt_untrusted boolean NOT NULL,
  collection_status text NOT NULL,
  content_sha256 text NOT NULL,
  version_sha256 text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT world_news_version_id_check CHECK (document_version_id ~ '^news_ver_[0-9a-f]{32}$'),
  CONSTRAINT world_news_provider_document_id_check CHECK (
    provider_document_id IS NULL OR (
      char_length(provider_document_id) BETWEEN 1 AND 256
      AND provider_document_id ~ '^[A-Za-z0-9._:-]+$'
    )
  ),
  CONSTRAINT world_news_identity_status_check CHECK (
    identity_status IN ('VERIFIED','PROVIDER_ID_CONFLICT','URL_HASH_CONFLICT')
  ),
  CONSTRAINT world_news_title_check CHECK (
    title IS NULL OR (char_length(title) BETWEEN 1 AND 300 AND octet_length(title) <= 1024 AND title !~ '[[:cntrl:]]')
  ),
  CONSTRAINT world_news_quote_check CHECK (
    bounded_quote IS NULL OR (char_length(bounded_quote) BETWEEN 1 AND 600 AND bounded_quote !~ '[[:cntrl:]]')
  ),
  CONSTRAINT world_news_passage_check CHECK (
    bounded_passage IS NULL OR (char_length(bounded_passage) BETWEEN 1 AND 1200 AND bounded_passage !~ '[[:cntrl:]]')
  ),
  CONSTRAINT world_news_content_check CHECK (
    octet_length(canonical_content) BETWEEN 1 AND 4096
    AND num_nonnulls(title,bounded_quote,bounded_passage) >= 1
  ),
  CONSTRAINT world_news_language_check CHECK (language ~ '^[a-z]{2,3}(-[A-Z]{2})?$'),
  CONSTRAINT world_news_publication_status_check CHECK (
    publication_status IN ('VERIFIED','MISSING','CONFLICT')
    AND (publication_status <> 'MISSING' OR published_at IS NULL)
    AND (publication_status <> 'VERIFIED' OR published_at IS NOT NULL)
  ),
  CONSTRAINT world_news_clock_check CHECK (first_seen_at <= available_at),
  CONSTRAINT world_news_rights_check CHECK (
    rights_profile IN ('GDELT_METADATA_QUOTE','FINNHUB_PERSONAL_LOCAL')
    AND (rights_profile <> 'FINNHUB_PERSONAL_LOCAL' OR external_llm_allowed = false)
  ),
  CONSTRAINT world_news_retrieval_flags_check CHECK (lookup_allowed AND rag_retrieval_allowed AND prompt_untrusted),
  CONSTRAINT world_news_collection_status_check CHECK (
    collection_status IN ('COMPLETE','PARTIAL','COLLECTION_FAILED','NOT_COLLECTED')
  ),
  CONSTRAINT world_news_hashes_check CHECK (
    content_sha256 ~ '^[0-9a-f]{64}$' AND version_sha256 ~ '^[0-9a-f]{64}$'
  ),
  CONSTRAINT world_news_document_version_unique UNIQUE (document_id, version_sha256)
);

CREATE TABLE public.world_news_observations_v2 (
  document_version_id text NOT NULL REFERENCES public.world_news_document_versions_v2(document_version_id) ON DELETE RESTRICT,
  provider_document_id text,
  identity_status text NOT NULL,
  provider_observed_at timestamptz NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  PRIMARY KEY (document_version_id, provider_observed_at),
  CONSTRAINT world_news_observation_provider_id_check CHECK (
    provider_document_id IS NULL OR (
      char_length(provider_document_id) BETWEEN 1 AND 256
      AND provider_document_id ~ '^[A-Za-z0-9._:-]+$'
    )
  ),
  CONSTRAINT world_news_observation_identity_check CHECK (
    identity_status IN ('VERIFIED','PROVIDER_ID_CONFLICT','URL_HASH_CONFLICT')
  )
);

CREATE TABLE public.world_news_identity_conflicts_v2 (
  conflict_id text PRIMARY KEY,
  provider text NOT NULL,
  provider_document_id text NOT NULL,
  existing_document_id text NOT NULL REFERENCES public.world_news_documents_v2(document_id) ON DELETE RESTRICT,
  observed_document_id text NOT NULL REFERENCES public.world_news_documents_v2(document_id) ON DELETE RESTRICT,
  observed_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT world_news_conflict_id_check CHECK (conflict_id ~ '^news_conf_[0-9a-f]{32}$'),
  CONSTRAINT world_news_conflict_documents_check CHECK (existing_document_id <> observed_document_id)
);

CREATE TABLE public.world_news_collection_runs_v2 (
  collection_id text PRIMARY KEY,
  provider text NOT NULL,
  collection_status text NOT NULL,
  started_at timestamptz NOT NULL,
  completed_at timestamptz,
  observed_through timestamptz,
  item_count integer NOT NULL,
  cursor_sha256 text,
  error_code text,
  previous_collection_id text REFERENCES public.world_news_collection_runs_v2(collection_id) ON DELETE RESTRICT,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT world_news_collection_id_check CHECK (collection_id ~ '^news_col_[0-9a-f]{32}$'),
  CONSTRAINT world_news_collection_provider_check CHECK (
    provider IN ('GDELT_GQG','GDELT_GEMG','FINNHUB_MARKET_NEWS')
  ),
  CONSTRAINT world_news_collection_state_check CHECK (
    collection_status IN ('COMPLETE','PARTIAL','COLLECTION_FAILED','NOT_COLLECTED')
    AND (collection_status <> 'COMPLETE' OR (completed_at IS NOT NULL AND error_code IS NULL))
    AND (collection_status NOT IN ('COLLECTION_FAILED','NOT_COLLECTED') OR error_code IS NOT NULL)
  ),
  CONSTRAINT world_news_collection_time_check CHECK (completed_at IS NULL OR completed_at >= started_at),
  CONSTRAINT world_news_collection_count_check CHECK (item_count BETWEEN 0 AND 100000),
  CONSTRAINT world_news_collection_cursor_check CHECK (cursor_sha256 IS NULL OR cursor_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT world_news_collection_error_check CHECK (error_code IS NULL OR error_code ~ '^[A-Z0-9_]{1,96}$')
);

CREATE INDEX world_news_lookup_idx
  ON public.world_news_document_versions_v2 (available_at DESC, first_seen_at DESC, document_version_id);
CREATE INDEX world_news_observation_latest_idx
  ON public.world_news_observations_v2 (document_version_id, provider_observed_at DESC);
CREATE INDEX world_news_provider_document_idx
  ON public.world_news_observations_v2 (provider_document_id, provider_observed_at DESC)
  WHERE provider_document_id IS NOT NULL;

ALTER TABLE public.world_news_documents_v2 OWNER TO flyway;
ALTER TABLE public.world_news_document_versions_v2 OWNER TO flyway;
ALTER TABLE public.world_news_observations_v2 OWNER TO flyway;
ALTER TABLE public.world_news_identity_conflicts_v2 OWNER TO flyway;
ALTER TABLE public.world_news_collection_runs_v2 OWNER TO flyway;

CREATE FUNCTION public.reject_world_news_v2_mutation()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $reject$
BEGIN
  RAISE EXCEPTION 'world-news v2 rows are append-only' USING ERRCODE='55000';
END $reject$;
ALTER FUNCTION public.reject_world_news_v2_mutation() OWNER TO flyway;
REVOKE ALL ON FUNCTION public.reject_world_news_v2_mutation() FROM PUBLIC;

CREATE TRIGGER world_news_documents_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
  ON public.world_news_documents_v2 FOR EACH STATEMENT EXECUTE FUNCTION public.reject_world_news_v2_mutation();
CREATE TRIGGER world_news_versions_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
  ON public.world_news_document_versions_v2 FOR EACH STATEMENT EXECUTE FUNCTION public.reject_world_news_v2_mutation();
CREATE TRIGGER world_news_observations_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
  ON public.world_news_observations_v2 FOR EACH STATEMENT EXECUTE FUNCTION public.reject_world_news_v2_mutation();
CREATE TRIGGER world_news_conflicts_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
  ON public.world_news_identity_conflicts_v2 FOR EACH STATEMENT EXECUTE FUNCTION public.reject_world_news_v2_mutation();
CREATE TRIGGER world_news_collections_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
  ON public.world_news_collection_runs_v2 FOR EACH STATEMENT EXECUTE FUNCTION public.reject_world_news_v2_mutation();

CREATE FUNCTION public.append_world_news_document_v2(p_payload jsonb)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER VOLATILE SET search_path=pg_catalog AS $append$
DECLARE
  existing_document public.world_news_documents_v2%ROWTYPE;
  existing_version public.world_news_document_versions_v2%ROWTYPE;
  provider_collision text;
  effective_identity_status text;
  version_inserted integer := 0;
  observation_inserted integer := 0;
  expected_content text;
  expected_document_id text;
  generated_conflict_id text;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_market_writer'
     OR jsonb_typeof(p_payload) <> 'object'
     OR NOT p_payload ?& ARRAY[
       'documentId','documentVersionId','sourceId','provider','providerDocumentId','canonicalUrl',
       'canonicalUrlSha256','republicationOfDocumentId','identityStatus','title','boundedQuote',
       'boundedPassage','canonicalContent','language','publishedAt','publicationStatus',
       'providerObservedAt','firstSeenAt','availableAt','rightsProfile','externalLlmAllowed',
       'lookupAllowed','ragRetrievalAllowed','promptUntrusted','collectionStatus','contentSha256','versionSha256'
     ]
     OR p_payload - ARRAY[
       'documentId','documentVersionId','sourceId','provider','providerDocumentId','canonicalUrl',
       'canonicalUrlSha256','republicationOfDocumentId','identityStatus','title','boundedQuote',
       'boundedPassage','canonicalContent','language','publishedAt','publicationStatus',
       'providerObservedAt','firstSeenAt','availableAt','rightsProfile','externalLlmAllowed',
       'lookupAllowed','ragRetrievalAllowed','promptUntrusted','collectionStatus','contentSha256','versionSha256'
     ] <> '{}'::jsonb THEN
    RAISE EXCEPTION 'world-news payload shape invalid' USING ERRCODE='22023';
  END IF;
  BEGIN
    PERFORM (p_payload->>'providerObservedAt')::timestamptz;
    PERFORM (p_payload->>'firstSeenAt')::timestamptz;
    PERFORM (p_payload->>'availableAt')::timestamptz;
    IF p_payload->'publishedAt' <> 'null'::jsonb THEN PERFORM (p_payload->>'publishedAt')::timestamptz; END IF;
  EXCEPTION WHEN others THEN
    RAISE EXCEPTION 'world-news time invalid' USING ERRCODE='22023';
  END;
  IF p_payload->>'documentId' !~ '^news_doc_[0-9a-f]{32}$'
     OR p_payload->>'documentVersionId' !~ '^news_ver_[0-9a-f]{32}$'
     OR p_payload->>'sourceId' !~ '^src_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_payload->>'provider' NOT IN ('GDELT_GQG','GDELT_GEMG','FINNHUB_MARKET_NEWS')
     OR p_payload->>'canonicalUrl' !~ '^https://[^/#?]+(?:/[^#]*)?$'
     OR octet_length(p_payload->>'canonicalUrl') > 2048
     OR p_payload->>'canonicalUrlSha256' !~ '^[0-9a-f]{64}$'
     OR encode(public.digest(convert_to(p_payload->>'canonicalUrl','UTF8'),'sha256'),'hex') <> p_payload->>'canonicalUrlSha256'
     OR p_payload->>'identityStatus' NOT IN ('VERIFIED','PROVIDER_ID_CONFLICT','URL_HASH_CONFLICT')
     OR p_payload->>'publicationStatus' NOT IN ('VERIFIED','MISSING','CONFLICT')
     OR p_payload->>'rightsProfile' NOT IN ('GDELT_METADATA_QUOTE','FINNHUB_PERSONAL_LOCAL')
     OR p_payload->>'collectionStatus' NOT IN ('COMPLETE','PARTIAL','COLLECTION_FAILED','NOT_COLLECTED')
     OR p_payload->>'contentSha256' !~ '^[0-9a-f]{64}$'
     OR p_payload->>'versionSha256' !~ '^[0-9a-f]{64}$'
     OR p_payload->>'documentVersionId' <> 'news_ver_'||left(p_payload->>'versionSha256',32)
     OR p_payload->>'language' !~ '^[a-z]{2,3}(-[A-Z]{2})?$'
     OR jsonb_typeof(p_payload->'externalLlmAllowed') <> 'boolean'
     OR jsonb_typeof(p_payload->'lookupAllowed') <> 'boolean'
     OR jsonb_typeof(p_payload->'ragRetrievalAllowed') <> 'boolean'
     OR jsonb_typeof(p_payload->'promptUntrusted') <> 'boolean'
     OR p_payload->'lookupAllowed' <> 'true'::jsonb
     OR p_payload->'ragRetrievalAllowed' <> 'true'::jsonb
     OR p_payload->'promptUntrusted' <> 'true'::jsonb THEN
    RAISE EXCEPTION 'world-news payload values invalid' USING ERRCODE='22023';
  END IF;
  expected_document_id := 'news_doc_'||left(encode(public.digest(convert_to(
    'world-news/v2|'||(p_payload->>'provider')||'|'||(p_payload->>'canonicalUrlSha256'),'UTF8'),'sha256'),'hex'),32);
  IF expected_document_id <> p_payload->>'documentId' THEN
    RAISE EXCEPTION 'world-news document identity invalid' USING ERRCODE='22023';
  END IF;
  expected_content := concat_ws(E'\n',
    NULLIF(p_payload->>'title',''),NULLIF(p_payload->>'boundedQuote',''),NULLIF(p_payload->>'boundedPassage',''));
  IF expected_content = '' OR expected_content <> p_payload->>'canonicalContent'
     OR octet_length(expected_content) > 4096
     OR encode(public.digest(convert_to(expected_content,'UTF8'),'sha256'),'hex') <> p_payload->>'contentSha256'
     OR (p_payload->'title' <> 'null'::jsonb AND (char_length(p_payload->>'title') NOT BETWEEN 1 AND 300 OR octet_length(p_payload->>'title') > 1024))
     OR (p_payload->'boundedQuote' <> 'null'::jsonb AND char_length(p_payload->>'boundedQuote') NOT BETWEEN 1 AND 600)
     OR (p_payload->'boundedPassage' <> 'null'::jsonb AND char_length(p_payload->>'boundedPassage') NOT BETWEEN 1 AND 1200)
     OR (p_payload->>'publicationStatus'='MISSING' AND p_payload->'publishedAt'<>'null'::jsonb)
     OR (p_payload->>'publicationStatus'='VERIFIED' AND p_payload->'publishedAt'='null'::jsonb)
     OR (p_payload->>'publicationStatus'='VERIFIED' AND (p_payload->>'publishedAt')::timestamptz > (p_payload->>'providerObservedAt')::timestamptz)
     OR (p_payload->>'firstSeenAt')::timestamptz > (p_payload->>'availableAt')::timestamptz
     OR (p_payload->>'provider'='FINNHUB_MARKET_NEWS' AND (p_payload->>'rightsProfile'<>'FINNHUB_PERSONAL_LOCAL' OR (p_payload->>'externalLlmAllowed')::boolean))
     OR (p_payload->>'provider'<>'FINNHUB_MARKET_NEWS' AND p_payload->>'rightsProfile'<>'GDELT_METADATA_QUOTE') THEN
    RAISE EXCEPTION 'world-news bounded content or rights invalid' USING ERRCODE='22023';
  END IF;

  IF p_payload->'republicationOfDocumentId' <> 'null'::jsonb AND NOT EXISTS (
    SELECT 1 FROM public.world_news_documents_v2 WHERE document_id=p_payload->>'republicationOfDocumentId'
  ) THEN RAISE EXCEPTION 'world-news lineage missing' USING ERRCODE='22023'; END IF;

  SELECT * INTO existing_document FROM public.world_news_documents_v2
  WHERE provider=p_payload->>'provider' AND canonical_url_sha256=p_payload->>'canonicalUrlSha256';
  IF FOUND AND (existing_document.document_id<>p_payload->>'documentId'
    OR existing_document.source_id<>p_payload->>'sourceId'
    OR existing_document.canonical_url<>p_payload->>'canonicalUrl'
    OR existing_document.republication_of_document_id IS DISTINCT FROM NULLIF(p_payload->>'republicationOfDocumentId','')) THEN
    RAISE EXCEPTION 'world-news URL identity conflict' USING ERRCODE='23505';
  END IF;
  IF NOT FOUND THEN
    INSERT INTO public.world_news_documents_v2(
      document_id,source_id,provider,canonical_url,canonical_url_sha256,republication_of_document_id
    ) VALUES (
      p_payload->>'documentId',p_payload->>'sourceId',p_payload->>'provider',p_payload->>'canonicalUrl',
      p_payload->>'canonicalUrlSha256',NULLIF(p_payload->>'republicationOfDocumentId','')
    );
  END IF;

  IF p_payload->'providerDocumentId' <> 'null'::jsonb THEN
    SELECT document.document_id INTO provider_collision
    FROM public.world_news_observations_v2 observation
    JOIN public.world_news_document_versions_v2 version USING(document_version_id)
    JOIN public.world_news_documents_v2 document USING(document_id)
    WHERE document.provider=p_payload->>'provider'
      AND observation.provider_document_id=p_payload->>'providerDocumentId'
      AND document.document_id<>p_payload->>'documentId'
    ORDER BY observation.provider_observed_at LIMIT 1;
  END IF;
  effective_identity_status := CASE WHEN provider_collision IS NOT NULL
    THEN 'PROVIDER_ID_CONFLICT' ELSE p_payload->>'identityStatus' END;

  INSERT INTO public.world_news_document_versions_v2(
    document_version_id,document_id,provider_document_id,identity_status,title,bounded_quote,bounded_passage,
    canonical_content,language,published_at,publication_status,first_seen_at,available_at,rights_profile,
    external_llm_allowed,lookup_allowed,rag_retrieval_allowed,prompt_untrusted,collection_status,
    content_sha256,version_sha256
  ) VALUES (
    p_payload->>'documentVersionId',p_payload->>'documentId',NULLIF(p_payload->>'providerDocumentId',''),
    effective_identity_status,NULLIF(p_payload->>'title',''),NULLIF(p_payload->>'boundedQuote',''),
    NULLIF(p_payload->>'boundedPassage',''),expected_content,p_payload->>'language',
    CASE WHEN p_payload->'publishedAt'='null'::jsonb THEN NULL ELSE (p_payload->>'publishedAt')::timestamptz END,
    p_payload->>'publicationStatus',(p_payload->>'firstSeenAt')::timestamptz,(p_payload->>'availableAt')::timestamptz,
    p_payload->>'rightsProfile',(p_payload->>'externalLlmAllowed')::boolean,true,true,true,
    p_payload->>'collectionStatus',p_payload->>'contentSha256',p_payload->>'versionSha256'
  ) ON CONFLICT (document_version_id) DO NOTHING;
  GET DIAGNOSTICS version_inserted=ROW_COUNT;
  IF version_inserted=0 THEN
    SELECT * INTO existing_version FROM public.world_news_document_versions_v2
    WHERE document_version_id=p_payload->>'documentVersionId';
    IF existing_version.document_id<>p_payload->>'documentId'
       OR existing_version.version_sha256<>p_payload->>'versionSha256'
       OR existing_version.canonical_content<>expected_content THEN
      RAISE EXCEPTION 'world-news version identity conflict' USING ERRCODE='23505';
    END IF;
  END IF;

  INSERT INTO public.world_news_observations_v2(
    document_version_id,provider_document_id,identity_status,provider_observed_at
  ) VALUES (
    p_payload->>'documentVersionId',NULLIF(p_payload->>'providerDocumentId',''),effective_identity_status,
    (p_payload->>'providerObservedAt')::timestamptz
  ) ON CONFLICT (document_version_id,provider_observed_at) DO NOTHING;
  GET DIAGNOSTICS observation_inserted=ROW_COUNT;

  IF provider_collision IS NOT NULL THEN
    generated_conflict_id := 'news_conf_'||left(encode(public.digest(convert_to(
      (p_payload->>'provider')||'|'||(p_payload->>'providerDocumentId')||'|'||provider_collision||'|'||(p_payload->>'documentId'),'UTF8'),
      'sha256'),'hex'),32);
    INSERT INTO public.world_news_identity_conflicts_v2(
      conflict_id,provider,provider_document_id,existing_document_id,observed_document_id,observed_at
    ) VALUES (
      generated_conflict_id,p_payload->>'provider',p_payload->>'providerDocumentId',provider_collision,
      p_payload->>'documentId',(p_payload->>'providerObservedAt')::timestamptz
    ) ON CONFLICT (conflict_id) DO NOTHING;
    RETURN 'IDENTITY_CONFLICT';
  END IF;
  IF version_inserted=1 THEN RETURN 'INSERTED'; END IF;
  IF observation_inserted=1 THEN RETURN 'OBSERVED'; END IF;
  RETURN 'NO_OP';
END $append$;
ALTER FUNCTION public.append_world_news_document_v2(jsonb) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.append_world_news_document_v2(jsonb) FROM PUBLIC;

CREATE FUNCTION public.append_world_news_collection_v2(p_payload jsonb)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER VOLATILE SET search_path=pg_catalog AS $collection$
DECLARE inserted integer;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_market_writer'
     OR jsonb_typeof(p_payload)<>'object'
     OR NOT p_payload ?& ARRAY['collectionId','provider','collectionStatus','startedAt','completedAt',
       'observedThrough','itemCount','cursorSha256','errorCode','previousCollectionId']
     OR p_payload-ARRAY['collectionId','provider','collectionStatus','startedAt','completedAt',
       'observedThrough','itemCount','cursorSha256','errorCode','previousCollectionId']<>'{}'::jsonb THEN
    RAISE EXCEPTION 'world-news collection shape invalid' USING ERRCODE='22023'; END IF;
  BEGIN
    PERFORM (p_payload->>'startedAt')::timestamptz;
    IF p_payload->'completedAt'<>'null'::jsonb THEN PERFORM (p_payload->>'completedAt')::timestamptz; END IF;
    IF p_payload->'observedThrough'<>'null'::jsonb THEN PERFORM (p_payload->>'observedThrough')::timestamptz; END IF;
  EXCEPTION WHEN others THEN RAISE EXCEPTION 'world-news collection time invalid' USING ERRCODE='22023'; END;
  IF p_payload->>'collectionId'!~'^news_col_[0-9a-f]{32}$'
     OR p_payload->>'provider' NOT IN ('GDELT_GQG','GDELT_GEMG','FINNHUB_MARKET_NEWS')
     OR p_payload->>'collectionStatus' NOT IN ('COMPLETE','PARTIAL','COLLECTION_FAILED','NOT_COLLECTED')
     OR jsonb_typeof(p_payload->'itemCount')<>'number'
     OR (p_payload->>'itemCount')::integer NOT BETWEEN 0 AND 100000
     OR (p_payload->'cursorSha256'<>'null'::jsonb AND p_payload->>'cursorSha256'!~'^[0-9a-f]{64}$')
     OR (p_payload->'errorCode'<>'null'::jsonb AND p_payload->>'errorCode'!~'^[A-Z0-9_]{1,96}$')
     OR (p_payload->>'collectionStatus'='COMPLETE' AND (p_payload->'completedAt'='null'::jsonb OR p_payload->'errorCode'<>'null'::jsonb))
     OR (p_payload->>'collectionStatus' IN ('COLLECTION_FAILED','NOT_COLLECTED') AND p_payload->'errorCode'='null'::jsonb)
     OR (p_payload->'previousCollectionId'<>'null'::jsonb AND NOT EXISTS(
       SELECT 1 FROM public.world_news_collection_runs_v2 WHERE collection_id=p_payload->>'previousCollectionId')) THEN
    RAISE EXCEPTION 'world-news collection values invalid' USING ERRCODE='22023'; END IF;
  INSERT INTO public.world_news_collection_runs_v2(
    collection_id,provider,collection_status,started_at,completed_at,observed_through,item_count,
    cursor_sha256,error_code,previous_collection_id
  ) VALUES (
    p_payload->>'collectionId',p_payload->>'provider',p_payload->>'collectionStatus',
    (p_payload->>'startedAt')::timestamptz,
    CASE WHEN p_payload->'completedAt'='null'::jsonb THEN NULL ELSE (p_payload->>'completedAt')::timestamptz END,
    CASE WHEN p_payload->'observedThrough'='null'::jsonb THEN NULL ELSE (p_payload->>'observedThrough')::timestamptz END,
    (p_payload->>'itemCount')::integer,NULLIF(p_payload->>'cursorSha256',''),NULLIF(p_payload->>'errorCode',''),
    NULLIF(p_payload->>'previousCollectionId','')
  ) ON CONFLICT (collection_id) DO NOTHING;
  GET DIAGNOSTICS inserted=ROW_COUNT;
  IF inserted=1 THEN RETURN 'INSERTED'; END IF;
  IF EXISTS(SELECT 1 FROM public.world_news_collection_runs_v2 run WHERE run.collection_id=p_payload->>'collectionId'
    AND run.provider=p_payload->>'provider' AND run.collection_status=p_payload->>'collectionStatus'
    AND run.item_count=(p_payload->>'itemCount')::integer AND run.cursor_sha256 IS NOT DISTINCT FROM NULLIF(p_payload->>'cursorSha256','')
    AND run.error_code IS NOT DISTINCT FROM NULLIF(p_payload->>'errorCode','')) THEN RETURN 'NO_OP'; END IF;
  RAISE EXCEPTION 'world-news collection identity conflict' USING ERRCODE='23505';
END $collection$;
ALTER FUNCTION public.append_world_news_collection_v2(jsonb) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.append_world_news_collection_v2(jsonb) FROM PUBLIC;

CREATE FUNCTION public.read_world_news_documents_v2(p_query text,p_as_of timestamptz,p_limit integer)
RETURNS TABLE(
  document_id text,document_version_id text,source_id text,provider text,provider_document_id text,
  canonical_url text,republication_of_document_id text,identity_status text,title text,bounded_quote text,
  bounded_passage text,language text,published_at timestamptz,publication_status text,
  provider_observed_at timestamptz,first_seen_at timestamptz,available_at timestamptz,rights_profile text,
  external_llm_allowed boolean,lookup_allowed boolean,rag_retrieval_allowed boolean,prompt_untrusted boolean,
  collection_status text,content_sha256 text,version_sha256 text
) LANGUAGE plpgsql SECURITY DEFINER STABLE SET search_path=pg_catalog AS $read$
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_app' OR p_query IS NULL OR p_as_of IS NULL
     OR char_length(p_query)>200
     OR p_limit NOT BETWEEN 1 AND 50 THEN
    RAISE EXCEPTION 'world-news lookup arguments invalid' USING ERRCODE='22023'; END IF;
  RETURN QUERY
  WITH latest_version AS (
    SELECT DISTINCT ON (version.document_id) version.*
    FROM public.world_news_document_versions_v2 version
    WHERE version.available_at<=p_as_of AND version.lookup_allowed
    ORDER BY version.document_id,version.available_at DESC,version.first_seen_at DESC,version.document_version_id DESC
  ), latest_observation AS (
    SELECT DISTINCT ON (observation.document_version_id) observation.*
    FROM public.world_news_observations_v2 observation
    ORDER BY observation.document_version_id,observation.provider_observed_at DESC
  )
  SELECT document.document_id,version.document_version_id,document.source_id,document.provider,
    observation.provider_document_id,document.canonical_url,document.republication_of_document_id,
    observation.identity_status,version.title,version.bounded_quote,version.bounded_passage,version.language,
    version.published_at,version.publication_status,observation.provider_observed_at,version.first_seen_at,
    version.available_at,version.rights_profile,version.external_llm_allowed,version.lookup_allowed,
    version.rag_retrieval_allowed,version.prompt_untrusted,version.collection_status,
    version.content_sha256,version.version_sha256
  FROM latest_version version
  JOIN public.world_news_documents_v2 document USING(document_id)
  JOIN latest_observation observation USING(document_version_id)
  WHERE btrim(p_query)=''
     OR concat_ws(' ',version.title,version.bounded_quote,version.bounded_passage,document.provider) ILIKE '%'||p_query||'%'
  ORDER BY COALESCE(version.published_at,version.first_seen_at) DESC,version.first_seen_at DESC,document.document_id
  LIMIT p_limit;
END $read$;
ALTER FUNCTION public.read_world_news_documents_v2(text,timestamptz,integer) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.read_world_news_documents_v2(text,timestamptz,integer) FROM PUBLIC;

CREATE FUNCTION public.read_world_news_collection_status_v2()
RETURNS TABLE(
  provider text,collection_status text,started_at timestamptz,completed_at timestamptz,
  observed_through timestamptz,item_count integer,error_code text
) LANGUAGE plpgsql SECURITY DEFINER STABLE SET search_path=pg_catalog AS $status$
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_app' THEN
    RAISE EXCEPTION 'world-news collection status denied' USING ERRCODE='42501'; END IF;
  RETURN QUERY
  SELECT DISTINCT ON (run.provider) run.provider,run.collection_status,run.started_at,run.completed_at,
    run.observed_through,run.item_count,run.error_code
  FROM public.world_news_collection_runs_v2 run
  ORDER BY run.provider,run.started_at DESC,run.collection_id DESC;
END $status$;
ALTER FUNCTION public.read_world_news_collection_status_v2() OWNER TO flyway;
REVOKE ALL ON FUNCTION public.read_world_news_collection_status_v2() FROM PUBLIC;

-- 기존 immutable RAG bundle에 쓰지 않고, valid scope에 결속된 별도 lexical channel로만 읽는다.
CREATE FUNCTION public.search_authorized_world_news_rag_v2(
  p_scope_claim_id text,p_owner_user_id text,p_session_id text,p_topics text[],p_query_text text
) RETURNS TABLE(
  rank_no integer,canonical_content text,canonical_content_sha256 text,canonical_https_url text,
  chunk_id text,document_id text,embedding_profile_id text,external_processing_eligible boolean,
  generation_id text,heading_path text[],locator jsonb,candidate_owner_user_id text,policy_version bigint,
  sanitized_display_name text,scope_claim_id text,session_id text,source_id text,source_revision_id text,
  source_scope text,citation_title text,retrieval_topics text[]
) LANGUAGE plpgsql SECURITY DEFINER VOLATILE SET search_path=pg_catalog AS $rag$
DECLARE scope_row record;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_rag_query' OR p_query_text IS NULL
     OR char_length(p_query_text) NOT BETWEEN 1 AND 1000 OR p_topics IS NULL
     OR NOT ('DATA'=ANY(p_topics)) THEN
    RETURN; END IF;
  SELECT * INTO scope_row FROM public.read_rag_v2_retrieval_scope_v2(
    p_scope_claim_id,p_owner_user_id,p_session_id
  );
  IF NOT FOUND OR NOT ('DATA'=ANY(scope_row.allowed_topics)) THEN RETURN; END IF;
  RETURN QUERY
  WITH latest_version AS (
    SELECT DISTINCT ON (version.document_id) version.*
    FROM public.world_news_document_versions_v2 version
    WHERE version.available_at<=statement_timestamp() AND version.rag_retrieval_allowed
    ORDER BY version.document_id,version.available_at DESC,version.first_seen_at DESC,version.document_version_id DESC
  ), latest_observation AS (
    SELECT DISTINCT ON (observation.document_version_id) observation.*
    FROM public.world_news_observations_v2 observation
    ORDER BY observation.document_version_id,observation.provider_observed_at DESC
  ), candidates AS (
    SELECT version.*,document.source_id,document.canonical_url,
      similarity(lower(version.canonical_content),lower(p_query_text)) AS score
    FROM latest_version version JOIN public.world_news_documents_v2 document USING(document_id)
    JOIN latest_observation observation USING(document_version_id)
    WHERE version.canonical_content ILIKE '%'||p_query_text||'%'
       OR similarity(lower(version.canonical_content),lower(p_query_text))>=0.05
  )
  SELECT row_number() OVER(ORDER BY candidate.score DESC,candidate.first_seen_at DESC,candidate.document_version_id)::integer,
    candidate.canonical_content,candidate.content_sha256,candidate.canonical_url,
    'rag_v2_chk_'||left(candidate.version_sha256,32),NULL::text,scope_row.embedding_profile_id,
    candidate.external_llm_allowed,scope_row.oa112_generation_id,ARRAY['세계 뉴스']::text[],
    jsonb_build_object('section','world-news-v2'),NULL::text,scope_row.policy_version,NULL::text,
    p_scope_claim_id,p_session_id,candidate.source_id,'srv_news_'||left(candidate.version_sha256,32),
    'WORLD_NEWS',COALESCE(candidate.title,'세계 뉴스'),ARRAY['DATA']::text[]
  FROM candidates candidate ORDER BY candidate.score DESC,candidate.first_seen_at DESC,candidate.document_version_id LIMIT 10;
END $rag$;
ALTER FUNCTION public.search_authorized_world_news_rag_v2(text,text,text,text[],text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.search_authorized_world_news_rag_v2(text,text,text,text[],text) FROM PUBLIC;

REVOKE ALL ON TABLE public.world_news_documents_v2,public.world_news_document_versions_v2,
  public.world_news_observations_v2,public.world_news_identity_conflicts_v2,public.world_news_collection_runs_v2
FROM PUBLIC,decision_app,decision_market_writer,decision_rag_query;

DO $grants$
BEGIN
  IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='decision_market_writer') THEN
    GRANT EXECUTE ON FUNCTION public.append_world_news_document_v2(jsonb),
      public.append_world_news_collection_v2(jsonb) TO decision_market_writer;
  END IF;
  IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='decision_app') THEN
    GRANT EXECUTE ON FUNCTION public.read_world_news_documents_v2(text,timestamptz,integer),
      public.read_world_news_collection_status_v2() TO decision_app;
  END IF;
  IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='decision_rag_query') THEN
    GRANT EXECUTE ON FUNCTION public.search_authorized_world_news_rag_v2(text,text,text,text[],text) TO decision_rag_query;
  END IF;
END $grants$;

-- RAG history의 기존 immutable citation recheck는 보존하고, world-news 한 건씩만 새 경계에서 합성한다.
ALTER FUNCTION public.canonicalize_rag_v2_immutable_retrieval_citations(text,text,text,jsonb)
  RENAME TO canonicalize_rag_v2_immutable_retrieval_citations_pre_world_news_v157;
REVOKE ALL ON FUNCTION public.canonicalize_rag_v2_immutable_retrieval_citations_pre_world_news_v157(text,text,text,jsonb)
  FROM PUBLIC,decision_app;

CREATE FUNCTION public.canonicalize_rag_v2_immutable_retrieval_citations(
  p_owner_user_id text,p_session_id text,p_scope_claim_id text,p_citations jsonb
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER VOLATILE SET search_path=pg_catalog AS $canonical$
DECLARE citation_item jsonb;
DECLARE normalized_item jsonb;
DECLARE canonical_one jsonb;
DECLARE candidate record;
DECLARE claim_row public.rag_v2_retrieval_scope_claims%ROWTYPE;
DECLARE expected_ordinal integer:=1;
DECLARE output jsonb:='[]'::jsonb;
DECLARE seen_chunks text[]:=ARRAY[]::text[];
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_app'
     OR nullif(current_setting('app.actor_user_id',true),'') IS DISTINCT FROM p_owner_user_id
     OR jsonb_typeof(p_citations)<>'array' OR jsonb_array_length(p_citations) NOT BETWEEN 1 AND 5
     OR octet_length(p_citations::text)>16384 THEN
    RAISE EXCEPTION 'RAG v2 world-news citation arguments invalid' USING ERRCODE='22023'; END IF;
  PERFORM set_config('app.actor_user_id',p_owner_user_id,true);
  PERFORM set_config('app.rag_v2_retrieval_scope','enabled',true);
  SELECT * INTO claim_row FROM public.rag_v2_retrieval_scope_claims scope
  WHERE scope.scope_claim_id=p_scope_claim_id AND scope.owner_user_id=p_owner_user_id
    AND scope.session_id=p_session_id AND scope.expires_at>statement_timestamp();
  IF NOT FOUND OR NOT ('DATA'=ANY(claim_row.allowed_topics)) AND EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_citations) item WHERE item->>'sourceRevisionId' LIKE 'srv_news_%'
  ) THEN RAISE EXCEPTION 'RAG v2 world-news scope unavailable' USING ERRCODE='55000'; END IF;

  FOR citation_item IN SELECT value FROM jsonb_array_elements(p_citations) LOOP
    IF jsonb_typeof(citation_item)<>'object'
       OR citation_item->>'ordinal'<>expected_ordinal::text
       OR citation_item->>'citationId'<>('cit_'||expected_ordinal::text)
       OR citation_item->>'chunkRevisionId'=ANY(seen_chunks) THEN
      RAISE EXCEPTION 'RAG v2 world-news citation receipt invalid' USING ERRCODE='22023'; END IF;
    seen_chunks:=array_append(seen_chunks,citation_item->>'chunkRevisionId');
    SELECT document.source_id,document.canonical_url,version.document_version_id,version.title,
      version.version_sha256,version.available_at,version.rag_retrieval_allowed
    INTO candidate
    FROM public.world_news_document_versions_v2 version
    JOIN public.world_news_documents_v2 document USING(document_id)
    WHERE citation_item->>'sourceRevisionId'='srv_news_'||left(version.version_sha256,32)
      AND citation_item->>'chunkRevisionId'='rag_v2_chk_'||left(version.version_sha256,32)
      AND citation_item->>'sourceId'=document.source_id
      AND citation_item->>'generationId'=claim_row.oa112_generation_id
      AND citation_item->>'citationKind'='PUBLIC_WEB'
      AND version.available_at<=statement_timestamp() AND version.rag_retrieval_allowed
      AND NOT EXISTS(
        SELECT 1 FROM public.world_news_document_versions_v2 newer
        WHERE newer.document_id=version.document_id AND newer.available_at<=statement_timestamp()
          AND (newer.available_at,newer.first_seen_at,newer.document_version_id)>
              (version.available_at,version.first_seen_at,version.document_version_id)
      );
    IF FOUND THEN
      output:=output||jsonb_build_array(jsonb_build_object(
        'citationKind','PUBLIC_WEB','citationId','cit_'||expected_ordinal::text,
        'sourceId',candidate.source_id,'sourceRevisionId',citation_item->>'sourceRevisionId',
        'chunkRevisionId',citation_item->>'chunkRevisionId','generationId',claim_row.oa112_generation_id,
        'title',COALESCE(candidate.title,'세계 뉴스'),'canonicalUrl',candidate.canonical_url,
        'locator',jsonb_build_object('section','world-news-v2')
      ));
    ELSE
      normalized_item:=citation_item||jsonb_build_object('ordinal',1,'citationId','cit_1');
      canonical_one:=public.canonicalize_rag_v2_immutable_retrieval_citations_pre_world_news_v157(
        p_owner_user_id,p_session_id,p_scope_claim_id,jsonb_build_array(normalized_item));
      IF jsonb_array_length(canonical_one)<>1 THEN
        RAISE EXCEPTION 'RAG v2 immutable citation recheck failed' USING ERRCODE='55000'; END IF;
      output:=output||jsonb_build_array((canonical_one->0)||jsonb_build_object(
        'citationId','cit_'||expected_ordinal::text));
    END IF;
    expected_ordinal:=expected_ordinal+1;
  END LOOP;
  RETURN output;
END $canonical$;
ALTER FUNCTION public.canonicalize_rag_v2_immutable_retrieval_citations(text,text,text,jsonb) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.canonicalize_rag_v2_immutable_retrieval_citations(text,text,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.canonicalize_rag_v2_immutable_retrieval_citations(text,text,text,jsonb) TO decision_app;

-- 외부 생성은 rights flag가 true인 world-news만 읽는다. Finnhub personal-local은 항상 여기서 닫힌다.
ALTER FUNCTION public.read_rag_v2_vertex_generation_evidence_legacy_v87(text,text,text,jsonb)
  RENAME TO read_rag_v2_vertex_generation_evidence_pre_world_news_v157;
REVOKE ALL ON FUNCTION public.read_rag_v2_vertex_generation_evidence_pre_world_news_v157(text,text,text,jsonb)
  FROM PUBLIC,decision_app;

CREATE FUNCTION public.read_rag_v2_vertex_generation_evidence_legacy_v87(
  p_owner_user_id text,p_session_id text,p_scope_claim_id text,p_citations jsonb
) RETURNS TABLE(ordinal integer,citation_id text,chunk_revision_id text,
  canonical_content text,canonical_content_sha256 text)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=public,pg_catalog,pg_temp
SET "app.required_actor_operation"='READ_VERTEX_EVIDENCE'
SET "app.required_actor_target_kind"='RAG_SCOPE'
AS $evidence$
DECLARE citation_item jsonb;
DECLARE normalized_item jsonb;
DECLARE item record;
DECLARE news record;
DECLARE claim_row public.rag_v2_retrieval_scope_claims%ROWTYPE;
DECLARE expected_ordinal integer:=1;
DECLARE total_bytes integer:=0;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_app'
     OR nullif(current_setting('app.actor_user_id',true),'') IS DISTINCT FROM p_owner_user_id
     OR jsonb_typeof(p_citations)<>'array' OR jsonb_array_length(p_citations) NOT BETWEEN 1 AND 5 THEN
    RAISE EXCEPTION 'RAG v2 world-news evidence arguments invalid' USING ERRCODE='22023'; END IF;
  SELECT * INTO claim_row FROM public.rag_v2_retrieval_scope_claims scope
  WHERE scope.scope_claim_id=p_scope_claim_id AND scope.owner_user_id=p_owner_user_id
    AND scope.session_id=p_session_id AND scope.expires_at>statement_timestamp();
  IF NOT FOUND THEN RAISE EXCEPTION 'RAG v2 world-news evidence scope unavailable' USING ERRCODE='55000'; END IF;
  FOR citation_item IN SELECT value FROM jsonb_array_elements(p_citations) LOOP
    SELECT version.canonical_content,version.content_sha256
    INTO news
    FROM public.world_news_document_versions_v2 version JOIN public.world_news_documents_v2 document USING(document_id)
    WHERE citation_item->>'sourceRevisionId'='srv_news_'||left(version.version_sha256,32)
      AND citation_item->>'chunkRevisionId'='rag_v2_chk_'||left(version.version_sha256,32)
      AND citation_item->>'sourceId'=document.source_id
      AND citation_item->>'generationId'=claim_row.oa112_generation_id
      AND citation_item->>'citationKind'='PUBLIC_WEB'
      AND version.available_at<=statement_timestamp() AND version.rag_retrieval_allowed
      AND version.external_llm_allowed;
    IF FOUND THEN
      ordinal:=expected_ordinal;citation_id:='cit_'||expected_ordinal::text;
      chunk_revision_id:=citation_item->>'chunkRevisionId';canonical_content:=news.canonical_content;
      canonical_content_sha256:=news.content_sha256;
    ELSE
      normalized_item:=citation_item||jsonb_build_object('ordinal',1,'citationId','cit_1');
      SELECT * INTO item FROM public.read_rag_v2_vertex_generation_evidence_pre_world_news_v157(
        p_owner_user_id,p_session_id,p_scope_claim_id,jsonb_build_array(normalized_item));
      IF NOT FOUND THEN RAISE EXCEPTION 'RAG v2 evidence is not externally eligible' USING ERRCODE='55000'; END IF;
      ordinal:=expected_ordinal;citation_id:='cit_'||expected_ordinal::text;
      chunk_revision_id:=item.chunk_revision_id;canonical_content:=item.canonical_content;
      canonical_content_sha256:=item.canonical_content_sha256;
    END IF;
    total_bytes:=total_bytes+octet_length(canonical_content);
    IF total_bytes>60000 THEN RAISE EXCEPTION 'RAG v2 evidence exceeds bounded input' USING ERRCODE='22023'; END IF;
    RETURN NEXT;expected_ordinal:=expected_ordinal+1;
  END LOOP;
END $evidence$;
ALTER FUNCTION public.read_rag_v2_vertex_generation_evidence_legacy_v87(text,text,text,jsonb) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.read_rag_v2_vertex_generation_evidence_legacy_v87(text,text,text,jsonb) FROM PUBLIC;
