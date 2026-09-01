from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class EvidenceItem(_message.Message):
    __slots__ = ("ordinal", "citation_id", "chunk_revision_id", "canonical_text", "canonical_text_sha256", "owner_private")
    ORDINAL_FIELD_NUMBER: _ClassVar[int]
    CITATION_ID_FIELD_NUMBER: _ClassVar[int]
    CHUNK_REVISION_ID_FIELD_NUMBER: _ClassVar[int]
    CANONICAL_TEXT_FIELD_NUMBER: _ClassVar[int]
    CANONICAL_TEXT_SHA256_FIELD_NUMBER: _ClassVar[int]
    OWNER_PRIVATE_FIELD_NUMBER: _ClassVar[int]
    ordinal: int
    citation_id: str
    chunk_revision_id: str
    canonical_text: str
    canonical_text_sha256: str
    owner_private: bool
    def __init__(self, ordinal: _Optional[int] = ..., citation_id: _Optional[str] = ..., chunk_revision_id: _Optional[str] = ..., canonical_text: _Optional[str] = ..., canonical_text_sha256: _Optional[str] = ..., owner_private: _Optional[bool] = ...) -> None: ...

class JudgementCandidate(_message.Message):
    __slots__ = ("symbol", "expected_return", "lstm_signal", "baseline_signal")
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_RETURN_FIELD_NUMBER: _ClassVar[int]
    LSTM_SIGNAL_FIELD_NUMBER: _ClassVar[int]
    BASELINE_SIGNAL_FIELD_NUMBER: _ClassVar[int]
    symbol: str
    expected_return: float
    lstm_signal: str
    baseline_signal: str
    def __init__(self, symbol: _Optional[str] = ..., expected_return: _Optional[float] = ..., lstm_signal: _Optional[str] = ..., baseline_signal: _Optional[str] = ...) -> None: ...

class StartRun(_message.Message):
    __slots__ = ("model_id", "question", "answer_mode", "related_symbols", "topics", "public_evidence", "owner_evidence", "google_search_enabled", "max_tool_rounds", "current_time", "timezone", "language", "mode", "candidates", "thinking_level", "grounding_discovery_only")
    MODEL_ID_FIELD_NUMBER: _ClassVar[int]
    QUESTION_FIELD_NUMBER: _ClassVar[int]
    ANSWER_MODE_FIELD_NUMBER: _ClassVar[int]
    RELATED_SYMBOLS_FIELD_NUMBER: _ClassVar[int]
    TOPICS_FIELD_NUMBER: _ClassVar[int]
    PUBLIC_EVIDENCE_FIELD_NUMBER: _ClassVar[int]
    OWNER_EVIDENCE_FIELD_NUMBER: _ClassVar[int]
    GOOGLE_SEARCH_ENABLED_FIELD_NUMBER: _ClassVar[int]
    MAX_TOOL_ROUNDS_FIELD_NUMBER: _ClassVar[int]
    CURRENT_TIME_FIELD_NUMBER: _ClassVar[int]
    TIMEZONE_FIELD_NUMBER: _ClassVar[int]
    LANGUAGE_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    CANDIDATES_FIELD_NUMBER: _ClassVar[int]
    THINKING_LEVEL_FIELD_NUMBER: _ClassVar[int]
    GROUNDING_DISCOVERY_ONLY_FIELD_NUMBER: _ClassVar[int]
    model_id: str
    question: str
    answer_mode: str
    related_symbols: _containers.RepeatedScalarFieldContainer[str]
    topics: _containers.RepeatedScalarFieldContainer[str]
    public_evidence: _containers.RepeatedCompositeFieldContainer[EvidenceItem]
    owner_evidence: _containers.RepeatedCompositeFieldContainer[EvidenceItem]
    google_search_enabled: bool
    max_tool_rounds: int
    current_time: str
    timezone: str
    language: str
    mode: str
    candidates: _containers.RepeatedCompositeFieldContainer[JudgementCandidate]
    thinking_level: str
    grounding_discovery_only: bool
    def __init__(self, model_id: _Optional[str] = ..., question: _Optional[str] = ..., answer_mode: _Optional[str] = ..., related_symbols: _Optional[_Iterable[str]] = ..., topics: _Optional[_Iterable[str]] = ..., public_evidence: _Optional[_Iterable[_Union[EvidenceItem, _Mapping]]] = ..., owner_evidence: _Optional[_Iterable[_Union[EvidenceItem, _Mapping]]] = ..., google_search_enabled: _Optional[bool] = ..., max_tool_rounds: _Optional[int] = ..., current_time: _Optional[str] = ..., timezone: _Optional[str] = ..., language: _Optional[str] = ..., mode: _Optional[str] = ..., candidates: _Optional[_Iterable[_Union[JudgementCandidate, _Mapping]]] = ..., thinking_level: _Optional[str] = ..., grounding_discovery_only: _Optional[bool] = ...) -> None: ...

