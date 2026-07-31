from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class RagResponseStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    RAG_RESPONSE_STATUS_UNSPECIFIED: _ClassVar[RagResponseStatus]
    RAG_RESPONSE_STATUS_ANSWERED: _ClassVar[RagResponseStatus]
    RAG_RESPONSE_STATUS_RETRIEVAL_ONLY: _ClassVar[RagResponseStatus]
    RAG_RESPONSE_STATUS_RETRIEVAL_FAILURE: _ClassVar[RagResponseStatus]
    RAG_RESPONSE_STATUS_BLOCKED_SENSITIVE: _ClassVar[RagResponseStatus]
    RAG_RESPONSE_STATUS_BLOCKED_ADVICE: _ClassVar[RagResponseStatus]
    RAG_RESPONSE_STATUS_GENERATION_UNAVAILABLE: _ClassVar[RagResponseStatus]
RAG_RESPONSE_STATUS_UNSPECIFIED: RagResponseStatus
RAG_RESPONSE_STATUS_ANSWERED: RagResponseStatus
RAG_RESPONSE_STATUS_RETRIEVAL_ONLY: RagResponseStatus
RAG_RESPONSE_STATUS_RETRIEVAL_FAILURE: RagResponseStatus
RAG_RESPONSE_STATUS_BLOCKED_SENSITIVE: RagResponseStatus
RAG_RESPONSE_STATUS_BLOCKED_ADVICE: RagResponseStatus
RAG_RESPONSE_STATUS_GENERATION_UNAVAILABLE: RagResponseStatus

class RagAskRequest(_message.Message):
    __slots__ = ("request_id", "owner_scope_claim", "question", "answer_mode", "related_symbols", "topics", "consent_context", "policy_context")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    OWNER_SCOPE_CLAIM_FIELD_NUMBER: _ClassVar[int]
    QUESTION_FIELD_NUMBER: _ClassVar[int]
    ANSWER_MODE_FIELD_NUMBER: _ClassVar[int]
    RELATED_SYMBOLS_FIELD_NUMBER: _ClassVar[int]
    TOPICS_FIELD_NUMBER: _ClassVar[int]
    CONSENT_CONTEXT_FIELD_NUMBER: _ClassVar[int]
    POLICY_CONTEXT_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    owner_scope_claim: str
    question: str
    answer_mode: str
    related_symbols: _containers.RepeatedScalarFieldContainer[str]
    topics: _containers.RepeatedScalarFieldContainer[str]
    consent_context: RagConsentContext
    policy_context: RagPolicyContext
    def __init__(self, request_id: _Optional[str] = ..., owner_scope_claim: _Optional[str] = ..., question: _Optional[str] = ..., answer_mode: _Optional[str] = ..., related_symbols: _Optional[_Iterable[str]] = ..., topics: _Optional[_Iterable[str]] = ..., consent_context: _Optional[_Union[RagConsentContext, _Mapping]] = ..., policy_context: _Optional[_Union[RagPolicyContext, _Mapping]] = ...) -> None: ...

class RagConsentContext(_message.Message):
    __slots__ = ("granted", "policy_version")
    GRANTED_FIELD_NUMBER: _ClassVar[int]
    POLICY_VERSION_FIELD_NUMBER: _ClassVar[int]
    granted: bool
    policy_version: str
    def __init__(self, granted: _Optional[bool] = ..., policy_version: _Optional[str] = ...) -> None: ...

class RagPolicyContext(_message.Message):
    __slots__ = ("policy_id", "policy_version", "active_generation_id", "embedding_profile_id")
    POLICY_ID_FIELD_NUMBER: _ClassVar[int]
    POLICY_VERSION_FIELD_NUMBER: _ClassVar[int]
    ACTIVE_GENERATION_ID_FIELD_NUMBER: _ClassVar[int]
    EMBEDDING_PROFILE_ID_FIELD_NUMBER: _ClassVar[int]
    policy_id: str
    policy_version: int
    active_generation_id: str
    embedding_profile_id: str
    def __init__(self, policy_id: _Optional[str] = ..., policy_version: _Optional[int] = ..., active_generation_id: _Optional[str] = ..., embedding_profile_id: _Optional[str] = ...) -> None: ...

