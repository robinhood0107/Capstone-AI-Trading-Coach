from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from copy import deepcopy
from typing import Any, cast

import httpx
import pytest
from pydantic import SecretStr

from app.data.ecos import registry_preflight_cli
from app.data.ecos.errors import ECOSApplicationError, ECOSError, ECOSParseError
from app.data.ecos.http_client import ECOSHttpClient, ECOSHttpError
from app.data.ecos.models import StatisticItemMetadata, StatisticTableMetadata
from app.data.ecos.parsers import parse_statistic_item_list, parse_statistic_table_list
from app.data.ecos.registry_preflight import inspect_registry_metadata
from app.data.ecos.series_registry import CANDIDATE_SERIES, ECOSSeries
from app.data.ecos.settings import ECOSSettings


class _RecordingQuota:
    def __init__(self) -> None:
        self.reservations: list[str] = []

    def reserve(self, *, attempt_id: str) -> None:
        self.reservations.append(attempt_id)


def _table_payload(*, searchable: str = "Y") -> dict[str, object]:
    return {
        "StatisticTableList": {
            "list_total_count": 1,
            "row": [
                {
                    "STAT_CODE": "722Y001",
                    "STAT_NAME": "합성 기준금리",
                    "CYCLE": "D",
                    "SRCH_YN": searchable,
                }
            ],
        }
    }


def _item_payload() -> dict[str, object]:
    return {
        "StatisticItemList": {
            "list_total_count": 1,
            "row": [
                {
                    "STAT_CODE": "722Y001",
                    "ITEM_CODE": "0101000",
                    "ITEM_NAME": "합성 기준금리 항목",
                    "CYCLE": "D",
                    "UNIT_NAME": "%",
                }
            ],
        }
    }


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    settings: ECOSSettings | None = None,
) -> ECOSHttpClient:
    return ECOSHttpClient._for_tests(
        settings or ECOSSettings(_env_file=None, ECOS_MAX_ATTEMPTS_PER_REQUEST=1),
        transport=httpx.MockTransport(handler),
        quota=_RecordingQuota(),
        credential=SecretStr("synthetic-key"),
    )


def _diagnostic(error: BaseException) -> dict[str, object]:
    diagnostic = getattr(error, "diagnostic", None)
    assert diagnostic is not None
    payload = diagnostic.to_payload()
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


@pytest.mark.parametrize(
    ("headers", "expected_reason", "expected_class"),
    [
        ([], "content_type_missing", "missing"),
        (
            [("content-type", "application/json"), ("content-type", "text/plain")],
            "content_type_multiple",
            "multiple",
        ),
        (
            [("content-type", "text/plain; provider-detail=forbidden")],
            "content_type_unsupported",
            "other",
        ),
    ],
)
def test_http_content_type_failures_keep_only_allowlisted_safe_diagnostics(
    headers: list[tuple[str, str]],
    expected_reason: str,
    expected_class: str,
) -> None:
    client = _client(lambda _: httpx.Response(200, headers=headers, content=b"{}"))
    try:
        with pytest.raises(ECOSHttpError) as exc_info:
            client.statistic_table_list(series=CANDIDATE_SERIES[0])
    finally:
        client.close()

    assert _diagnostic(exc_info.value) == {
        "contentTypeClass": expected_class,
        "diagnosticVersion": 1,
        "failureReason": expected_reason,
        "failureStage": "response_headers",
        "httpStatus": 200,
        "responseBytes": 2,
    }
    rendered = repr(exc_info.value)
    assert "provider-detail" not in rendered
    assert "content-type" not in rendered.lower()


