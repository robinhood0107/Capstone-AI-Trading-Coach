from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class BoundMockCredentialEnvelope(_message.Message):
    __slots__ = ("owner_user_id", "account_id", "revision", "credential_state", "kek_version", "wrap_nonce", "wrapped_dek", "wrap_tag", "payload_nonce", "payload_ciphertext", "payload_tag")
    OWNER_USER_ID_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_STATE_FIELD_NUMBER: _ClassVar[int]
    KEK_VERSION_FIELD_NUMBER: _ClassVar[int]
    WRAP_NONCE_FIELD_NUMBER: _ClassVar[int]
    WRAPPED_DEK_FIELD_NUMBER: _ClassVar[int]
    WRAP_TAG_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_NONCE_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_CIPHERTEXT_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_TAG_FIELD_NUMBER: _ClassVar[int]
    owner_user_id: str
    account_id: str
    revision: int
    credential_state: str
    kek_version: str
    wrap_nonce: bytes
    wrapped_dek: bytes
    wrap_tag: bytes
    payload_nonce: bytes
    payload_ciphertext: bytes
    payload_tag: bytes
    def __init__(self, owner_user_id: _Optional[str] = ..., account_id: _Optional[str] = ..., revision: _Optional[int] = ..., credential_state: _Optional[str] = ..., kek_version: _Optional[str] = ..., wrap_nonce: _Optional[bytes] = ..., wrapped_dek: _Optional[bytes] = ..., wrap_tag: _Optional[bytes] = ..., payload_nonce: _Optional[bytes] = ..., payload_ciphertext: _Optional[bytes] = ..., payload_tag: _Optional[bytes] = ...) -> None: ...

class SubmitMockCashOrderRequest(_message.Message):
    __slots__ = ("request_id", "order_id", "account_id", "symbol", "side", "order_type", "quantity", "estimated_price_krw", "credential")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    SIDE_FIELD_NUMBER: _ClassVar[int]
    ORDER_TYPE_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    ESTIMATED_PRICE_KRW_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    order_id: str
    account_id: str
    symbol: str
    side: str
    order_type: str
    quantity: int
    estimated_price_krw: int
    credential: BoundMockCredentialEnvelope
    def __init__(self, request_id: _Optional[str] = ..., order_id: _Optional[str] = ..., account_id: _Optional[str] = ..., symbol: _Optional[str] = ..., side: _Optional[str] = ..., order_type: _Optional[str] = ..., quantity: _Optional[int] = ..., estimated_price_krw: _Optional[int] = ..., credential: _Optional[_Union[BoundMockCredentialEnvelope, _Mapping]] = ...) -> None: ...

class SubmitMockCashOrderResponse(_message.Message):
    __slots__ = ("order_id", "accepted", "provider_order_ref_hash", "tr_id", "received_at")
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    ACCEPTED_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_ORDER_REF_HASH_FIELD_NUMBER: _ClassVar[int]
    TR_ID_FIELD_NUMBER: _ClassVar[int]
    RECEIVED_AT_FIELD_NUMBER: _ClassVar[int]
    order_id: str
    accepted: bool
    provider_order_ref_hash: str
    tr_id: str
    received_at: str
    def __init__(self, order_id: _Optional[str] = ..., accepted: _Optional[bool] = ..., provider_order_ref_hash: _Optional[str] = ..., tr_id: _Optional[str] = ..., received_at: _Optional[str] = ...) -> None: ...

class CancelMockCashOrderRequest(_message.Message):
    __slots__ = ("request_id", "order_id", "account_id", "credential")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    order_id: str
    account_id: str
    credential: BoundMockCredentialEnvelope
    def __init__(self, request_id: _Optional[str] = ..., order_id: _Optional[str] = ..., account_id: _Optional[str] = ..., credential: _Optional[_Union[BoundMockCredentialEnvelope, _Mapping]] = ...) -> None: ...

class CancelMockCashOrderResponse(_message.Message):
    __slots__ = ("order_id", "status", "received_at")
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    RECEIVED_AT_FIELD_NUMBER: _ClassVar[int]
    order_id: str
    status: str
    received_at: str
    def __init__(self, order_id: _Optional[str] = ..., status: _Optional[str] = ..., received_at: _Optional[str] = ...) -> None: ...

class GetMockBalanceRequest(_message.Message):
    __slots__ = ("request_id", "account_id", "credential")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    account_id: str
    credential: BoundMockCredentialEnvelope
    def __init__(self, request_id: _Optional[str] = ..., account_id: _Optional[str] = ..., credential: _Optional[_Union[BoundMockCredentialEnvelope, _Mapping]] = ...) -> None: ...

