from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class AsyncTransport(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ASYNC_TRANSPORT_UNSPECIFIED: _ClassVar[AsyncTransport]
    ASYNC_TRANSPORT_DB: _ClassVar[AsyncTransport]
    ASYNC_TRANSPORT_KAFKA: _ClassVar[AsyncTransport]

class AsyncWorkOutcome(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ASYNC_WORK_OUTCOME_UNSPECIFIED: _ClassVar[AsyncWorkOutcome]
    ASYNC_WORK_COMPLETED: _ClassVar[AsyncWorkOutcome]
    ASYNC_WORK_DUPLICATE: _ClassVar[AsyncWorkOutcome]
    ASYNC_WORK_FAILED: _ClassVar[AsyncWorkOutcome]
    ASYNC_WORK_NEEDS_REVIEW: _ClassVar[AsyncWorkOutcome]
ASYNC_TRANSPORT_UNSPECIFIED: AsyncTransport
ASYNC_TRANSPORT_DB: AsyncTransport
ASYNC_TRANSPORT_KAFKA: AsyncTransport
ASYNC_WORK_OUTCOME_UNSPECIFIED: AsyncWorkOutcome
ASYNC_WORK_COMPLETED: AsyncWorkOutcome
ASYNC_WORK_DUPLICATE: AsyncWorkOutcome
ASYNC_WORK_FAILED: AsyncWorkOutcome
ASYNC_WORK_NEEDS_REVIEW: AsyncWorkOutcome

class AsyncWorkRequest(_message.Message):
    __slots__ = ("event_id", "event_type", "schema_version", "payload_hash", "job_id", "job_type", "payload_json", "claim_token", "transport", "attempt")
    EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    EVENT_TYPE_FIELD_NUMBER: _ClassVar[int]
    SCHEMA_VERSION_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_HASH_FIELD_NUMBER: _ClassVar[int]
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    JOB_TYPE_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_JSON_FIELD_NUMBER: _ClassVar[int]
    CLAIM_TOKEN_FIELD_NUMBER: _ClassVar[int]
    TRANSPORT_FIELD_NUMBER: _ClassVar[int]
    ATTEMPT_FIELD_NUMBER: _ClassVar[int]
    event_id: str
    event_type: str
    schema_version: int
    payload_hash: str
    job_id: str
    job_type: str
    payload_json: bytes
    claim_token: str
    transport: AsyncTransport
    attempt: int
    def __init__(self, event_id: _Optional[str] = ..., event_type: _Optional[str] = ..., schema_version: _Optional[int] = ..., payload_hash: _Optional[str] = ..., job_id: _Optional[str] = ..., job_type: _Optional[str] = ..., payload_json: _Optional[bytes] = ..., claim_token: _Optional[str] = ..., transport: _Optional[_Union[AsyncTransport, str]] = ..., attempt: _Optional[int] = ...) -> None: ...

class AsyncWorkResponse(_message.Message):
    __slots__ = ("job_id", "outcome", "result_ref", "failure_code")
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    RESULT_REF_FIELD_NUMBER: _ClassVar[int]
    FAILURE_CODE_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    outcome: AsyncWorkOutcome
    result_ref: str
    failure_code: str
    def __init__(self, job_id: _Optional[str] = ..., outcome: _Optional[_Union[AsyncWorkOutcome, str]] = ..., result_ref: _Optional[str] = ..., failure_code: _Optional[str] = ...) -> None: ...