@pytest.mark.parametrize(
    ("body", "expected_stage", "expected_reason"),
    [
        (b"", "response_body", "body_empty"),
        (b"{", "json_decode", "json_decode_failed"),
        (b'{"nested":{"too":{"deep":true}}}', "json_limits", "json_limits_exceeded"),
    ],
)
def test_body_decode_and_json_limit_failures_have_distinct_stable_reasons(
    body: bytes,
    expected_stage: str,
    expected_reason: str,
) -> None:
    settings = ECOSSettings(
        _env_file=None,
        ECOS_MAX_ATTEMPTS_PER_REQUEST=1,
        ECOS_JSON_MAX_DEPTH=1,
    )
    client = _client(
        lambda _: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=body,
        ),
        settings=settings,
    )
    try:
        with pytest.raises(ECOSHttpError) as exc_info:
            client.get_json(
                "/api/StatisticSearch/__KEYLESS__/json/kr/1/1/722Y001/D/20260701/20260701/0101000/"
            )
    finally:
        client.close()

    diagnostic = _diagnostic(exc_info.value)
    assert diagnostic["failureStage"] == expected_stage
    assert diagnostic["failureReason"] == expected_reason
    assert diagnostic["contentTypeClass"] == "application_json"
    assert diagnostic["responseBytes"] == len(body)


def test_body_limit_failure_is_classified_without_body_or_header_echo() -> None:
    body = b'{"value":"' + (b"x" * 80) + b'"}'
    client = _client(
        lambda _: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=body,
        ),
        settings=ECOSSettings(
            _env_file=None,
            ECOS_MAX_ATTEMPTS_PER_REQUEST=1,
            ECOS_RESPONSE_MAX_BYTES=64,
        ),
    )
    try:
        with pytest.raises(ECOSHttpError) as exc_info:
            client.statistic_table_list(series=CANDIDATE_SERIES[0])
    finally:
        client.close()

    diagnostic = _diagnostic(exc_info.value)
    assert diagnostic["failureStage"] == "response_body"
    assert diagnostic["failureReason"] == "body_too_large"
    assert "x" * 16 not in f"{exc_info.value!r} {diagnostic!r}"


def test_application_envelope_failure_has_stable_diagnostic_without_provider_message() -> None:
    provider_message = "synthetic provider message must not escape"
    client = _client(
        lambda _: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"RESULT": {"CODE": "ERROR-500", "MESSAGE": provider_message}},
        )
    )
    try:
        with pytest.raises(ECOSApplicationError) as exc_info:
            client.statistic_table_list(series=CANDIDATE_SERIES[0])
    finally:
        client.close()

    diagnostic = _diagnostic(exc_info.value)
    assert diagnostic["failureStage"] == "application_envelope"
    assert diagnostic["failureReason"] == "application_error"
    assert provider_message not in f"{exc_info.value!r} {diagnostic!r}"


def _set_nested(payload: dict[str, object], key: str, value: object) -> dict[str, object]:
    envelope = cast(dict[str, object], payload["StatisticItemList"])
    envelope[key] = value
    return payload


def _set_row(payload: dict[str, object], key: str, value: object) -> dict[str, object]:
    envelope = cast(dict[str, object], payload["StatisticItemList"])
    rows = cast(list[dict[str, object]], envelope["row"])
    rows[0][key] = value
    return payload


def _duplicate_target_row(payload: dict[str, object]) -> dict[str, object]:
    envelope = cast(dict[str, object], payload["StatisticItemList"])
    rows = cast(list[dict[str, object]], envelope["row"])
    rows.append(deepcopy(rows[0]))
    envelope["list_total_count"] = 2
    return payload


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda _: {}, ("metadata_envelope", "metadata_envelope_missing", None, None)),
        (
            lambda _: {"StatisticItemList": []},
            ("metadata_envelope", "metadata_envelope_invalid", None, None),
        ),
        (
            lambda payload: _set_nested(payload, "list_total_count", "1"),
            ("pagination", "pagination_invalid", "list_total_count", "wrong_type"),
        ),
        (
            lambda payload: _set_nested(payload, "list_total_count", 2),
            ("pagination", "pagination_invalid", "row", "truncated"),
        ),
        (
            lambda payload: _set_row(payload, "ITEM_CODE", "9999999"),
            ("candidate_match", "candidate_not_found", "item_code", "not_found"),
        ),
        (
            _duplicate_target_row,
            ("candidate_match", "candidate_duplicate", "item_code", "duplicate"),
        ),
        (
            lambda payload: _set_row(payload, "ITEM_NAME", ""),
            ("field_validation", "field_invalid", "item_name", "empty"),
        ),
        (
            lambda payload: _set_row(payload, "UNIT_NAME", ""),
            ("field_validation", "field_invalid", "unit_name", "empty"),
        ),
        (
            lambda payload: _set_row(payload, "CYCLE", "M"),
            ("field_validation", "field_invalid", "cycle", "mismatch"),
        ),
    ],
)
def test_item_metadata_leaf_failures_are_diagnostic_and_value_free(
    mutation: Callable[[dict[str, object]], dict[str, object]],
    expected: tuple[str, str, str | None, str | None],
) -> None:
    payload = mutation(deepcopy(_item_payload()))

    with pytest.raises(ECOSParseError) as exc_info:
        parse_statistic_item_list(
            payload,
            expected_stat_code="722Y001",
            expected_item_code="0101000",
        )

    diagnostic = _diagnostic(exc_info.value)
    stage, reason, field, field_kind = expected
    assert diagnostic["failureStage"] == stage
    assert diagnostic["failureReason"] == reason
    if field is None:
        assert "field" not in diagnostic
    else:
        assert diagnostic["field"] == field
    if field_kind is None:
        assert "fieldKind" not in diagnostic
    else:
        assert diagnostic["fieldKind"] == field_kind
    assert "9999999" not in repr(diagnostic)


