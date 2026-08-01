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
    RAG_RESPONSE_STATUS_CORPUS_NOT_READY: _ClassVar[RagResponseStatus]
RAG_RESPONSE_STATUS_UNSPECIFIED: RagResponseStatus
RAG_RESPONSE_STATUS_ANSWERED: RagResponseStatus
RAG_RESPONSE_STATUS_RETRIEVAL_ONLY: RagResponseStatus
RAG_RESPONSE_STATUS_RETRIEVAL_FAILURE: RagResponseStatus
RAG_RESPONSE_STATUS_BLOCKED_SENSITIVE: RagResponseStatus
RAG_RESPONSE_STATUS_BLOCKED_ADVICE: RagResponseStatus
RAG_RESPONSE_STATUS_GENERATION_UNAVAILABLE: RagResponseStatus
RAG_RESPONSE_STATUS_CORPUS_NOT_READY: RagResponseStatus

class RagAskRequest(_message.Message):
    __slots__ = ("request_id", "owner_scope_claim", "question", "answer_mode", "related_symbols", "topics", "consent_context")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    OWNER_SCOPE_CLAIM_FIELD_NUMBER: _ClassVar[int]
    QUESTION_FIELD_NUMBER: _ClassVar[int]
    ANSWER_MODE_FIELD_NUMBER: _ClassVar[int]
    RELATED_SYMBOLS_FIELD_NUMBER: _ClassVar[int]
    TOPICS_FIELD_NUMBER: _ClassVar[int]
    CONSENT_CONTEXT_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    owner_scope_claim: str
    question: str
    answer_mode: str
    related_symbols: _containers.RepeatedScalarFieldContainer[str]
    topics: _containers.RepeatedScalarFieldContainer[str]
    consent_context: RagConsentContext
    def __init__(self, request_id: _Optional[str] = ..., owner_scope_claim: _Optional[str] = ..., question: _Optional[str] = ..., answer_mode: _Optional[str] = ..., related_symbols: _Optional[_Iterable[str]] = ..., topics: _Optional[_Iterable[str]] = ..., consent_context: _Optional[_Union[RagConsentContext, _Mapping]] = ...) -> None: ...

class RagConsentContext(_message.Message):
    __slots__ = ("granted", "policy_version")
    GRANTED_FIELD_NUMBER: _ClassVar[int]
    POLICY_VERSION_FIELD_NUMBER: _ClassVar[int]
    granted: bool
    policy_version: str
    def __init__(self, granted: _Optional[bool] = ..., policy_version: _Optional[str] = ...) -> None: ...

class RagAskResponse(_message.Message):
    __slots__ = ("request_id", "status", "answer", "citations", "citation_coverage", "retrieval_failure", "guardrail_flags", "exact30_generation_id", "oa_generation_id", "owner_generation_id", "embedding_profile_id", "failure_code", "provider_physical_counts", "authorized_top5_chunk_revision_ids", "external_provider_candidate", "policy_version")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    ANSWER_FIELD_NUMBER: _ClassVar[int]
    CITATIONS_FIELD_NUMBER: _ClassVar[int]
    CITATION_COVERAGE_FIELD_NUMBER: _ClassVar[int]
    RETRIEVAL_FAILURE_FIELD_NUMBER: _ClassVar[int]
    GUARDRAIL_FLAGS_FIELD_NUMBER: _ClassVar[int]
    EXACT30_GENERATION_ID_FIELD_NUMBER: _ClassVar[int]
    OA_GENERATION_ID_FIELD_NUMBER: _ClassVar[int]
    OWNER_GENERATION_ID_FIELD_NUMBER: _ClassVar[int]
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
    exact30_generation_id: str
    oa_generation_id: str
    owner_generation_id: str
    embedding_profile_id: str
    failure_code: str
    provider_physical_counts: ProviderPhysicalCounts
    authorized_top5_chunk_revision_ids: _containers.RepeatedScalarFieldContainer[str]
    external_provider_candidate: bool
    policy_version: int
    def __init__(self, request_id: _Optional[str] = ..., status: _Optional[_Union[RagResponseStatus, str]] = ..., answer: _Optional[str] = ..., citations: _Optional[_Iterable[_Union[RagCitation, _Mapping]]] = ..., citation_coverage: _Optional[float] = ..., retrieval_failure: _Optional[bool] = ..., guardrail_flags: _Optional[_Iterable[str]] = ..., exact30_generation_id: _Optional[str] = ..., oa_generation_id: _Optional[str] = ..., owner_generation_id: _Optional[str] = ..., embedding_profile_id: _Optional[str] = ..., failure_code: _Optional[str] = ..., provider_physical_counts: _Optional[_Union[ProviderPhysicalCounts, _Mapping]] = ..., authorized_top5_chunk_revision_ids: _Optional[_Iterable[str]] = ..., external_provider_candidate: _Optional[bool] = ..., policy_version: _Optional[int] = ...) -> None: ...

