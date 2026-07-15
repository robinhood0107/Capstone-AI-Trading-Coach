from __future__ import annotations

import json
import stat
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Self

import pytest

from app.data.krx import universe_refresh_cli
from app.data.krx.parsers import KrxDailyRow
from app.data.krx.universe_refresh_cli import main


_AS_OF = date(2026, 7, 14)


def _row(index: int) -> KrxDailyRow:
    return KrxDailyRow(
        as_of_date=_AS_OF,
        symbol=f"{index:06d}",
        name=f"합성종목{index:02d}",
        market="KOSPI" if index % 2 else "KOSDAQ",
        market_cap=1_000_000 - index * 1_000,
        trading_value=500_000 - index * 100,
    )


@dataclass
class _ClientState:
    rows: tuple[KrxDailyRow, ...] = field(
        default_factory=lambda: tuple(_row(index) for index in range(1, 32))
    )
    error: Exception | None = None
    close_error: Exception | None = None
    created: int = 0
    closed: int = 0
    requested_dates: list[date] = field(default_factory=list)


class _FakeClient:
    def __init__(self, state: _ClientState) -> None:
        self._state = state
        self.physical_attempt_count = 2

    def fetch_universe_rows(self, as_of: date) -> tuple[KrxDailyRow, ...]:
        self._state.requested_dates.append(as_of)
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
    state: _ClientState,
) -> None:
    def factory(*_: object, **__: object) -> _FakeClient:
        state.created += 1
        return _FakeClient(state)

    monkeypatch.setattr(universe_refresh_cli, "KrxOpenApiClient", factory)


def _args(data_dir: Path, *extra: str) -> list[str]:
    return ["--online", "--data-dir", str(data_dir), *extra]


def test_online_flag_is_mandatory_and_missing_flag_creates_no_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _ClientState()
    _install_client(monkeypatch, state)

    with pytest.raises(SystemExit) as exc_info:
        main(["--data-dir", str(tmp_path / "data" / "kis")])

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert state.created == 0
    assert captured.out == ""
    assert captured.err.strip() == ("source=krx operation=universe_refresh code=invalid_arguments")
    assert str(tmp_path) not in captured.err
    assert "AUTH_KEY" not in captured.err