class _BoundaryClient:
    def __init__(self, *, fail_ordinal: int | None = None, identity_mismatch: bool = False) -> None:
        self.fail_ordinal = fail_ordinal
        self.identity_mismatch = identity_mismatch
        self.calls = 0

    def _maybe_fail(self) -> None:
        self.calls += 1
        if self.calls != self.fail_ordinal:
            return
        with pytest.raises(ECOSParseError) as exc_info:
            parse_statistic_item_list(
                {},
                expected_stat_code="722Y001",
                expected_item_code="0101000",
            )
        raise exc_info.value

    def statistic_table_list(self, *, series: ECOSSeries) -> StatisticTableMetadata:
        self._maybe_fail()
        return StatisticTableMetadata(
            stat_code="9999999" if self.identity_mismatch else series.stat_code,
            name="합성 표",
            cycle=series.cycle,
            searchable=True,
        )

    def statistic_item_list(self, *, series: ECOSSeries) -> StatisticItemMetadata:
        self._maybe_fail()
        return StatisticItemMetadata(
            stat_code=series.stat_code,
            item_code=series.item_code1,
            name="합성 항목",
            cycle=series.cycle,
            unit="합성 단위",
        )


@pytest.mark.parametrize(
    ("ordinal", "service", "series_id"),
    [
        (1, "StatisticTableList", "policy-rate"),
        (2, "StatisticItemList", "policy-rate"),
        (3, "StatisticTableList", "krw-usd-rate"),
        (4, "StatisticItemList", "krw-usd-rate"),
    ],
)
def test_preflight_enriches_all_four_request_ordinals(
    ordinal: int,
    service: str,
    series_id: str,
) -> None:
    with pytest.raises(ECOSParseError) as exc_info:
        inspect_registry_metadata(
            client=_BoundaryClient(fail_ordinal=ordinal),
            series=CANDIDATE_SERIES,
        )

    diagnostic = _diagnostic(exc_info.value)
    assert diagnostic["requestOrdinal"] == ordinal
    assert diagnostic["service"] == service
    assert diagnostic["seriesId"] == series_id


def test_registry_identity_and_searchability_failures_have_explicit_leaf_diagnostics() -> None:
    with pytest.raises(ECOSParseError) as identity_error:
        inspect_registry_metadata(
            client=_BoundaryClient(identity_mismatch=True),
            series=CANDIDATE_SERIES,
        )
    identity = _diagnostic(identity_error.value)
    assert identity["failureStage"] == "registry_identity"
    assert identity["failureReason"] == "identity_mismatch"
    assert identity["field"] == "stat_code"
    assert identity["fieldKind"] == "mismatch"

    class NonSearchableClient(_BoundaryClient):
        def statistic_table_list(self, *, series: ECOSSeries) -> StatisticTableMetadata:
            return StatisticTableMetadata(
                stat_code=series.stat_code,
                name="합성 표",
                cycle=series.cycle,
                searchable=False,
            )

    with pytest.raises(ECOSParseError) as searchable_error:
        inspect_registry_metadata(client=NonSearchableClient(), series=CANDIDATE_SERIES)
    searchable = _diagnostic(searchable_error.value)
    assert searchable["failureStage"] == "searchability"
    assert searchable["failureReason"] == "not_searchable"
    assert searchable["field"] == "searchable"