class MockBalancePosition(_message.Message):
    __slots__ = ("symbol", "quantity", "market_value_krw", "is_gold_etf_etn")
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    MARKET_VALUE_KRW_FIELD_NUMBER: _ClassVar[int]
    IS_GOLD_ETF_ETN_FIELD_NUMBER: _ClassVar[int]
    symbol: str
    quantity: int
    market_value_krw: int
    is_gold_etf_etn: bool
    def __init__(self, symbol: _Optional[str] = ..., quantity: _Optional[int] = ..., market_value_krw: _Optional[int] = ..., is_gold_etf_etn: _Optional[bool] = ...) -> None: ...

class GetMockBalanceResponse(_message.Message):
    __slots__ = ("account_id", "cash_krw", "portfolio_equity_krw", "margin_requirement_krw", "positions", "observed_at", "source_version")
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    CASH_KRW_FIELD_NUMBER: _ClassVar[int]
    PORTFOLIO_EQUITY_KRW_FIELD_NUMBER: _ClassVar[int]
    MARGIN_REQUIREMENT_KRW_FIELD_NUMBER: _ClassVar[int]
    POSITIONS_FIELD_NUMBER: _ClassVar[int]
    OBSERVED_AT_FIELD_NUMBER: _ClassVar[int]
    SOURCE_VERSION_FIELD_NUMBER: _ClassVar[int]
    account_id: str
    cash_krw: int
    portfolio_equity_krw: int
    margin_requirement_krw: int
    positions: _containers.RepeatedCompositeFieldContainer[MockBalancePosition]
    observed_at: str
    source_version: str
    def __init__(self, account_id: _Optional[str] = ..., cash_krw: _Optional[int] = ..., portfolio_equity_krw: _Optional[int] = ..., margin_requirement_krw: _Optional[int] = ..., positions: _Optional[_Iterable[_Union[MockBalancePosition, _Mapping]]] = ..., observed_at: _Optional[str] = ..., source_version: _Optional[str] = ...) -> None: ...

class GetMockBuyableRequest(_message.Message):
    __slots__ = ("request_id", "account_id", "symbol", "estimated_price_krw", "credential")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    ESTIMATED_PRICE_KRW_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    account_id: str
    symbol: str
    estimated_price_krw: int
    credential: BoundMockCredentialEnvelope
    def __init__(self, request_id: _Optional[str] = ..., account_id: _Optional[str] = ..., symbol: _Optional[str] = ..., estimated_price_krw: _Optional[int] = ..., credential: _Optional[_Union[BoundMockCredentialEnvelope, _Mapping]] = ...) -> None: ...

class GetMockBuyableResponse(_message.Message):
    __slots__ = ("account_id", "symbol", "estimated_price_krw", "buyable_quantity", "buyable_amount_krw", "cash_krw", "observed_at", "source_version")
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    ESTIMATED_PRICE_KRW_FIELD_NUMBER: _ClassVar[int]
    BUYABLE_QUANTITY_FIELD_NUMBER: _ClassVar[int]
    BUYABLE_AMOUNT_KRW_FIELD_NUMBER: _ClassVar[int]
    CASH_KRW_FIELD_NUMBER: _ClassVar[int]
    OBSERVED_AT_FIELD_NUMBER: _ClassVar[int]
    SOURCE_VERSION_FIELD_NUMBER: _ClassVar[int]
    account_id: str
    symbol: str
    estimated_price_krw: int
    buyable_quantity: int
    buyable_amount_krw: int
    cash_krw: int
    observed_at: str
    source_version: str
    def __init__(self, account_id: _Optional[str] = ..., symbol: _Optional[str] = ..., estimated_price_krw: _Optional[int] = ..., buyable_quantity: _Optional[int] = ..., buyable_amount_krw: _Optional[int] = ..., cash_krw: _Optional[int] = ..., observed_at: _Optional[str] = ..., source_version: _Optional[str] = ...) -> None: ...

class VerifyMockConnectionRequest(_message.Message):
    __slots__ = ("request_id", "account_id", "credential")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    account_id: str
    credential: BoundMockCredentialEnvelope
    def __init__(self, request_id: _Optional[str] = ..., account_id: _Optional[str] = ..., credential: _Optional[_Union[BoundMockCredentialEnvelope, _Mapping]] = ...) -> None: ...

class VerifyMockConnectionResponse(_message.Message):
    __slots__ = ("account_id", "connected")
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    CONNECTED_FIELD_NUMBER: _ClassVar[int]
    account_id: str
    connected: bool
    def __init__(self, account_id: _Optional[str] = ..., connected: _Optional[bool] = ...) -> None: ...