class ProviderCallPermit(_message.Message):
    __slots__ = ("planned_call_id",)
    PLANNED_CALL_ID_FIELD_NUMBER: _ClassVar[int]
    planned_call_id: str
    def __init__(self, planned_call_id: _Optional[str] = ...) -> None: ...

class ToolResult(_message.Message):
    __slots__ = ("tool_call_id", "tool_name", "result_json", "failed", "failure_leaf")
    TOOL_CALL_ID_FIELD_NUMBER: _ClassVar[int]
    TOOL_NAME_FIELD_NUMBER: _ClassVar[int]
    RESULT_JSON_FIELD_NUMBER: _ClassVar[int]
    FAILED_FIELD_NUMBER: _ClassVar[int]
    FAILURE_LEAF_FIELD_NUMBER: _ClassVar[int]
    tool_call_id: str
    tool_name: str
    result_json: str
    failed: bool
    failure_leaf: str
    def __init__(self, tool_call_id: _Optional[str] = ..., tool_name: _Optional[str] = ..., result_json: _Optional[str] = ..., failed: _Optional[bool] = ..., failure_leaf: _Optional[str] = ...) -> None: ...

class CancelRun(_message.Message):
    __slots__ = ("reason",)
    REASON_FIELD_NUMBER: _ClassVar[int]
    reason: str
    def __init__(self, reason: _Optional[str] = ...) -> None: ...

class HostEvent(_message.Message):
    __slots__ = ("run_id", "sequence", "call_id", "start_run", "provider_call_permit", "tool_result", "cancel_run")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    CALL_ID_FIELD_NUMBER: _ClassVar[int]
    START_RUN_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_CALL_PERMIT_FIELD_NUMBER: _ClassVar[int]
    TOOL_RESULT_FIELD_NUMBER: _ClassVar[int]
    CANCEL_RUN_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    sequence: int
    call_id: str
    start_run: StartRun
    provider_call_permit: ProviderCallPermit
    tool_result: ToolResult
    cancel_run: CancelRun
    def __init__(self, run_id: _Optional[str] = ..., sequence: _Optional[int] = ..., call_id: _Optional[str] = ..., start_run: _Optional[_Union[StartRun, _Mapping]] = ..., provider_call_permit: _Optional[_Union[ProviderCallPermit, _Mapping]] = ..., tool_result: _Optional[_Union[ToolResult, _Mapping]] = ..., cancel_run: _Optional[_Union[CancelRun, _Mapping]] = ...) -> None: ...

class ProviderCallPlanned(_message.Message):
    __slots__ = ("planned_call_id", "phase", "google_search_attached")
    PLANNED_CALL_ID_FIELD_NUMBER: _ClassVar[int]
    PHASE_FIELD_NUMBER: _ClassVar[int]
    GOOGLE_SEARCH_ATTACHED_FIELD_NUMBER: _ClassVar[int]
    planned_call_id: str
    phase: str
    google_search_attached: bool
    def __init__(self, planned_call_id: _Optional[str] = ..., phase: _Optional[str] = ..., google_search_attached: _Optional[bool] = ...) -> None: ...

