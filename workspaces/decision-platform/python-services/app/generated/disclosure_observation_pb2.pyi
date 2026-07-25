from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class GetDisclosureEventsRequest(_message.Message):
    __slots__ = ("symbol", "corp_code", "as_of", "window_from", "window_to")
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    CORP_CODE_FIELD_NUMBER: _ClassVar[int]
    AS_OF_FIELD_NUMBER: _ClassVar[int]
    WINDOW_FROM_FIELD_NUMBER: _ClassVar[int]
    WINDOW_TO_FIELD_NUMBER: _ClassVar[int]
    symbol: str
    corp_code: str
    as_of: str
    window_from: str
    window_to: str
    def __init__(self, symbol: _Optional[str] = ..., corp_code: _Optional[str] = ..., as_of: _Optional[str] = ..., window_from: _Optional[str] = ..., window_to: _Optional[str] = ...) -> None: ...

class GetDisclosureEventsResponse(_message.Message):
    __slots__ = ("symbol", "corp_code", "as_of", "window_from", "window_to", "score", "mapping_version", "events", "warnings", "source_refs", "observed_at", "complete")
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    CORP_CODE_FIELD_NUMBER: _ClassVar[int]
    AS_OF_FIELD_NUMBER: _ClassVar[int]
    WINDOW_FROM_FIELD_NUMBER: _ClassVar[int]
    WINDOW_TO_FIELD_NUMBER: _ClassVar[int]
    SCORE_FIELD_NUMBER: _ClassVar[int]
    MAPPING_VERSION_FIELD_NUMBER: _ClassVar[int]
    EVENTS_FIELD_NUMBER: _ClassVar[int]
    WARNINGS_FIELD_NUMBER: _ClassVar[int]
    SOURCE_REFS_FIELD_NUMBER: _ClassVar[int]
    OBSERVED_AT_FIELD_NUMBER: _ClassVar[int]
    COMPLETE_FIELD_NUMBER: _ClassVar[int]
    symbol: str
    corp_code: str
    as_of: str
    window_from: str
    window_to: str
    score: float
    mapping_version: str
    events: _containers.RepeatedCompositeFieldContainer[DisclosureRiskEvent]
    warnings: _containers.RepeatedCompositeFieldContainer[DisclosureRiskWarning]
    source_refs: _containers.RepeatedScalarFieldContainer[str]
    observed_at: str
    complete: bool
    def __init__(self, symbol: _Optional[str] = ..., corp_code: _Optional[str] = ..., as_of: _Optional[str] = ..., window_from: _Optional[str] = ..., window_to: _Optional[str] = ..., score: _Optional[float] = ..., mapping_version: _Optional[str] = ..., events: _Optional[_Iterable[_Union[DisclosureRiskEvent, _Mapping]]] = ..., warnings: _Optional[_Iterable[_Union[DisclosureRiskWarning, _Mapping]]] = ..., source_refs: _Optional[_Iterable[str]] = ..., observed_at: _Optional[str] = ..., complete: _Optional[bool] = ...) -> None: ...

class DisclosureRiskEvent(_message.Message):
    __slots__ = ("event_code", "receipt_no", "occurred_on")
    EVENT_CODE_FIELD_NUMBER: _ClassVar[int]
    RECEIPT_NO_FIELD_NUMBER: _ClassVar[int]
    OCCURRED_ON_FIELD_NUMBER: _ClassVar[int]
    event_code: str
    receipt_no: str
    occurred_on: str
    def __init__(self, event_code: _Optional[str] = ..., receipt_no: _Optional[str] = ..., occurred_on: _Optional[str] = ...) -> None: ...

class DisclosureRiskWarning(_message.Message):
    __slots__ = ("code", "event_code", "receipt_no", "message")
    CODE_FIELD_NUMBER: _ClassVar[int]
    EVENT_CODE_FIELD_NUMBER: _ClassVar[int]
    RECEIPT_NO_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    code: str
    event_code: str
    receipt_no: str
    message: str
    def __init__(self, code: _Optional[str] = ..., event_code: _Optional[str] = ..., receipt_no: _Optional[str] = ..., message: _Optional[str] = ...) -> None: ...
