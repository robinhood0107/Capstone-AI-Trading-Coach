from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Self

_VALIDATION_STAGE_BY_LEAF = {
    "content_type_missing": "media_type",
    "content_type_multiple": "media_type",
    "content_type_unsupported": "media_type",
    "body_empty": "response_body",
    "body_too_large": "response_body",
    "response_body_unavailable": "response_body",
    "response_headers_invalid": "response_body",
    "json_decode_failed": "json_decode",
    "json_limits_exceeded": "json_limits",
    "payload_not_object": "root_shape",
    "envelope_key_mismatch": "envelope_shape",
    "rows_not_list": "envelope_shape",
    "rows_empty": "envelope_shape",
    "rows_too_many": "envelope_shape",
    "row_not_object": "row_shape",
    "row_field_set_mismatch": "row_shape",
    "row_non_string": "row_shape",
    "row_date_mismatch": "row_semantics",
    "row_market_mismatch": "row_semantics",
    "row_symbol_invalid": "row_semantics",
    "row_name_invalid": "row_semantics",
    "row_numeric_invalid": "row_semantics",
    "row_symbol_duplicate": "row_semantics",
}
_CONTENT_TYPE_CLASSES = frozenset(
    {"application_json", "structured_json", "missing", "multiple", "other"}
)
_BODY_CLASSES = frozenset({"empty", "json_candidate", "html_like", "text_like", "opaque"})
_BODY_SIZE_BUCKETS = frozenset({"empty", "1_4k", "4k_64k", "64k_1m", "1m_4m"})
_TOP_LEVEL_TYPES = frozenset({"object", "array", "string", "number", "boolean", "null"})
_ROW_CONTAINER_TYPES = frozenset({"list", "object", "scalar", "null"})
_SERVICES = frozenset({"stk_bydd_trd", "ksq_bydd_trd"})
_OFFICIAL_FIELDS = frozenset(
    {
        "BAS_DD",
        "ISU_CD",
        "ISU_NM",
        "MKT_NM",
        "SECT_TP_NM",
        "TDD_CLSPRC",
        "CMPPREVDD_PRC",
        "FLUC_RT",
        "TDD_OPNPRC",
        "TDD_HGPRC",
        "TDD_LWPRC",
        "ACC_TRDVOL",
        "ACC_TRDVAL",
        "MKTCAP",
        "LIST_SHRS",
    }
)
_DIAGNOSTIC_VALUE_FIELDS = frozenset(
    {
        "request_ordinal",
        "service",
        "http_status",
        "content_type_class",
        "body_class",
        "body_size_bucket",
        "utf8_valid",
        "utf8_bom_present",
        "top_level_type",
        "top_level_key_count",
        "expected_block_present",
        "row_container_type",
        "row_count",
        "row_ordinal",
        "official_field",
        "missing_official_field_count",
        "unexpected_row_key_count",
    }
)


class KrxError(RuntimeError):
    """provider 원문이나 인증정보를 경계 밖으로 전달하지 않는 KRX 공통 오류다."""


@dataclass(frozen=True, slots=True)
class KrxSafeResponseMetadata:
    """원문 header/body 없이 response validation에 필요한 파생 분류만 보존한다."""

    content_type_class: str
    body_class: str
    body_size_bucket: str
    utf8_valid: bool
    utf8_bom_present: bool

    def __post_init__(self) -> None:
        if (
            type(self.content_type_class) is not str
            or self.content_type_class not in _CONTENT_TYPE_CLASSES
        ):
            raise ValueError("KRX response content type class is invalid")
        if type(self.body_class) is not str or self.body_class not in _BODY_CLASSES:
            raise ValueError("KRX response body class is invalid")
        if (
            type(self.body_size_bucket) is not str
            or self.body_size_bucket not in _BODY_SIZE_BUCKETS
        ):
            raise ValueError("KRX response body size bucket is invalid")
        if type(self.utf8_valid) is not bool or type(self.utf8_bom_present) is not bool:
            raise ValueError("KRX response encoding flags are invalid")