class GroundingRoot(_message.Message):
    __slots__ = ("result_id", "title", "uri", "domain", "chunk_index", "citation_id")
    RESULT_ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    URI_FIELD_NUMBER: _ClassVar[int]
    DOMAIN_FIELD_NUMBER: _ClassVar[int]
    CHUNK_INDEX_FIELD_NUMBER: _ClassVar[int]
    CITATION_ID_FIELD_NUMBER: _ClassVar[int]
    result_id: str
    title: str
    uri: str
    domain: str
    chunk_index: int
    citation_id: str
    def __init__(self, result_id: _Optional[str] = ..., title: _Optional[str] = ..., uri: _Optional[str] = ..., domain: _Optional[str] = ..., chunk_index: _Optional[int] = ..., citation_id: _Optional[str] = ...) -> None: ...

class GroundingSupport(_message.Message):
    __slots__ = ("start_index", "end_index", "text", "chunk_indices")
    START_INDEX_FIELD_NUMBER: _ClassVar[int]
    END_INDEX_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    CHUNK_INDICES_FIELD_NUMBER: _ClassVar[int]
    start_index: int
    end_index: int
    text: str
    chunk_indices: _containers.RepeatedScalarFieldContainer[int]
    def __init__(self, start_index: _Optional[int] = ..., end_index: _Optional[int] = ..., text: _Optional[str] = ..., chunk_indices: _Optional[_Iterable[int]] = ...) -> None: ...

class RegisterGroundingRoots(_message.Message):
    __slots__ = ("web_search_queries", "roots", "supports")
    WEB_SEARCH_QUERIES_FIELD_NUMBER: _ClassVar[int]
    ROOTS_FIELD_NUMBER: _ClassVar[int]
    SUPPORTS_FIELD_NUMBER: _ClassVar[int]
    web_search_queries: _containers.RepeatedScalarFieldContainer[str]
    roots: _containers.RepeatedCompositeFieldContainer[GroundingRoot]
    supports: _containers.RepeatedCompositeFieldContainer[GroundingSupport]
    def __init__(self, web_search_queries: _Optional[_Iterable[str]] = ..., roots: _Optional[_Iterable[_Union[GroundingRoot, _Mapping]]] = ..., supports: _Optional[_Iterable[_Union[GroundingSupport, _Mapping]]] = ...) -> None: ...

class WebSearch(_message.Message):
    __slots__ = ("tool_call_id", "query")
    TOOL_CALL_ID_FIELD_NUMBER: _ClassVar[int]
    QUERY_FIELD_NUMBER: _ClassVar[int]
    tool_call_id: str
    query: str
    def __init__(self, tool_call_id: _Optional[str] = ..., query: _Optional[str] = ...) -> None: ...

class WebRead(_message.Message):
    __slots__ = ("tool_call_id", "result_id")
    TOOL_CALL_ID_FIELD_NUMBER: _ClassVar[int]
    RESULT_ID_FIELD_NUMBER: _ClassVar[int]
    tool_call_id: str
    result_id: str
    def __init__(self, tool_call_id: _Optional[str] = ..., result_id: _Optional[str] = ...) -> None: ...