class RagAskResponse(_message.Message):
    __slots__ = ("request_id", "status", "answer", "citations", "citation_coverage", "retrieval_failure", "guardrail_flags", "generation_id", "embedding_profile_id", "failure_code", "provider_physical_counts", "authorized_top5_chunk_revision_ids", "external_provider_candidate", "policy_version")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    ANSWER_FIELD_NUMBER: _ClassVar[int]
    CITATIONS_FIELD_NUMBER: _ClassVar[int]
    CITATION_COVERAGE_FIELD_NUMBER: _ClassVar[int]
    RETRIEVAL_FAILURE_FIELD_NUMBER: _ClassVar[int]
    GUARDRAIL_FLAGS_FIELD_NUMBER: _ClassVar[int]
    GENERATION_ID_FIELD_NUMBER: _ClassVar[int]
    EMBEDDING_PROFILE_ID_FIELD_NUMBER: _ClassVar[int]
    FAILURE_CODE_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_PHYSICAL_COUNTS_FIELD_NUMBER: _ClassVar[int]
    AUTHORIZED_TOP5_CHUNK_REVISION_IDS_FIELD_NUMBER: _ClassVar[int]
    EXTERNAL_PROVIDER_CANDIDATE_FIELD_NUMBER: _ClassVar[int]
    POLICY_VERSION_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    status: RagResponseStatus
    answer: str
    citations: _containers.RepeatedCompositeFieldContainer[RagCitation]
    citation_coverage: float
    retrieval_failure: bool
    guardrail_flags: _containers.RepeatedScalarFieldContainer[str]
    generation_id: str
    embedding_profile_id: str
    failure_code: str
    provider_physical_counts: ProviderPhysicalCounts
    authorized_top5_chunk_revision_ids: _containers.RepeatedScalarFieldContainer[str]
    external_provider_candidate: bool
    policy_version: int
    def __init__(self, request_id: _Optional[str] = ..., status: _Optional[_Union[RagResponseStatus, str]] = ..., answer: _Optional[str] = ..., citations: _Optional[_Iterable[_Union[RagCitation, _Mapping]]] = ..., citation_coverage: _Optional[float] = ..., retrieval_failure: _Optional[bool] = ..., guardrail_flags: _Optional[_Iterable[str]] = ..., generation_id: _Optional[str] = ..., embedding_profile_id: _Optional[str] = ..., failure_code: _Optional[str] = ..., provider_physical_counts: _Optional[_Union[ProviderPhysicalCounts, _Mapping]] = ..., authorized_top5_chunk_revision_ids: _Optional[_Iterable[str]] = ..., external_provider_candidate: _Optional[bool] = ..., policy_version: _Optional[int] = ...) -> None: ...

class RagCitation(_message.Message):
    __slots__ = ("citation_id", "source_id", "source_revision_id", "chunk_revision_id", "generation_id", "title", "section_title", "canonical_url")
    CITATION_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_REVISION_ID_FIELD_NUMBER: _ClassVar[int]
    CHUNK_REVISION_ID_FIELD_NUMBER: _ClassVar[int]
    GENERATION_ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    SECTION_TITLE_FIELD_NUMBER: _ClassVar[int]
    CANONICAL_URL_FIELD_NUMBER: _ClassVar[int]
    citation_id: str
    source_id: str
    source_revision_id: str
    chunk_revision_id: str
    generation_id: str
    title: str
    section_title: str
    canonical_url: str
    def __init__(self, citation_id: _Optional[str] = ..., source_id: _Optional[str] = ..., source_revision_id: _Optional[str] = ..., chunk_revision_id: _Optional[str] = ..., generation_id: _Optional[str] = ..., title: _Optional[str] = ..., section_title: _Optional[str] = ..., canonical_url: _Optional[str] = ...) -> None: ...

class ProviderPhysicalCounts(_message.Message):
    __slots__ = ("total", "gemini", "openai", "voyage")
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    GEMINI_FIELD_NUMBER: _ClassVar[int]
    OPENAI_FIELD_NUMBER: _ClassVar[int]
    VOYAGE_FIELD_NUMBER: _ClassVar[int]
    total: int
    gemini: int
    openai: int
    voyage: int
    def __init__(self, total: _Optional[int] = ..., gemini: _Optional[int] = ..., openai: _Optional[int] = ..., voyage: _Optional[int] = ...) -> None: ...