class RagCitation(_message.Message):
    __slots__ = ("citation_id", "source_id", "source_revision_id", "chunk_revision_id", "generation_id", "public_web", "local_document")
    CITATION_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_REVISION_ID_FIELD_NUMBER: _ClassVar[int]
    CHUNK_REVISION_ID_FIELD_NUMBER: _ClassVar[int]
    GENERATION_ID_FIELD_NUMBER: _ClassVar[int]
    PUBLIC_WEB_FIELD_NUMBER: _ClassVar[int]
    LOCAL_DOCUMENT_FIELD_NUMBER: _ClassVar[int]
    citation_id: str
    source_id: str
    source_revision_id: str
    chunk_revision_id: str
    generation_id: str
    public_web: PublicWebCitation
    local_document: LocalDocumentCitation
    def __init__(self, citation_id: _Optional[str] = ..., source_id: _Optional[str] = ..., source_revision_id: _Optional[str] = ..., chunk_revision_id: _Optional[str] = ..., generation_id: _Optional[str] = ..., public_web: _Optional[_Union[PublicWebCitation, _Mapping]] = ..., local_document: _Optional[_Union[LocalDocumentCitation, _Mapping]] = ...) -> None: ...

class PublicWebCitation(_message.Message):
    __slots__ = ("title", "canonical_url", "locator")
    TITLE_FIELD_NUMBER: _ClassVar[int]
    CANONICAL_URL_FIELD_NUMBER: _ClassVar[int]
    LOCATOR_FIELD_NUMBER: _ClassVar[int]
    title: str
    canonical_url: str
    locator: DocumentLocator
    def __init__(self, title: _Optional[str] = ..., canonical_url: _Optional[str] = ..., locator: _Optional[_Union[DocumentLocator, _Mapping]] = ...) -> None: ...

class LocalDocumentCitation(_message.Message):
    __slots__ = ("document_id", "display_name", "locator")
    DOCUMENT_ID_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    LOCATOR_FIELD_NUMBER: _ClassVar[int]
    document_id: str
    display_name: str
    locator: DocumentLocator
    def __init__(self, document_id: _Optional[str] = ..., display_name: _Optional[str] = ..., locator: _Optional[_Union[DocumentLocator, _Mapping]] = ...) -> None: ...

class DocumentLocator(_message.Message):
    __slots__ = ("page", "slide", "sheet", "section")
    PAGE_FIELD_NUMBER: _ClassVar[int]
    SLIDE_FIELD_NUMBER: _ClassVar[int]
    SHEET_FIELD_NUMBER: _ClassVar[int]
    SECTION_FIELD_NUMBER: _ClassVar[int]
    page: int
    slide: int
    sheet: str
    section: str
    def __init__(self, page: _Optional[int] = ..., slide: _Optional[int] = ..., sheet: _Optional[str] = ..., section: _Optional[str] = ...) -> None: ...

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