@dataclass(frozen=True, slots=True)
class KrxValidationDiagnostic:
    """실제 key/value를 저장하지 않는 KRX validation failure allowlist다."""

    stage: str
    leaf: str
    request_ordinal: int | None = None
    service: str | None = None
    http_status: int | None = None
    content_type_class: str | None = None
    body_class: str | None = None
    body_size_bucket: str | None = None
    utf8_valid: bool | None = None
    utf8_bom_present: bool | None = None
    top_level_type: str | None = None
    top_level_key_count: int | None = None
    expected_block_present: bool | None = None
    row_container_type: str | None = None
    row_count: int | None = None
    row_ordinal: int | None = None
    official_field: str | None = None
    missing_official_field_count: int | None = None
    unexpected_row_key_count: int | None = None

    def __post_init__(self) -> None:
        if (
            type(self.stage) is not str
            or type(self.leaf) is not str
            or _VALIDATION_STAGE_BY_LEAF.get(self.leaf) != self.stage
        ):
            raise ValueError("KRX validation stage and leaf are invalid")
        if self.request_ordinal is not None and (
            type(self.request_ordinal) is not int or self.request_ordinal not in {1, 2}
        ):
            raise ValueError("KRX validation request ordinal is invalid")
        if self.service is not None and (
            type(self.service) is not str or self.service not in _SERVICES
        ):
            raise ValueError("KRX validation service is invalid")
        if self.http_status is not None and (
            type(self.http_status) is not int or not 100 <= self.http_status <= 599
        ):
            raise ValueError("KRX validation HTTP status is invalid")
        self._validate_optional_enum(
            self.content_type_class,
            _CONTENT_TYPE_CLASSES,
            "content type class",
        )
        self._validate_optional_enum(self.body_class, _BODY_CLASSES, "body class")
        self._validate_optional_enum(
            self.body_size_bucket,
            _BODY_SIZE_BUCKETS,
            "body size bucket",
        )
        self._validate_optional_enum(
            self.top_level_type,
            _TOP_LEVEL_TYPES,
            "top-level type",
        )
        self._validate_optional_enum(
            self.row_container_type,
            _ROW_CONTAINER_TYPES,
            "row container type",
        )
        for value, label in (
            (self.utf8_valid, "UTF-8 validity"),
            (self.utf8_bom_present, "UTF-8 BOM flag"),
            (self.expected_block_present, "expected block flag"),
        ):
            if value is not None and type(value) is not bool:
                raise ValueError(f"KRX validation {label} is invalid")
        self._validate_optional_count(self.top_level_key_count, maximum=16, label="key count")
        self._validate_optional_count(self.row_count, maximum=5_001, label="row count")
        if self.row_ordinal is not None and (
            type(self.row_ordinal) is not int or not 1 <= self.row_ordinal <= 5_001
        ):
            raise ValueError("KRX validation row ordinal is invalid")
        if self.official_field is not None and (
            type(self.official_field) is not str or self.official_field not in _OFFICIAL_FIELDS
        ):
            raise ValueError("KRX validation official field is invalid")
        self._validate_optional_count(
            self.missing_official_field_count,
            maximum=15,
            label="missing field count",
        )
        self._validate_optional_count(
            self.unexpected_row_key_count,
            maximum=16,
            label="unexpected key count",
        )

    @classmethod
    def for_leaf(cls, leaf: str, **values: object) -> Self:
        """leaf에 결속된 stage를 source-controlled mapping에서만 생성한다."""
        if type(leaf) is not str:
            raise ValueError("KRX validation leaf is invalid")
        stage = _VALIDATION_STAGE_BY_LEAF.get(leaf)
        if stage is None:
            raise ValueError("KRX validation leaf is invalid")
        if not all(type(name) is str for name in values) or not set(values).issubset(
            _DIAGNOSTIC_VALUE_FIELDS
        ):
            raise ValueError("KRX validation fields are invalid")
        return cls(stage=stage, leaf=leaf, **values)  # type: ignore[arg-type]

    def with_context(
        self,
        *,
        request_ordinal: int,
        service: str,
        http_status: int,
        response_metadata: KrxSafeResponseMetadata | None,
    ) -> Self:
        """client만 알고 있는 request 순서와 안전 response 분류를 immutable하게 결합한다."""
        if response_metadata is None:
            return replace(
                self,
                request_ordinal=request_ordinal,
                service=service,
                http_status=http_status,
            )
        return replace(
            self,
            request_ordinal=request_ordinal,
            service=service,
            http_status=http_status,
            content_type_class=response_metadata.content_type_class,
            body_class=response_metadata.body_class,
            body_size_bucket=response_metadata.body_size_bucket,
            utf8_valid=response_metadata.utf8_valid,
            utf8_bom_present=response_metadata.utf8_bom_present,
        )

    def to_cli_fields(self) -> tuple[tuple[str, str], ...]:
        """CLI가 임의 객체를 직렬화하지 않도록 고정된 scalar 순서만 반환한다."""
        fields: list[tuple[str, str]] = [
            ("validation_stage", self.stage),
            ("validation_leaf", self.leaf),
        ]
        for name, value in (
            ("request_ordinal", self.request_ordinal),
            ("service", self.service),
            ("http_status", self.http_status),
            ("content_type_class", self.content_type_class),
            ("body_class", self.body_class),
            ("body_size_bucket", self.body_size_bucket),
            ("utf8_valid", self.utf8_valid),
            ("utf8_bom_present", self.utf8_bom_present),
            ("top_level_type", self.top_level_type),
            ("top_level_key_count", self.top_level_key_count),
            ("expected_block_present", self.expected_block_present),
            ("row_container_type", self.row_container_type),
            ("row_count", self.row_count),
            ("row_ordinal", self.row_ordinal),
            ("official_field", self.official_field),
            ("missing_official_field_count", self.missing_official_field_count),
            ("unexpected_row_key_count", self.unexpected_row_key_count),
        ):
            if value is not None:
                fields.append((name, _stable_scalar(value)))
        return tuple(fields)

    @staticmethod
    def _validate_optional_enum(value: str | None, allowed: frozenset[str], label: str) -> None:
        if value is not None and (type(value) is not str or value not in allowed):
            raise ValueError(f"KRX validation {label} is invalid")

    @staticmethod
    def _validate_optional_count(value: int | None, *, maximum: int, label: str) -> None:
        if value is not None and (type(value) is not int or not 0 <= value <= maximum):
            raise ValueError(f"KRX validation {label} is invalid")


class KrxParseError(KrxError):
    """공식 응답 계약을 벗어난 KRX payload를 원문 비노출 오류로 거부한다."""

    code = "invalid_response"
    retryable = False

    def __init__(self, diagnostic: KrxValidationDiagnostic | None = None) -> None:
        self.diagnostic = diagnostic
        super().__init__("krx_parse_error:invalid_response")


def _stable_scalar(value: object) -> str:
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) in {int, str}:
        return str(value)
    raise ValueError("KRX validation scalar is invalid")