class Completed(_message.Message):
    __slots__ = ("answer_json", "prompt_token_count", "output_token_count", "vertex_generate_call_count", "google_grounding_query_count", "search_backend", "evidence_validation_mode", "grounding_roots", "grounding_supports", "web_search_queries", "provider_id")
    ANSWER_JSON_FIELD_NUMBER: _ClassVar[int]
    PROMPT_TOKEN_COUNT_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_TOKEN_COUNT_FIELD_NUMBER: _ClassVar[int]
    VERTEX_GENERATE_CALL_COUNT_FIELD_NUMBER: _ClassVar[int]
    GOOGLE_GROUNDING_QUERY_COUNT_FIELD_NUMBER: _ClassVar[int]
    SEARCH_BACKEND_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_VALIDATION_MODE_FIELD_NUMBER: _ClassVar[int]
    GROUNDING_ROOTS_FIELD_NUMBER: _ClassVar[int]
    GROUNDING_SUPPORTS_FIELD_NUMBER: _ClassVar[int]
    WEB_SEARCH_QUERIES_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_ID_FIELD_NUMBER: _ClassVar[int]
    answer_json: str
    prompt_token_count: int
    output_token_count: int
    vertex_generate_call_count: int
    google_grounding_query_count: int
    search_backend: str
    evidence_validation_mode: str
    grounding_roots: _containers.RepeatedCompositeFieldContainer[GroundingRoot]
    grounding_supports: _containers.RepeatedCompositeFieldContainer[GroundingSupport]
    web_search_queries: _containers.RepeatedScalarFieldContainer[str]
    provider_id: str
    def __init__(self, answer_json: _Optional[str] = ..., prompt_token_count: _Optional[int] = ..., output_token_count: _Optional[int] = ..., vertex_generate_call_count: _Optional[int] = ..., google_grounding_query_count: _Optional[int] = ..., search_backend: _Optional[str] = ..., evidence_validation_mode: _Optional[str] = ..., grounding_roots: _Optional[_Iterable[_Union[GroundingRoot, _Mapping]]] = ..., grounding_supports: _Optional[_Iterable[_Union[GroundingSupport, _Mapping]]] = ..., web_search_queries: _Optional[_Iterable[str]] = ..., provider_id: _Optional[str] = ...) -> None: ...

class Failed(_message.Message):
    __slots__ = ("failure_leaf", "provider_attempted", "vertex_generate_call_count", "google_grounding_query_count")
    FAILURE_LEAF_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_ATTEMPTED_FIELD_NUMBER: _ClassVar[int]
    VERTEX_GENERATE_CALL_COUNT_FIELD_NUMBER: _ClassVar[int]
    GOOGLE_GROUNDING_QUERY_COUNT_FIELD_NUMBER: _ClassVar[int]
    failure_leaf: str
    provider_attempted: bool
    vertex_generate_call_count: int
    google_grounding_query_count: int
    def __init__(self, failure_leaf: _Optional[str] = ..., provider_attempted: _Optional[bool] = ..., vertex_generate_call_count: _Optional[int] = ..., google_grounding_query_count: _Optional[int] = ...) -> None: ...

class AgentEvent(_message.Message):
    __slots__ = ("run_id", "sequence", "call_id", "provider_call_planned", "register_grounding_roots", "web_search", "web_read", "completed", "failed")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    CALL_ID_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_CALL_PLANNED_FIELD_NUMBER: _ClassVar[int]
    REGISTER_GROUNDING_ROOTS_FIELD_NUMBER: _ClassVar[int]
    WEB_SEARCH_FIELD_NUMBER: _ClassVar[int]
    WEB_READ_FIELD_NUMBER: _ClassVar[int]
    COMPLETED_FIELD_NUMBER: _ClassVar[int]
    FAILED_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    sequence: int
    call_id: str
    provider_call_planned: ProviderCallPlanned
    register_grounding_roots: RegisterGroundingRoots
    web_search: WebSearch
    web_read: WebRead
    completed: Completed
    failed: Failed
    def __init__(self, run_id: _Optional[str] = ..., sequence: _Optional[int] = ..., call_id: _Optional[str] = ..., provider_call_planned: _Optional[_Union[ProviderCallPlanned, _Mapping]] = ..., register_grounding_roots: _Optional[_Union[RegisterGroundingRoots, _Mapping]] = ..., web_search: _Optional[_Union[WebSearch, _Mapping]] = ..., web_read: _Optional[_Union[WebRead, _Mapping]] = ..., completed: _Optional[_Union[Completed, _Mapping]] = ..., failed: _Optional[_Union[Failed, _Mapping]] = ...) -> None: ...
