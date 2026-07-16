from __future__ import annotations

import pytest

from app.data.krx.errors import KrxSafeResponseMetadata, KrxValidationDiagnostic


class _UntrustedString(str):
    def __str__(self) -> str:
        return "synthetic-provider-secret"


@pytest.mark.parametrize(
    "overrides",
    [
        {"request_ordinal": True},
        {"request_ordinal": 3},
        {"stage": _UntrustedString("json_decode")},
        {"leaf": _UntrustedString("json_decode_failed")},
        {"service": "synthetic-provider-secret"},
        {"service": _UntrustedString("stk_bydd_trd")},
        {"http_status": True},
        {"http_status": 99},
        {"content_type_class": "text/html"},
        {"content_type_class": _UntrustedString("application_json")},
        {"body_class": "synthetic-provider-secret"},
        {"body_class": _UntrustedString("json_candidate")},
        {"body_size_bucket": "4097"},
        {"body_size_bucket": _UntrustedString("1_4k")},
        {"utf8_valid": 1},
        {"utf8_bom_present": 0},
        {"top_level_type": "mapping"},
        {"top_level_type": _UntrustedString("object")},
        {"top_level_key_count": True},
        {"top_level_key_count": 17},
        {"expected_block_present": 1},
        {"row_container_type": "tuple"},
        {"row_container_type": _UntrustedString("list")},
        {"row_count": 5_002},
        {"row_ordinal": 0},
        {"official_field": "AUTH_KEY"},
        {"official_field": _UntrustedString("MKTCAP")},
        {"missing_official_field_count": 16},
        {"unexpected_row_key_count": 17},
    ],
)
def test_validation_diagnostic_rejects_non_allowlisted_or_out_of_bounds_values(
    overrides: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "stage": "json_decode",
        "leaf": "json_decode_failed",
    }
    values.update(overrides)

    with pytest.raises(ValueError):
        KrxValidationDiagnostic(**values)  # type: ignore[arg-type]


def test_validation_diagnostic_rejects_stage_leaf_mismatch_and_unknown_leaf() -> None:
    with pytest.raises(ValueError):
        KrxValidationDiagnostic(stage="row_shape", leaf="json_decode_failed")
    with pytest.raises(ValueError):
        KrxValidationDiagnostic.for_leaf("synthetic-provider-secret")


def test_validation_factory_rejects_unknown_field_without_echoing_its_name() -> None:
    marker = "synthetic_provider_secret"

    with pytest.raises(ValueError) as exc_info:
        KrxValidationDiagnostic.for_leaf(
            "json_decode_failed",
            **{marker: 1},
        )

    assert marker not in str(exc_info.value)
    assert marker not in repr(exc_info.value)


@pytest.mark.parametrize(
    "overrides",
    [
        {"content_type_class": "text/html"},
        {"content_type_class": _UntrustedString("application_json")},
        {"body_class": "synthetic-provider-secret"},
        {"body_class": _UntrustedString("json_candidate")},
        {"body_size_bucket": "4097"},
        {"body_size_bucket": _UntrustedString("1_4k")},
        {"utf8_valid": 1},
        {"utf8_bom_present": 0},
    ],
)
def test_safe_response_metadata_rejects_raw_or_untyped_values(
    overrides: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "content_type_class": "application_json",
        "body_class": "json_candidate",
        "body_size_bucket": "1_4k",
        "utf8_valid": True,
        "utf8_bom_present": False,
    }
    values.update(overrides)

    with pytest.raises(ValueError):
        KrxSafeResponseMetadata(**values)  # type: ignore[arg-type]
