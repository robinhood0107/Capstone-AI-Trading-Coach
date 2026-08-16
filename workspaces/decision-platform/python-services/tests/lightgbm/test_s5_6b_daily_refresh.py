from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import NoReturn, cast

import numpy as np
import pytest

from app.data.ecos.models import ECOSObservation
from app.data.ecos.series_registry import CANDIDATE_SERIES, ECOSSeries
from app.data.kis.parsers import DailyBar
from app.lightgbm import daily_refresh
from app.lightgbm.calibration import fit_ovr_platt
from app.lightgbm.daily_refresh import (
    DailyInferenceState,
    DailyKrxProjection,
    _daily_krx_operations,
    _write_state,
    author_daily_refresh_packet,
    execute_daily_refresh,
    read_daily_state,
    validate_daily_refresh_packet,
)
from app.lightgbm.errors import DatasetUnavailable, LightGbmContractError
from app.lightgbm.features import IndexEvidence, MacroObservation, ProductionPriceEvidence
from app.lightgbm.feature_artifact import ProductionFeatureBundle
from app.lightgbm.pit_calendar import _calendar, derive_monthly_universe_schedule
from app.lightgbm.temporal import (
    AvailabilityBasis,
    RevisionBasis,
    TemporalQuality,
    TemporalReceipt,
    next_session_evidence_clock,
    next_xkrx_evidence_clock,
)
from app.lightgbm.production_release import QualifiedProductionRelease
from app.lightgbm.training import exact_grid, fit_lightgbm_reproducible, raw_margins
from app.lightgbm.universe import MonthlyUniverse


def _private(path: Path) -> Path:
    path.mkdir(mode=0o700)
    return path


def _receipt(source: str, operation: str, day: date) -> TemporalReceipt:
    is_krx = source == "KRX"
    return TemporalReceipt(
        source_id=source,
        operation_id=operation,
        observation_date=day,
        retrieved_at=datetime(2026, 8, 16, 8, 0, tzinfo=UTC),
        availability_basis=(
            AvailabilityBasis.PROVIDER_AS_OF_SCHEDULE
            if is_krx
            else AvailabilityBasis.PROJECT_FIXED_LAG
        ),
        revision_basis=RevisionBasis.CONTENT_SNAPSHOT,
        request_sha256="1" * 64,
        snapshot_sha256=("2" if is_krx else "3") * 64,
        temporal_quality=(
            TemporalQuality.PROVIDER_AS_OF_NO_VINTAGE
            if is_krx
            else TemporalQuality.RECONSTRUCTED_FIXED_LAG
        ),
        policy_effective_at=next_session_evidence_clock(day),
    )


def _state(root: Path) -> DailyInferenceState:
    calendar = _calendar()
    last = calendar.date_to_session("2026-08-14", direction="none")
    first = calendar.session_offset(last, -59)
    sessions = tuple(value.date() for value in calendar.sessions_in_range(first, last))
    symbols = tuple(sorted([f"{value:06d}" for value in range(1, 30)] + ["005930", "132030"]))
    identities = tuple(
        "XKRX:ETF:132030" if symbol == "132030" else f"KR{symbol}0000"
        for symbol in symbols
    )
    schedule = derive_monthly_universe_schedule(
        "2026-08",
        dataset_cutoff=datetime(2026, 8, 17, 8, 10, tzinfo=next_xkrx_evidence_clock(sessions[-1]).tzinfo),
    )
    universe = MonthlyUniverse(
        selection_session=schedule.selection_session,
        effective_month="2026-08",
        instrument_ids=identities,
        symbols=symbols,
    )
    prices = tuple(
        ProductionPriceEvidence(
            instrument_id=identity,
            symbol=symbol,
            session_date=session,
            adjusted_open=100.0,
            adjusted_close=101.0,
            volume=1000.0,
            flng_cls_code="00",
            prtt_rate=0.0,
            mod_yn="N",
            revl_issu_reas="",
            receipt=_receipt("KIS", "FHKST03010100", session),
        )
        for identity, symbol in zip(identities, symbols, strict=True)
        for session in sessions
    )
    indices = tuple(
        IndexEvidence(
            session_date=session,
            market=market,
            adjusted_close=2000.0,
            receipt=_receipt("KRX", service, session),
        )
        for session in sessions
        for market, service in (("KOSPI", "kospi_dd_trd"), ("KOSDAQ", "kosdaq_dd_trd"))
    )
    macro = (
        MacroObservation(
            series_id="policy-rate",
            observation_date=sessions[0],
            value=2.5,
            receipt=_receipt("ECOS", "722Y001/0101000/D", sessions[0]),
        ),
        *(
            MacroObservation(
                series_id="krw-usd-rate",
                observation_date=session,
                value=1300.0,
                receipt=_receipt("ECOS", "731Y001/0000001/D", session),
            )
            for session in sessions
        ),
    )
    history_sessions = sessions[-20:]
    history = tuple(
        DailyKrxProjection(
            session_date=session,
            service=service,
            rows=({"BAS_DD": session.strftime("%Y%m%d"), "ISU_CD": "005930"},),
            receipt=_receipt("KRX", service, session),
        )
        for session in history_sessions
        for service in ("stk_bydd_trd", "ksq_bydd_trd")
    )
    return _write_state(
        state_root=root,
        bootstrap_packet_sha256="4" * 64,
        source_manifest_sha256="5" * 64,
        feature_manifest_sha256="6" * 64,
        release_manifest_sha256="7" * 64,
        previous_state_sha256=None,
        session_date=sessions[-1],
        as_of=next_xkrx_evidence_clock(sessions[-1]),
        universe=universe,
        listing_markets={identity: "KOSPI" for identity in identities},
        prices=prices,
        indices=indices,
        macro=macro,
        krx_history=history,
    )


