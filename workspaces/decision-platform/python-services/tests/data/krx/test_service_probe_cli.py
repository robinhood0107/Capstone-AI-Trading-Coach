from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Self

import pytest

from app.data._shared.canonical_json import canonical_json_sha256
from app.data.krx import service_probe_cli
from app.data.krx._credential_transport import KrxCredentialError
from app.data.krx.client import KrxHttpError
from app.data.krx.errors import KrxValidationDiagnostic
from app.data.krx.parsers import KrxDailyRow
from app.data.krx.service_probe_cli import main
from app.data.krx.settings import KrxOpenApiSettings


_AS_OF = date(2026, 7, 14)
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _row(
    *,
    symbol: str,
    market: str,
    market_cap: int,
    trading_value: int,
) -> KrxDailyRow:
    return KrxDailyRow(
        as_of_date=_AS_OF,
        symbol=symbol,
        name=f"합성종목{symbol}",
        market=market,  # type: ignore[arg-type]
        market_cap=market_cap,
        trading_value=trading_value,
    )


@dataclass
class _ProbeState:
    rows: tuple[KrxDailyRow, ...] = field(
        default_factory=lambda: (
            _row(
                symbol="005930",
                market="KOSPI",
                market_cap=500_000,
                trading_value=900_000,
            ),
            _row(
                symbol="000660",
                market="KOSPI",
                market_cap=0,
                trading_value=800_000,
            ),
        )
    )
    error: Exception | None = None
    close_error: Exception | None = None
    physical_attempt_count: int = 1
    created: int = 0
    closed: int = 0
    calls: list[tuple[date, str]] = field(default_factory=list)
    settings: KrxOpenApiSettings | None = None


class _FakeClient:
    def __init__(self, state: _ProbeState) -> None:
        self._state = state
        self.physical_attempt_count = state.physical_attempt_count

    def fetch_service_rows(
        self,
        as_of: date,
        *,
        service: str,
    ) -> tuple[KrxDailyRow, ...]:
        self._state.calls.append((as_of, service))
        if self._state.error is not None:
            raise self._state.error
        return self._state.rows

    def close(self) -> None:
        self._state.closed += 1
        if self._state.close_error is not None:
            raise self._state.close_error

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _install_client(
    monkeypatch: pytest.MonkeyPatch,
    state: _ProbeState,
) -> None:
    def factory(settings: KrxOpenApiSettings) -> _FakeClient:
        state.created += 1
        state.settings = settings
        return _FakeClient(state)

    monkeypatch.setattr(service_probe_cli, "KrxOpenApiClient", factory)


def _install_common_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_probe_cli, "resolve_latest_available_date", lambda _: _AS_OF)


def _parse_fields(line: str) -> dict[str, str]:
    return dict(part.split("=", 1) for part in line.strip().split())


def _expected_source_sha256(rows: tuple[KrxDailyRow, ...]) -> str:
    ordered = sorted(
        rows,
        key=lambda row: (-row.market_cap, -row.trading_value, row.symbol),
    )
    return canonical_json_sha256(
        [
            {
                "asOfDate": row.as_of_date.isoformat(),
                "symbol": row.symbol,
                "name": row.name,
                "market": row.market,
                "marketCap": row.market_cap,
                "tradingValue": row.trading_value,
            }
            for row in ordered
        ]
    )


def test_online_and_service_flags_are_mandatory_before_client_creation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _ProbeState()
    _install_client(monkeypatch, state)

    for argv in (
        ["--as-of", _AS_OF.isoformat(), "--service", "stk_bydd_trd"],
        ["--online", "--as-of", _AS_OF.isoformat()],
        ["--online", "--service", "stk_bydd_trd"],
        [
            "--online",
            "--as-of",
            _AS_OF.isoformat(),
            "--service",
            "stk_bydd_trd",
            "--data-dir",
            "private",
        ],
    ):
        with pytest.raises(SystemExit) as exc_info:
            main(argv)
        assert exc_info.value.code == 2

    assert state.created == 0
    assert capsys.readouterr().err.count(
        "source=krx operation=service_probe code=invalid_arguments"
    ) == 4