def test_optional_as_of_uses_latest_available_open_api_date(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _ClientState()
    _install_client(monkeypatch, state)
    monkeypatch.setattr(
        universe_refresh_cli,
        "resolve_latest_available_date",
        lambda _: _AS_OF,
    )
    data_dir = tmp_path / "data" / "kis"

    exit_code = main(_args(data_dir))

    assert exit_code == 0
    assert state.created == 1
    assert state.requested_dates == [_AS_OF]
    assert state.closed == 1
    payload = json.loads((data_dir / "universe_manifest.json").read_text(encoding="utf-8"))
    assert payload["asOfDate"] == "2026-07-14"
    assert payload["source"] == "krx-open-api:stk_bydd_trd+ksq_bydd_trd"
    assert len(payload["symbols"]) == 30


def test_explicit_available_date_writes_private_manifest_and_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _ClientState()
    _install_client(monkeypatch, state)
    monkeypatch.setattr(
        universe_refresh_cli,
        "resolve_latest_available_date",
        lambda _: _AS_OF,
    )
    data_dir = tmp_path / "data" / "kis"
    report_path = data_dir / "reports" / "krx_openapi_universe.md"

    exit_code = main(
        _args(
            data_dir,
            "--as-of",
            _AS_OF.isoformat(),
            "--report-path",
            str(report_path),
        )
    )

    manifest_path = data_dir / "universe_manifest.json"
    assert exit_code == 0
    assert state.requested_dates == [_AS_OF]
    assert manifest_path.exists()
    assert report_path.exists()
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(data_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(report_path.parent.stat().st_mode) == 0o700
    assert "AUTH_KEY" not in manifest_path.read_text(encoding="utf-8")
    assert "AUTH_KEY" not in report_path.read_text(encoding="utf-8")
    output = capsys.readouterr().out
    assert output.strip() == (
        "source=krx operation=universe_refresh code=complete physical_attempts=2"
    )
    assert str(tmp_path) not in output


def test_provider_name_cannot_inject_markdown_table_or_remote_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _ClientState()
    rows = list(state.rows)
    rows[0] = replace(
        rows[0],
        name="합성 | ![probe](https://attacker.invalid/x)",
    )
    state.rows = tuple(rows)
    _install_client(monkeypatch, state)
    monkeypatch.setattr(
        universe_refresh_cli,
        "resolve_latest_available_date",
        lambda _: _AS_OF,
    )
    data_dir = tmp_path / "data" / "kis"

    exit_code = main(_args(data_dir, "--as-of", _AS_OF.isoformat()))

    report = (data_dir / "reports" / "universe_refresh.md").read_text(encoding="utf-8")
    assert exit_code == 0
    assert " | ![probe](https://attacker.invalid/x)" not in report
    assert r"합성 \| \!\[probe\]\(https\:\/\/attacker\.invalid\/x\)" in report


@pytest.mark.parametrize(
    ("as_of", "expected_fragment"),
    [
        ("2026-07-15", "available"),
        ("2026-07-12", "session"),
        ("2009-12-30", "range"),
        ("1900-01-02", "range"),
        ("9999-01-01", "available"),
        ("not-a-date", "date"),
    ],
)
def test_explicit_unavailable_or_invalid_date_is_rejected_before_client_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    as_of: str,
    expected_fragment: str,
) -> None:
    state = _ClientState()
    _install_client(monkeypatch, state)
    monkeypatch.setattr(
        universe_refresh_cli,
        "resolve_latest_available_date",
        lambda _: _AS_OF,
    )

    exit_code = main(_args(tmp_path / "data" / "kis", "--as-of", as_of))

    assert exit_code == 2
    assert state.created == 0
    assert expected_fragment in capsys.readouterr().err.lower()


def test_provider_failure_has_no_csv_fallback_or_partial_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    marker = "synthetic-krx-auth-key"
    state = _ClientState(
        error=RuntimeError(f"provider failed https://data-dbg.krx.co.kr AUTH_KEY={marker}")
    )
    _install_client(monkeypatch, state)
    monkeypatch.setattr(
        universe_refresh_cli,
        "resolve_latest_available_date",
        lambda _: _AS_OF,
    )
    data_dir = tmp_path / "data" / "kis"
    # 같은 폴더에 CSV가 있어도 online 실패를 수동 source 성공으로 바꾸지 않는다.
    (tmp_path / "krx.csv").write_text(
        "종목코드,종목명,시장구분,시가총액,거래대금\n005930,삼성전자,KOSPI,1,1\n",
        encoding="utf-8",
    )

    exit_code = main(_args(data_dir, "--as-of", _AS_OF.isoformat()))

    captured = capsys.readouterr()
    rendered = f"{captured.out}\n{captured.err}"
    assert exit_code == 1
    assert state.created == 1
    assert state.closed == 1
    assert not (data_dir / "universe_manifest.json").exists()
    assert not (data_dir / "reports" / "universe_refresh.md").exists()
    assert marker not in rendered
    assert "AUTH_KEY" not in rendered
    assert "data-dbg.krx.co.kr" not in rendered
    assert "physical_attempts=2" in rendered


def test_calendar_failure_is_sanitized_before_client_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    marker = "synthetic-calendar-path-/private/provider"
    state = _ClientState()
    _install_client(monkeypatch, state)
    monkeypatch.setattr(
        universe_refresh_cli,
        "resolve_latest_available_date",
        lambda _: _AS_OF,
    )
    monkeypatch.setattr(
        universe_refresh_cli,
        "is_xkrx_trading_day",
        lambda _: (_ for _ in ()).throw(RuntimeError(marker)),
    )

    exit_code = main(_args(tmp_path / "data" / "kis", "--as-of", "2026-07-13"))

    rendered = capsys.readouterr().err
    assert exit_code == 2
    assert state.created == 0
    assert "calendar_unavailable" in rendered
    assert marker not in rendered


def test_latest_available_date_resolution_failure_is_sanitized_before_client_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    marker = "synthetic-calendar-secret-/private/provider"
    state = _ClientState()
    _install_client(monkeypatch, state)
    monkeypatch.setattr(
        universe_refresh_cli,
        "resolve_latest_available_date",
        lambda _: (_ for _ in ()).throw(RuntimeError(marker)),
    )

    exit_code = main(_args(tmp_path / "data" / "kis"))

    captured = capsys.readouterr()
    assert exit_code == 2
    assert state.created == 0
    assert captured.out == ""
    assert "calendar_unavailable" in captured.err
    assert marker not in captured.err
    assert str(tmp_path) not in captured.err


def test_first_official_service_date_is_inclusive() -> None:
    assert universe_refresh_cli._validated_as_of(  # noqa: SLF001
        "2010-01-04",
        latest=_AS_OF,
    ) == date(2010, 1, 4)


def test_client_close_failure_is_sanitized_and_prevents_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    marker = "synthetic-close-secret https://data-dbg.krx.co.kr/private"
    state = _ClientState(close_error=RuntimeError(marker))
    _install_client(monkeypatch, state)
    monkeypatch.setattr(
        universe_refresh_cli,
        "resolve_latest_available_date",
        lambda _: _AS_OF,
    )
    data_dir = tmp_path / "data" / "kis"

    exit_code = main(_args(data_dir, "--as-of", _AS_OF.isoformat()))

    captured = capsys.readouterr()
    rendered = f"{captured.out}\n{captured.err}"
    assert exit_code == 1
    assert state.closed >= 1
    assert not (data_dir / "universe_manifest.json").exists()
    assert not (data_dir / "reports" / "universe_refresh.md").exists()
    assert "collection_failed" in rendered
    assert "physical_attempts=2" in rendered
    assert marker not in rendered
    assert str(tmp_path) not in rendered


def test_unknown_csv_fallback_argument_is_rejected_by_online_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _ClientState()
    _install_client(monkeypatch, state)

    with pytest.raises(SystemExit) as exc_info:
        main(
            _args(
                tmp_path / "data" / "kis",
                "--krx-export",
                str(tmp_path / "krx.csv"),
            )
        )

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert state.created == 0
    assert captured.out == ""
    assert captured.err.strip() == ("source=krx operation=universe_refresh code=invalid_arguments")
    assert "--krx-export" not in captured.err
    assert str(tmp_path) not in captured.err


def test_symlink_manifest_target_is_rejected_without_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _ClientState()
    _install_client(monkeypatch, state)
    monkeypatch.setattr(
        universe_refresh_cli,
        "resolve_latest_available_date",
        lambda _: _AS_OF,
    )
    data_dir = tmp_path / "data" / "kis"
    data_dir.mkdir(parents=True)
    outside = tmp_path / "must-not-change.json"
    outside.write_text('{"sentinel":true}\n', encoding="utf-8")
    (data_dir / "universe_manifest.json").symlink_to(outside)

    exit_code = main(_args(data_dir, "--as-of", _AS_OF.isoformat()))

    assert exit_code == 1
    assert outside.read_text(encoding="utf-8") == '{"sentinel":true}\n'
    assert (data_dir / "universe_manifest.json").is_symlink()
    assert not (data_dir / "reports" / "universe_refresh.md").exists()
    error_text = capsys.readouterr().err.lower()
    assert any(fragment in error_text for fragment in ("symlink", "output", "path"))
    assert "physical_attempts=0" in error_text


def test_symlink_report_target_is_rejected_before_client_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _ClientState()
    _install_client(monkeypatch, state)
    monkeypatch.setattr(
        universe_refresh_cli,
        "resolve_latest_available_date",
        lambda _: _AS_OF,
    )
    data_dir = tmp_path / "data" / "kis"
    reports = data_dir / "reports"
    reports.mkdir(parents=True)
    outside = tmp_path / "must-not-change.md"
    outside.write_text("sentinel\n", encoding="utf-8")
    report_path = reports / "universe_refresh.md"
    report_path.symlink_to(outside)

    exit_code = main(_args(data_dir, "--as-of", _AS_OF.isoformat()))

    rendered = capsys.readouterr().err
    assert exit_code == 1
    assert state.created == 0
    assert outside.read_text(encoding="utf-8") == "sentinel\n"
    assert report_path.is_symlink()
    assert not (data_dir / "universe_manifest.json").exists()
    assert "physical_attempts=0" in rendered


def test_parent_traversal_is_rejected_before_client_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _ClientState()
    _install_client(monkeypatch, state)
    monkeypatch.setattr(
        universe_refresh_cli,
        "resolve_latest_available_date",
        lambda _: _AS_OF,
    )
    unsafe_dir = Path(f"{tmp_path}/safe/../escape")

    exit_code = main(_args(unsafe_dir, "--as-of", _AS_OF.isoformat()))

    rendered = capsys.readouterr().err
    assert exit_code == 1
    assert state.created == 0
    assert not (tmp_path / "escape" / "universe_manifest.json").exists()
    assert "output_path_invalid" in rendered
    assert "physical_attempts=0" in rendered


def test_atomic_report_failure_removes_temporary_file_and_publishes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _ClientState()
    _install_client(monkeypatch, state)
    monkeypatch.setattr(
        universe_refresh_cli,
        "resolve_latest_available_date",
        lambda _: _AS_OF,
    )
    monkeypatch.setattr(
        "app.data.kis.universe.os.replace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("synthetic write failure")),
    )
    data_dir = tmp_path / "data" / "kis"

    exit_code = main(_args(data_dir, "--as-of", _AS_OF.isoformat()))

    rendered = capsys.readouterr().err
    assert exit_code == 1
    assert state.closed == 1
    assert not (data_dir / "universe_manifest.json").exists()
    assert not (data_dir / "reports" / "universe_refresh.md").exists()
    assert list(data_dir.rglob("*.tmp")) == []
    assert "output_path_invalid" in rendered
    assert "physical_attempts=2" in rendered