def test_daily_state_and_packet_are_digest_anchored_and_single_session(tmp_path: Path) -> None:
    root = _private(tmp_path / "daily")
    state = _state(root)
    assert read_daily_state(state_root=root, expected_sha256=state.sha256) == state
    calendar = _calendar()
    next_session = calendar.next_session(
        calendar.date_to_session(state.session_date.isoformat(), direction="none")
    ).date()
    assert next_session == date(2026, 8, 18)
    cutoff = next_xkrx_evidence_clock(next_session)
    packet = author_daily_refresh_packet(state=state, cutoff=cutoff)
    assert packet.session_date == next_session
    assert (
        validate_daily_refresh_packet(
            packet.content,
            expected_sha256=packet.sha256,
            state=state,
        )
        == packet
    )
    with pytest.raises(DatasetUnavailable, match="exactly one session"):
        author_daily_refresh_packet(
            state=state,
            cutoff=next_xkrx_evidence_clock(
                calendar.next_session(calendar.date_to_session(next_session, direction="none")).date()
            ),
        )
    # 놓친 날은 최신 session으로 건너뛰지 않고 바로 다음 XKRX session packet만 명시적으로 복구한다.
    resume = author_daily_refresh_packet(
        state=state,
        cutoff=next_xkrx_evidence_clock(
            calendar.next_session(calendar.date_to_session(next_session, direction="none")).date()
        ),
        requested_session=next_session,
    )
    assert resume.session_date == date(2026, 8, 18)
    assert _daily_krx_operations(state=state, packet=resume) == (
        "stk_bydd_trd",
        "ksq_bydd_trd",
        "kospi_dd_trd",
        "kosdaq_dd_trd",
    )


def test_daily_state_content_addressed_replay_is_idempotent(tmp_path: Path) -> None:
    root = _private(tmp_path / "daily")
    first = _state(root)
    second = _state(root)
    assert second == first


def test_daily_state_rejects_digest_mutation_and_symlink(tmp_path: Path) -> None:
    root = _private(tmp_path / "daily")
    state = _state(root)
    target = root / f"state-{state.sha256}.json"
    target.write_bytes(b"{}\n")
    target.chmod(0o600)
    with pytest.raises(LightGbmContractError, match="trust anchor"):
        read_daily_state(state_root=root, expected_sha256=state.sha256)

    other = _private(tmp_path / "other")
    link = other / f"state-{state.sha256}.json"
    link.symlink_to(target)
    with pytest.raises(LightGbmContractError):
        read_daily_state(state_root=other, expected_sha256=state.sha256)


class _FailingKrx:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, *, service: str, session_date: date) -> tuple[dict[str, str], ...]:
        del service, session_date
        self.calls += 1
        if self.calls == 3:
            raise RuntimeError("masked provider failure")
        return ({"BAS_DD": "20260817", "ISU_CD": "005930"},)


class _NoKis:
    calls = 0

    def prepare_access_token(self) -> NoReturn:
        self.calls += 1
        raise AssertionError("KIS must not run after KRX failure")

    def require_cached_token_only(self) -> None:
        raise AssertionError("KIS must not run")

    def fetch_page(self, *, symbol: str, start: date, end: date) -> tuple[object, ...]:
        del symbol, start, end
        raise AssertionError("KIS must not run")


class _NoEcos:
    calls = 0

    def fetch(self, *, series: object, start: date, end: date) -> tuple[object, ...]:
        del series, start, end
        self.calls += 1
        raise AssertionError("ECOS must not run")


def test_daily_refresh_stops_all_remaining_providers_after_first_failure(tmp_path: Path) -> None:
    state_root = _private(tmp_path / "daily")
    state = _state(state_root)
    next_session = _calendar().next_session(
        _calendar().date_to_session(state.session_date.isoformat(), direction="none")
    ).date()
    packet = author_daily_refresh_packet(
        state=state,
        cutoff=next_xkrx_evidence_clock(next_session),
    )
    krx = _FailingKrx()
    kis = _NoKis()
    ecos = _NoEcos()
    with pytest.raises(RuntimeError, match="masked provider failure"):
        execute_daily_refresh(
            packet=packet,
            state=state,
            state_root=state_root,
            run_root=_private(tmp_path / "run"),
            feature_root=_private(tmp_path / "feature"),
            release_root=_private(tmp_path / "release"),
            krx=krx,
            kis=kis,  # type: ignore[arg-type]
            ecos=ecos,  # type: ignore[arg-type]
            ecos_series=(),
        )
    assert krx.calls == 3
    assert kis.calls == 0
    assert ecos.calls == 0