def test_non_allowlisted_service_is_rejected_before_client_creation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _ProbeState()
    _install_client(monkeypatch, state)

    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "--online",
                "--as-of",
                _AS_OF.isoformat(),
                "--service",
                "knx_bydd_trd",
            ]
        )

    assert exc_info.value.code == 2
    assert state.created == 0
    assert capsys.readouterr().err.strip() == (
        "source=krx operation=service_probe code=invalid_arguments"
    )


@pytest.mark.parametrize(
    ("service", "market"),
    [
        ("stk_bydd_trd", "KOSPI"),
        ("ksq_bydd_trd", "KOSDAQ"),
    ],
)
def test_probe_success_calls_one_service_and_never_writes_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    service: str,
    market: str,
) -> None:
    rows = (
        _row(
            symbol="005930",
            market=market,
            market_cap=500_000,
            trading_value=900_000,
        ),
        _row(
            symbol="000660",
            market=market,
            market_cap=0,
            trading_value=800_000,
        ),
        _row(
            symbol="00279K",
            market=market,
            market_cap=700_000,
            trading_value=950_000,
        ),
    )
    state = _ProbeState(rows=tuple(reversed(rows)))
    _install_client(monkeypatch, state)
    _install_common_preflight(monkeypatch)
    monkeypatch.setenv("KRX_OPENAPI_MAX_CALLS_PER_RUN", "2")
    monkeypatch.setenv("KRX_OPENAPI_READ_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("KRX_OPENAPI_LOGICAL_DEADLINE_SECONDS", "260")
    monkeypatch.chdir(tmp_path)
    manifest_path = tmp_path / "data" / "kis" / "universe_manifest.json"
    report_path = tmp_path / "data" / "kis" / "reports" / "universe_refresh.md"
    report_path.parent.mkdir(parents=True)
    manifest_path.write_bytes(b"manual-manifest-sentinel")
    report_path.write_bytes(b"manual-report-sentinel")
    initial_files = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    monotonic_values = iter((1_000_000_000, 1_125_000_000))
    monkeypatch.setattr(
        service_probe_cli.time,
        "monotonic_ns",
        lambda: next(monotonic_values),
    )

    exit_code = main(
        [
            "--online",
            "--as-of",
            _AS_OF.isoformat(),
            "--service",
            service,
        ]
    )

    captured = capsys.readouterr()
    fields = _parse_fields(captured.out)
    assert exit_code == 0
    assert captured.err == ""
    assert state.created == 1
    assert state.closed == 1
    assert state.calls == [(_AS_OF, service)]
    assert state.settings is not None
    assert state.settings.max_calls_per_run == 1
    assert state.settings.read_timeout_seconds == 120.0
    assert state.settings.logical_deadline_seconds == 130.0
    assert fields == {
        "source": "krx",
        "operation": "service_probe",
        "code": "complete",
        "service": service,
        "as_of": _AS_OF.isoformat(),
        "row_count": "3",
        "positive_candidate_count": "1",
        "source_sha256": fields["source_sha256"],
        "elapsed_ms": "125",
        "physical_attempts": "1",
    }
    assert _SHA256.fullmatch(fields["source_sha256"]) is not None
    assert fields["source_sha256"] == _expected_source_sha256(rows)
    assert {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == initial_files


@pytest.mark.parametrize("physical_attempt_count", [0, 2])
def test_probe_refuses_complete_when_physical_attempt_count_is_not_exactly_one(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    physical_attempt_count: int,
) -> None:
    state = _ProbeState(physical_attempt_count=physical_attempt_count)
    _install_client(monkeypatch, state)
    _install_common_preflight(monkeypatch)

    exit_code = main(
        [
            "--online",
            "--as-of",
            _AS_OF.isoformat(),
            "--service",
            "stk_bydd_trd",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err == (
        "source=krx operation=service_probe code=attempt_accounting_invalid "
        f"service=stk_bydd_trd physical_attempts={physical_attempt_count}\n"
    )
    assert state.closed == 1


def test_probe_cleanup_failure_never_emits_complete_or_untrusted_text(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    marker = "synthetic-cleanup-secret https://provider.invalid/private"
    state = _ProbeState(close_error=RuntimeError(marker))
    _install_client(monkeypatch, state)
    _install_common_preflight(monkeypatch)

    exit_code = main(
        [
            "--online",
            "--as-of",
            _AS_OF.isoformat(),
            "--service",
            "stk_bydd_trd",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err == (
        "source=krx operation=service_probe code=collection_failed "
        "service=stk_bydd_trd physical_attempts=1\n"
    )
    assert marker not in captured.err
    assert "provider.invalid" not in captured.err


def test_probe_failure_stops_after_one_attempt_and_exposes_no_untrusted_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    marker = f"synthetic-secret {tmp_path} https://provider.invalid/private"
    state = _ProbeState(
        error=RuntimeError(marker),
        physical_attempt_count=1,
    )
    _install_client(monkeypatch, state)
    _install_common_preflight(monkeypatch)
    monkeypatch.chdir(tmp_path)

    exit_code = main(
        [
            "--online",
            "--as-of",
            _AS_OF.isoformat(),
            "--service",
            "stk_bydd_trd",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err == (
        "source=krx operation=service_probe code=collection_failed "
        "service=stk_bydd_trd physical_attempts=1\n"
    )
    assert marker not in captured.err
    assert str(tmp_path) not in captured.err
    assert "provider.invalid" not in captured.err
    assert state.calls == [(_AS_OF, "stk_bydd_trd")]
    assert list(tmp_path.rglob("*")) == []


def test_probe_failure_keeps_only_typed_allowlisted_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    diagnostic = KrxValidationDiagnostic(
        stage="envelope_shape",
        leaf="envelope_key_mismatch",
        request_ordinal=1,
        service="stk_bydd_trd",
        http_status=200,
        content_type_class="application_json",
        body_class="json_candidate",
        body_size_bucket="1_4k",
        utf8_valid=True,
        utf8_bom_present=False,
        top_level_type="object",
        top_level_key_count=2,
        expected_block_present=False,
    )
    state = _ProbeState(
        error=KrxHttpError(
            "parse_invalid_response",
            status_code=200,
            validation_diagnostic=diagnostic,
        ),
        physical_attempt_count=1,
    )
    _install_client(monkeypatch, state)
    _install_common_preflight(monkeypatch)
    monkeypatch.chdir(tmp_path)

    exit_code = main(
        [
            "--online",
            "--as-of",
            _AS_OF.isoformat(),
            "--service",
            "stk_bydd_trd",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err.startswith(
        "source=krx operation=service_probe code=invalid_response "
        "service=stk_bydd_trd physical_attempts=1 "
    )
    assert "validation_stage=envelope_shape" in captured.err
    assert "validation_leaf=envelope_key_mismatch" in captured.err
    assert "http_status=200" in captured.err
    assert "AUTH_KEY" not in captured.err
    assert str(tmp_path) not in captured.err
    assert list(tmp_path.rglob("*")) == []


def test_probe_ignores_untrusted_diagnostic_subclass(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    marker = "synthetic-provider-secret"

    class _UntrustedDiagnostic(KrxValidationDiagnostic):
        def to_cli_fields(self) -> tuple[tuple[str, str], ...]:
            return (("unsafe", marker),)

    state = _ProbeState(
        error=KrxHttpError(
            "parse_invalid_response",
            status_code=200,
            validation_diagnostic=_UntrustedDiagnostic(
                stage="json_decode",
                leaf="json_decode_failed",
            ),
        ),
        physical_attempt_count=1,
    )
    _install_client(monkeypatch, state)
    _install_common_preflight(monkeypatch)

    exit_code = main(
        [
            "--online",
            "--as-of",
            _AS_OF.isoformat(),
            "--service",
            "stk_bydd_trd",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err == (
        "source=krx operation=service_probe code=invalid_response "
        "service=stk_bydd_trd physical_attempts=1\n"
    )
    assert marker not in captured.err


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (KrxCredentialError("read_timeout"), "read_timeout"),
        (KrxCredentialError("logical_deadline_exceeded"), "logical_deadline_exceeded"),
    ],
)
def test_probe_timeout_codes_are_stable_and_not_retried(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
    expected_code: str,
) -> None:
    state = _ProbeState(error=error, physical_attempt_count=1)
    _install_client(monkeypatch, state)
    _install_common_preflight(monkeypatch)

    exit_code = main(
        [
            "--online",
            "--as-of",
            _AS_OF.isoformat(),
            "--service",
            "ksq_bydd_trd",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert state.calls == [(_AS_OF, "ksq_bydd_trd")]
    assert captured.err == (
        f"source=krx operation=service_probe code={expected_code} "
        "service=ksq_bydd_trd physical_attempts=1\n"
    )
