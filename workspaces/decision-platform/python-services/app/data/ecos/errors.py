from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final

_FAILURE_STAGES: Final = frozenset(
    {
        "response_headers",
        "response_body",
        "json_decode",
        "json_limits",
        "application_envelope",
        "metadata_envelope",
        "pagination",
        "candidate_scan",
        "candidate_match",
        "field_validation",
        "registry_identity",
        "searchability",
    }
)
_FAILURE_REASONS: Final = frozenset(
    {
        "content_type_missing",
        "content_type_multiple",
        "content_type_unsupported",
        "response_headers_invalid",
        "body_empty",
        "body_too_large",
        "response_body_unavailable",
        "json_decode_failed",
        "json_limits_exceeded",
        "application_error",
        "metadata_envelope_missing",
        "metadata_envelope_invalid",
        "pagination_invalid",
        "candidate_not_found",
        "candidate_duplicate",
        "candidate_invalid",
        "field_invalid",
        "identity_mismatch",
        "not_searchable",
    }
)
_SERVICES: Final = frozenset({"StatisticTableList", "StatisticItemList"})
_SERIES_IDS: Final = frozenset({"policy-rate", "krw-usd-rate"})
_CONTENT_TYPE_CLASSES: Final = frozenset(
    {"application_json", "structured_json", "missing", "multiple", "other"}
)
_FIELDS: Final = frozenset(
    {
        "stat_code",
        "item_code",
        "cycle",
        "item_name",
        "unit_name",
        "searchable",
        "list_total_count",
        "row",
    }
)
_FIELD_KINDS: Final = frozenset(
    {
        "missing",
        "null",
        "wrong_type",
        "empty",
        "untrimmed",
        "too_long",
        "mismatch",
        "not_found",
        "duplicate",
        "truncated",
    }
)


@dataclass(frozen=True)
class ECOSDiagnostic:
    """ECOS 원문 없이 실패 leaf와 허용된 숫자·분류 정보만 전달하는 immutable 진단값이다."""

    failure_stage: str
    failure_reason: str
    request_ordinal: int | None = None
    service: str | None = None
    series_id: str | None = None
    http_status: int | None = None
    content_type_class: str | None = None
    response_bytes: int | None = None
    list_total_count: int | None = None
    row_count: int | None = None
    expected_page_size: int | None = None
    candidate_match_count: int | None = None
    field: str | None = None
    field_kind: str | None = None
    diagnostic_version: int = 1

    def __post_init__(self) -> None:
        if type(self.diagnostic_version) is not int or self.diagnostic_version != 1:
            raise ValueError("unsupported ECOS diagnostic version")
        if self.failure_stage not in _FAILURE_STAGES:
            raise ValueError("ECOS diagnostic failure stage is not allowed")
        if self.failure_reason not in _FAILURE_REASONS:
            raise ValueError("ECOS diagnostic failure reason is not allowed")
        if self.request_ordinal is not None and (
            type(self.request_ordinal) is not int or self.request_ordinal not in {1, 2, 3, 4}
        ):
            raise ValueError("ECOS diagnostic request ordinal is out of bounds")
        if self.service is not None and self.service not in _SERVICES:
            raise ValueError("ECOS diagnostic service is not allowed")
        if self.series_id is not None and self.series_id not in _SERIES_IDS:
            raise ValueError("ECOS diagnostic series is not allowed")
        if (
            self.content_type_class is not None
            and self.content_type_class not in _CONTENT_TYPE_CLASSES
        ):
            raise ValueError("ECOS diagnostic content type class is not allowed")
        if self.field is not None and self.field not in _FIELDS:
            raise ValueError("ECOS diagnostic field is not allowed")
        if self.field_kind is not None and self.field_kind not in _FIELD_KINDS:
            raise ValueError("ECOS diagnostic field kind is not allowed")
        numeric_values = (
            self.http_status,
            self.response_bytes,
            self.list_total_count,
            self.row_count,
            self.expected_page_size,
            self.candidate_match_count,
        )
        if any(
            isinstance(value, bool)
            or (value is not None and (not isinstance(value, int) or value < 0))
            for value in numeric_values
        ):
            raise ValueError("ECOS diagnostic numeric value is invalid")
        if self.http_status is not None and not 100 <= self.http_status <= 599:
            raise ValueError("ECOS diagnostic HTTP status is invalid")

    def with_context(self, *, request_ordinal: int, service: str, series_id: str) -> ECOSDiagnostic:
        """결정적 preflight 순서와 candidate identity만 더하고 기존 진단값은 변경하지 않는다."""
        return replace(
            self,
            request_ordinal=request_ordinal,
            service=service,
            series_id=series_id,
        )

    def with_safe_response(
        self,
        *,
        http_status: int | None,
        content_type_class: str | None,
        response_bytes: int | None,
    ) -> ECOSDiagnostic:
        """transport가 만든 allowlist scalar만 더하고 기존 leaf 분류는 그대로 유지한다."""
        return replace(
            self,
            http_status=self.http_status if self.http_status is not None else http_status,
            content_type_class=(
                self.content_type_class
                if self.content_type_class is not None
                else content_type_class
            ),
            response_bytes=self.response_bytes
            if self.response_bytes is not None
            else response_bytes,
        )

    def to_payload(self) -> dict[str, object]:
        """operator evidence의 `sanitizedPreflight.diagnostic` 허용 필드만 직렬화한다."""
        optional = {
            "requestOrdinal": self.request_ordinal,
            "service": self.service,
            "seriesId": self.series_id,
            "httpStatus": self.http_status,
            "contentTypeClass": self.content_type_class,
            "responseBytes": self.response_bytes,
            "listTotalCount": self.list_total_count,
            "rowCount": self.row_count,
            "expectedPageSize": self.expected_page_size,
            "candidateMatchCount": self.candidate_match_count,
            "field": self.field,
            "fieldKind": self.field_kind,
        }
        payload: dict[str, object] = {
            "diagnosticVersion": self.diagnostic_version,
            "failureStage": self.failure_stage,
            "failureReason": self.failure_reason,
        }
        payload.update({key: value for key, value in optional.items() if value is not None})
        return payload


