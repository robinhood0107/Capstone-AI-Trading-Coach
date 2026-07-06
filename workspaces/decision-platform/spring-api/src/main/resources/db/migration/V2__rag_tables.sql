CREATE EXTENSION IF NOT EXISTS vector;
--pgvector 타입은 bge-m3 1024차원 embedding을 PostgreSQL 안에서 원칙/주문/RAG 메타데이터와 함께 조인하기 위해 필요하다.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
--한국어 키워드 fallback 검색은 trigram GIN 인덱스로 고정해 벡터 검색 실패 시에도 출처 후보를 찾을 수 있게 한다.

CREATE TABLE rag_sources (
  source_id text PRIMARY KEY,
  title text NOT NULL,
  source_type text NOT NULL,
  tier text NOT NULL,
  url text,
  doi text,
  access_level text NOT NULL DEFAULT 'PUBLIC',
  license_note text,
  ingest_status text NOT NULL DEFAULT 'REGISTERED',
  content_hash text,
  last_checked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE rag_chunks (
  chunk_id text PRIMARY KEY,
  source_id text NOT NULL REFERENCES rag_sources(source_id) ON DELETE CASCADE,
  seq integer NOT NULL CHECK (seq >= 0),
  content text NOT NULL,
  section_title text,
  topic text[] NOT NULL DEFAULT '{}',
  tier text NOT NULL,
  access_level text NOT NULL,
  token_count integer,
  content_hash text NOT NULL,
  embedding_model text NOT NULL,
  embedding vector(1024),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT rag_chunks_source_content_hash_embedding_model_unique UNIQUE (source_id, content_hash, embedding_model)
);
CREATE INDEX idx_chunks_trgm ON rag_chunks USING gin (content gin_trgm_ops);
CREATE INDEX idx_chunks_source ON rag_chunks(source_id, seq);
--ivfflat은 빈 테이블에서 만들면 품질과 성능 판단이 왜곡되므로 실제 embedding 적재 후 V5 후보로 남긴다.

CREATE TABLE rag_answers (
  answer_id text PRIMARY KEY,
  user_id text NOT NULL REFERENCES users(user_id),
  question text NOT NULL,
  question_hash text NOT NULL,
  answer text,
  retrieval_failure boolean NOT NULL DEFAULT false,
  citation_coverage numeric(4,3) CHECK (citation_coverage IS NULL OR citation_coverage BETWEEN 0 AND 1),
  direct_advice_blocked boolean NOT NULL DEFAULT false,
  llm_model text,
  prompt_version text,
  input_tokens integer,
  output_tokens integer,
  latency_ms integer,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_answers_user ON rag_answers(user_id, created_at DESC);

CREATE TABLE rag_citations (
  answer_id text NOT NULL REFERENCES rag_answers(answer_id) ON DELETE CASCADE,
  cit_no integer NOT NULL CHECK (cit_no > 0),
  chunk_id text NOT NULL REFERENCES rag_chunks(chunk_id),
  used_in_answer boolean NOT NULL,
  retrieval_score numeric(10,8),
  PRIMARY KEY (answer_id, cit_no)
);

CREATE TABLE rag_answer_feedback (
  answer_id text NOT NULL REFERENCES rag_answers(answer_id) ON DELETE CASCADE,
  user_id text NOT NULL REFERENCES users(user_id),
  helpful boolean,
  citation_helpful boolean,
  comment text,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (answer_id, user_id)
);