def test_cli_failure_is_canonical_json_and_nests_only_the_safe_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    responses = iter(
        [
            httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=_table_payload(),
            ),
            httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=_set_row(_item_payload(), "ITEM_NAME", ""),
            ),
        ]
    )
    client = _client(lambda _: next(responses))
    monkeypatch.setattr(registry_preflight_cli, "_build_client", lambda: client)

    assert registry_preflight_cli.main(["--online"]) == 1

    rendered_with_newline = capsys.readouterr().out
    assert rendered_with_newline.endswith("\n")
    rendered = rendered_with_newline.removesuffix("\n")
    payload = json.loads(rendered)
    assert rendered == json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    assert payload == {
        "code": "invalid_response",
        "diagnostic": {
            "contentTypeClass": "application_json",
            "diagnosticVersion": 1,
            "failureReason": "field_invalid",
            "failureStage": "field_validation",
            "field": "item_name",
            "fieldKind": "empty",
            "httpStatus": 200,
            "requestOrdinal": 2,
            "responseBytes": len(
                json.dumps(
                    _set_row(_item_payload(), "ITEM_NAME", ""),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
            ),
            "seriesId": "policy-rate",
            "service": "StatisticItemList",
        },
        "operation": "registry_preflight",
        "physicalAttemptCount": 2,
        "source": "ecos",
        "status": "failed",
    }
    lowered = rendered.lower()
    assert "synthetic-key" not in lowered
    assert "https://" not in lowered
    assert "content-type" not in lowered


def test_unknown_cli_exception_omits_diagnostic_key_and_operator_bytes_are_deterministic(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class UnknownClient:
        physical_attempt_count = 0

        def close(self) -> None:
            pass

    client = UnknownClient()
    monkeypatch.setattr(registry_preflight_cli, "_build_client", lambda: client)
    monkeypatch.setattr(
        registry_preflight_cli,
        "inspect_registry_metadata",
        lambda **_: (_ for _ in ()).throw(RuntimeError("synthetic raw secret URL")),
    )

    assert registry_preflight_cli.main(["--online"]) == 1

    rendered = capsys.readouterr().out.removesuffix("\n")
    payload = json.loads(rendered)
    assert "diagnostic" not in payload
    assert rendered == (
        '{"code":"preflight_failed","operation":"registry_preflight",'
        '"physicalAttemptCount":0,"source":"ecos","status":"failed"}'
    )

    evidence: dict[str, Any] = {
        "approvedAt": "2026-07-15T00:00:00Z",
        "candidateIdentities": [],
        "gitHead": "a" * 40,
        "physicalAttemptCount": 0,
        "redisDelta": 0,
        "redisServerTimeAfter": "2026-07-15T00:00:01Z",
        "redisServerTimeBefore": "2026-07-15T00:00:00Z",
        "sanitizedPreflight": payload,
        "sanitizedPreflightSha256": hashlib.sha256(rendered.encode()).hexdigest(),
    }
    first = registry_preflight_cli._canonical_payload_bytes(evidence)
    second = registry_preflight_cli._canonical_payload_bytes(deepcopy(evidence))
    assert first == second
    assert not first.endswith(b"\n")
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()


def test_diagnostic_value_object_rejects_non_allowlisted_fields_and_values() -> None:
    with pytest.raises((TypeError, ValueError)):
        registry_preflight_cli._diagnostic_payload(
            cast(
                ECOSError,
                ECOSParseError("invalid ECOS response", diagnostic=object()),
            )
        )


def test_table_parser_uses_the_same_synthetic_official_shape_without_raw_provider_data() -> None:
    metadata = parse_statistic_table_list(_table_payload(), expected_stat_code="722Y001")

    assert metadata.name == "합성 기준금리"
    assert metadata.searchable is True
    assert "raw" not in repr(metadata).lower()