class ECOSError(RuntimeError):
    """ECOS 경계 밖으로 provider 원문을 전달하지 않는 공통 오류다."""

    def __init__(self, message: str, *, diagnostic: ECOSDiagnostic | None = None) -> None:
        if diagnostic is not None and not isinstance(diagnostic, ECOSDiagnostic):
            raise TypeError("ECOS diagnostic must use the allowlisted value object")
        self.diagnostic = diagnostic
        super().__init__(message)

    def enrich_diagnostic(
        self,
        *,
        request_ordinal: int,
        service: str,
        series_id: str,
    ) -> None:
        """하위 진단이 있을 때만 immutable context-enriched 사본으로 교체한다."""
        if self.diagnostic is not None:
            self.diagnostic = self.diagnostic.with_context(
                request_ordinal=request_ordinal,
                service=service,
                series_id=series_id,
            )

    def enrich_safe_response(
        self,
        *,
        http_status: int | None,
        content_type_class: str | None,
        response_bytes: int | None,
    ) -> None:
        """하위 leaf 진단이 있을 때만 transport allowlist scalar를 immutable 사본으로 합친다."""
        if self.diagnostic is not None:
            self.diagnostic = self.diagnostic.with_safe_response(
                http_status=http_status,
                content_type_class=content_type_class,
                response_bytes=response_bytes,
            )


class ECOSParseError(ECOSError):
    """형식 또는 안전 상한을 위반한 ECOS 응답을 고정 메시지로 거부한다."""


class ECOSApplicationError(ECOSError):
    """HTTP 200 응답의 ECOS application code만 보존하는 안전한 오류다."""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool,
        cooldown_seconds: int = 0,
        diagnostic: ECOSDiagnostic | None = None,
    ) -> None:
        self.code = code
        self.retryable = retryable
        self.cooldown_seconds = cooldown_seconds
        super().__init__(f"ecos_application_error:{code}", diagnostic=diagnostic)


class RegistryNotVerifiedError(ECOSError):
    """공식 metadata 검증 전 provisional series의 online 사용을 차단한다."""