class _SuccessfulKrx:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def fetch(self, *, service: str, session_date: date) -> tuple[dict[str, str], ...]:
        self.calls.append(service)
        if service == "kospi_dd_trd":
            return ({"BAS_DD": session_date.strftime("%Y%m%d"), "IDX_NM": "KOSPI", "CLSPRC_IDX": "2100"},)
        if service == "kosdaq_dd_trd":
            return ({"BAS_DD": session_date.strftime("%Y%m%d"), "IDX_NM": "KOSDAQ", "CLSPRC_IDX": "900"},)
        return ({"BAS_DD": session_date.strftime("%Y%m%d"), "ISU_CD": "005930"},)


class _SuccessfulKis:
    def __init__(self, target: date) -> None:
        self.target = target
        self.prepared = 0
        self.frozen = 0
        self.calls = 0

    def prepare_access_token(self) -> None:
        self.prepared += 1

    def require_cached_token_only(self) -> None:
        self.frozen += 1

    def fetch_page(self, *, symbol: str, start: date, end: date) -> tuple[DailyBar, ...]:
        del start
        assert end == self.target
        self.calls += 1
        calendar = _calendar()
        last = calendar.date_to_session(self.target.isoformat(), direction="none")
        first = calendar.session_offset(last, -59)
        sessions = tuple(value.date() for value in calendar.sessions_in_range(first, last))
        return tuple(
            DailyBar(
                symbol=symbol,
                date=session,
                open=100,
                high=102,
                low=99,
                close=101,
                volume=1000,
                flng_cls_code="00",
                prtt_rate=Decimal("0"),
                mod_yn="N",
                revl_issu_reas="",
            )
            for session in sessions
        )


class _SuccessfulEcos:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(
        self, *, series: ECOSSeries, start: date, end: date
    ) -> tuple[ECOSObservation, ...]:
        assert start == end
        self.calls += 1
        if series.series_id == "policy-rate":
            return ()
        return (ECOSObservation(time=start.strftime("%Y%m%d"), value="1300"),)


def _actual_release() -> QualifiedProductionRelease:
    random = np.random.default_rng(20260729)
    x_fit = random.normal(size=(300, 17)).astype(np.float32)
    y_fit = np.tile(np.asarray([0, 1, 2]), 100)
    x_early = random.normal(size=(90, 17)).astype(np.float32)
    y_early = np.tile(np.asarray([0, 1, 2]), 30)
    model = fit_lightgbm_reproducible(x_fit, y_fit, x_early, y_early, exact_grid()[0])
    calibrator = fit_ovr_platt(raw_margins(model, x_early), y_early)
    return QualifiedProductionRelease(
        model_release_id="lgr-111111111111",
        model_version=f"lgbm-v1-{model.model_sha256[:12]}",
        model_report_id="mrp-222222222222",
        release_manifest_sha256="7" * 64,
        release_manifest_bytes=b"{}\n",
        feature_bundle=cast(ProductionFeatureBundle, object()),
        model=model,
        calibrator=calibrator,
    )


def test_daily_refresh_builds_real_lightgbm_exact_31_batch_with_bounded_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = _private(tmp_path / "daily")
    state = _state(state_root)
    target = _calendar().next_session(
        _calendar().date_to_session(state.session_date.isoformat(), direction="none")
    ).date()
    packet = author_daily_refresh_packet(
        state=state,
        cutoff=next_xkrx_evidence_clock(target),
    )
    release = _actual_release()
    monkeypatch.setattr(daily_refresh, "load_qualified_production_release", lambda **_: release)
    krx = _SuccessfulKrx()
    kis = _SuccessfulKis(target)
    ecos = _SuccessfulEcos()
    result = execute_daily_refresh(
        packet=packet,
        state=state,
        state_root=state_root,
        run_root=_private(tmp_path / "run"),
        feature_root=_private(tmp_path / "feature"),
        release_root=_private(tmp_path / "release"),
        krx=krx,
        kis=kis,
        ecos=ecos,
        ecos_series=CANDIDATE_SERIES,
    )
    assert tuple(krx.calls) == (
        "stk_bydd_trd",
        "ksq_bydd_trd",
        "kospi_dd_trd",
        "kosdaq_dd_trd",
    )
    assert kis.prepared == 1 and kis.frozen == 1 and kis.calls == 31
    assert ecos.calls == 2
    assert result.budgeted_calls == 38
    assert result.batch.manifest["rowCount"] == 31
    assert result.batch.manifest["sessionDate"] == target.isoformat()
    assert result.state.previous_state_sha256 == state.sha256
