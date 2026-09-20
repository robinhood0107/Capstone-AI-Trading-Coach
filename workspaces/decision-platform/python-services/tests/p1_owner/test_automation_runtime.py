from __future__ import annotations

import json

from datetime import date, datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
import exchange_calendars as xcals
import pandas as pd

from app.p1_owner.automation import (
    AutomationInputs,
    ReconcileSnapshot,
    FixtureAutomationTransport,
    OrderReservation,
    Quote,
)
from app.p1_owner.automation_runtime import (
    AccountLineageAdvance,
    AdvanceCommand,
    AiJudgementRecord,
    AutomationRuntimeError,
    AutomationRuntimeService,
    PersistentAutomationRunner,
    PostgresAutomationRuntimeRepository,
    RuntimeClaim,
    XkrxBoundaryPlanner,
    _DECISION_TIMES,
    _required_atr_periods,
    inputs_from_state,
)

_KST = ZoneInfo("Asia/Seoul")


@pytest.mark.parametrize("minute", [49, 50])
def test_preparation_is_due_before_order_opening(minute: int) -> None:
    planner = XkrxBoundaryPlanner()
    now = datetime(2026, 9, 8, 8, minute, tzinfo=_KST)
    assert planner.preparation_wakeup(now) == now
    assert planner.next_wakeup(now) == datetime(2026, 9, 8, 9, 30, tzinfo=_KST)
    holiday = datetime(2026, 8, 14, 16, 0, tzinfo=_KST)
    assert planner.preparation_wakeup(holiday) == datetime(2026, 8, 18, 8, 30, tzinfo=_KST)


@pytest.mark.parametrize("outcome", ["IMPORTED", "REPLAYED", "MODEL_OR_MARKET_DATA_UNAVAILABLE"])
def test_morning_preparation_never_claims_or_reports_missing_input_as_ready(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], outcome: str
) -> None:
    from app.p1_owner import automation_runtime as runtime
    from app.p1_owner.daily_inference import DailyInferenceResult

    now = datetime(2026, 9, 8, 8, 49, tzinfo=_KST)
    calls: list[date] = []
    report_calls: list[str] = []
    boundaries: list[datetime] = []

    class Clock(datetime):
        @staticmethod
        def now(tz: Any) -> datetime:
            return now.astimezone(tz)

    class Repository:
        def preflight(self) -> None:
            pass

        def claim(self, *args: Any) -> None:
            pytest.fail("장전 preparation에는 주문 claim 권한이 없다")

    class Daily:
        def ensure_daily_signals(self, session: date) -> DailyInferenceResult:
            calls.append(session)
            return DailyInferenceResult(outcome, session)

        def close(self) -> None:
            pass

    class Stop:
        def is_set(self) -> bool:
            return False

        def wait(self, seconds: float) -> bool:
            assert seconds == 60.0
            return True

    service = AutomationRuntimeService(
        cast(Any, Repository()),
        cast(Any, None),
        "x" * 32,
        daily_inference=cast(Any, Daily()),
        performance_report_refresh=lambda: (
            report_calls.append("refresh") or {"performanceReport": "NO_OP"}
        ),
    )
    service._stop = cast(Any, Stop())
    monkeypatch.setattr(runtime, "datetime", Clock)

    def wait_until(boundary: datetime) -> bool:
        boundaries.append(boundary)
        return boundary > now

    monkeypatch.setattr(service, "_wait_until", wait_until)
    service.serve()
    assert calls == [now.date()]
    output = capsys.readouterr().out
    if outcome == "MODEL_OR_MARKET_DATA_UNAVAILABLE":
        assert "UNAVAILABLE" in output
        assert "ON_TIME" not in output
        assert report_calls == []
    else:
        assert "AUTOMATION_PREPARATION_ATTEMPT=ON_TIME" in output
        assert f"outcome={outcome}" in output
        assert boundaries[-1].time().isoformat() == "09:30:00"
        assert report_calls == ["refresh"]


def test_wall_clock_wait_rechecks_after_suspend(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.p1_owner import automation_runtime as runtime

    current = datetime(2026, 9, 7, 8, 50, tzinfo=_KST)

    class Clock(datetime):
        @staticmethod
        def now(tz: Any) -> datetime:
            return current.astimezone(tz)

    class Stop:
        waits: list[float] = []

        def is_set(self) -> bool:
            return False

        def wait(self, seconds: float) -> bool:
            nonlocal current
            self.waits.append(seconds)
            # The laptop wakes after the entire scheduled opening has passed.
            current = datetime(2026, 9, 7, 10, 20, tzinfo=_KST)
            return False

    service = AutomationRuntimeService(cast(Any, None), cast(Any, None), "x" * 32)
    stop = Stop()
    service._stop = cast(Any, stop)
    monkeypatch.setattr(runtime, "datetime", Clock)
    assert not service._wait_until(datetime(2026, 9, 7, 9, 30, tzinfo=_KST))
    assert stop.waits == [30.0]


def test_connectivity_failure_does_not_consume_a_session(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.p1_owner import automation_runtime as runtime

    class Clock(datetime):
        @staticmethod
        def now(tz: Any) -> datetime:
            return datetime(2026, 9, 7, 10, 0, tzinfo=_KST).astimezone(tz)

    class Repository:
        def preflight(self) -> None:
            pass

        def claim(self, *args: Any) -> None:
            pytest.fail("Unavailable network must not consume a daily claim")

    class Daily:
        closed = False

        def ensure_daily_signals(self, session: date) -> None:
            pytest.fail("Connectivity must be checked before materialization")

        def close(self) -> None:
            self.closed = True

    class Stop:
        def is_set(self) -> bool:
            return False

        def wait(self, seconds: float) -> bool:
            assert seconds == 60.0
            return True

    daily = Daily()
    service = AutomationRuntimeService(
        cast(Any, Repository()),
        cast(Any, None),
        "x" * 32,
        daily_inference=cast(Any, daily),
        connectivity_check=lambda: False,
    )
    service._stop = cast(Any, Stop())
    monkeypatch.setattr(runtime, "datetime", Clock)
    service.serve()
    assert daily.closed


def _claim() -> RuntimeClaim:
    return RuntimeClaim(
        user_id="usr_automation_runtime_0001",
        run_id="auto_run_" + "a" * 32,
        control_version=2,
        account_id="acct_" + "b" * 32,
        principle_id="prc_automation_runtime_0001",
        strategy_id="strategy_automation_runtime_0001",
        baseline_account_digest="c" * 64,
        replayed=False,
        session_date=date(2026, 8, 28),
        claim_token_hash="sha256:" + "d" * 64,
    )


def _state(state: str = "SCHEDULED", version: int = 1) -> dict[str, Any]:
    return {
        "accountComplete": True,
        "accountDigestMatches": True,
        "accountId": "acct_" + "b" * 32,
        "baselineAccountDigest": "c" * 64,
        "brokerageMode": "KIS_MOCK",
        "checkpointVersion": version,
        "controlState": "ARMED",
        "controlVersion": 2,
        "dailyShardFreshComplete": True,
        "killSwitchActive": False,
        "manualPositionSymbols": [],
        "noOpenOrder": True,
        "positions": [],
        "policy": {
            "capitalLimitKrw": 10_000_000,
            "maxOpenPositions": 5,
            "policyId": "auto_pol_" + "f" * 32,
            "preset": "BALANCED",
            "stopLossBps": 500,
            "takeProfitBps": 1_000,
            "version": 1,
        },
        "principleId": "prc_automation_runtime_0001",
        "principleActiveCurrent": True,
        "providerCallCount": 0,
        "logicalSubmitCount": 0,
        "releaseActive": True,
        "reservation": None,
        "runId": "auto_run_" + "a" * 32,
        "runStartedAt": "2026-08-28T09:30:00+09:00",
        "selectedSide": None,
        "selectedSymbol": None,
        "sessionDate": "2026-08-28",
        "signals": [
            {
                "symbol": "005930",
                "lstmSignal": "BUY",
                "baselineSignal": "BUY",
                "expectedReturn": 0.03,
            }
        ],
        "state": state,
        "strategyId": "strategy_automation_runtime_0001",
        "unfinishedPreviousOrder": False,
        "vertexCallCount": 0,
    }


def _v3_state(state: str = "SCHEDULED", version: int = 1) -> dict[str, Any]:
    value = _state(state, version)
    value["policy"].update(
        {
            "atrMultiplierMilli": 3_000,
            "atrPeriod": 22,
            "maxHoldingSessions": 60,
            "modelSellEnabled": True,
        }
    )
    value["aiJudgementEnabled"] = True
    value["aiJudgementProviderBound"] = True
    value["aiSettingsSha256"] = "e" * 64
    value["thinkingLevel"] = "low"
    calendar = xcals.get_calendar("XKRX")
    previous = calendar.previous_session(pd.Timestamp(value["sessionDate"]))
    sessions = tuple(item.date() for item in calendar.sessions_window(previous, -23))
    value["atrHistories"] = {
        "005930": [
            {
                "sessionDate": session.isoformat(),
                "openPriceKrw": 70_000,
                "highPriceKrw": 71_000,
                "lowPriceKrw": 69_000,
                "closePriceKrw": 70_000,
            }
            for session in sessions
        ]
    }
    return value


class FakeRepository:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state
        self.commands: list[AdvanceCommand] = []
        self.ai_judgements: list[AiJudgementRecord] = []

    def record_ai_judgement(self, claim: RuntimeClaim, record: AiJudgementRecord) -> None:
        assert claim.run_id == self.state["runId"]
        self.ai_judgements.append(record)

    def read_state(self, claim: RuntimeClaim) -> dict[str, Any]:
        assert claim.run_id == self.state["runId"]
        return dict(self.state)

    def advance(self, command: AdvanceCommand) -> tuple[int, bool]:
        self.commands.append(command)
        self.state["state"] = command.next_state
        self.state["checkpointVersion"] = command.expected_version + 1
        self.state["selectedSymbol"] = command.selected_symbol
        self.state["selectedSide"] = command.selected_side
        self.state["vertexCallCount"] = command.vertex_call_count
        self.state["providerCallCount"] = command.provider_call_count
        self.state["logicalSubmitCount"] = command.logical_submit_count
        return command.expected_version + 1, False

    def advance_with_ai_judgement(
        self,
        command: AdvanceCommand,
        claim: RuntimeClaim,
        record: AiJudgementRecord,
    ) -> tuple[int, bool]:
        assert claim.run_id == self.state["runId"] == command.run_id
        result = self.advance(command)
        self.ai_judgements.append(record)
        return result


class FakeRuntimePort(FixtureAutomationTransport):
    order_id: str | None = None
    provider_order_ref_hash: str | None = None
    decision_id: str | None = None
    last_judgement_json: str | None = None

    def inputs(
        self,
        *,
        state: dict[str, Any],
        run: object,
        now: datetime,
    ) -> AutomationInputs:
        del run, now
        return inputs_from_state(state, risk_allow=True, buyable_quantity=1)

    def close(self) -> None:
        return None


def test_persistent_runner_reloads_state_and_cas_persists_each_boundary() -> None:
    repository = FakeRepository(_state())
    port = FakeRuntimePort(quotes={"005930": Quote("005930", 75_000, 52_500, 97_500)})
    first = PersistentAutomationRunner(cast(Any, repository)).run_tick(
        claim=_claim(),
        tick_id="boundary-001",
        now=datetime(2026, 8, 28, 9, 30, tzinfo=_KST),
        port=port,
    )
    second = PersistentAutomationRunner(cast(Any, repository)).run_tick(
        claim=_claim(),
        tick_id="boundary-002",
        now=datetime(2026, 8, 28, 9, 30, 1, tzinfo=_KST),
        port=port,
    )

    assert first["state"] == "PRECHECK"
    # 후보 선정 앞에 AI_JUDGING이 선다. 그 경계도 CAS로 저장돼야 재시작이 안전하다.
    assert second["state"] == "AI_JUDGING"
    assert [item.expected_version for item in repository.commands] == [1, 2]
    assert all(item.tick_identity_hash.startswith("sha256:") for item in repository.commands)
    assert all(item.result_hash.startswith("sha256:") for item in repository.commands)
    assert port.physical_calls == port.physical_submit_calls == 0


def test_inputs_from_state_preserve_rule_lstm_and_fail_closed_flags() -> None:
    state = _state()
    state["killSwitchActive"] = True
    state["accountDigestMatches"] = False
    inputs = inputs_from_state(state, risk_allow=False, buyable_quantity=0)

    assert inputs.signals[0].lstm_signal == inputs.signals[0].baseline_signal == "BUY"
    assert inputs.kill_switch_active is True
    assert inputs.account_digest_matches is False
    assert inputs.risk_allow is False
    assert inputs.buyable_quantity == 0


def test_atr_reader_uses_the_largest_immutable_position_snapshot_period() -> None:
    state = _state()
    state["positions"] = [
        {"symbol": "005930", "atrPeriod": 100},
        {"symbol": "000660", "atrPeriod": 22},
    ]

    assert _required_atr_periods(state, policy_period=5) == {
        "000660": 22,
        "005930": 100,
    }


def test_xkrx_boundary_skips_substitute_holiday_and_uses_exact_times() -> None:
    planner = XkrxBoundaryPlanner()

    after_close = datetime(2026, 8, 14, 15, 21, tzinfo=_KST)
    assert planner.current_or_next_session(after_close) == date(2026, 8, 18)
    assert planner.next_wakeup(after_close) == datetime(2026, 8, 18, 9, 30, tzinfo=_KST)
    pending = datetime(2026, 8, 18, 10, 0, tzinfo=_KST)
    assert planner.next_wakeup(pending, "PENDING_RECONCILIATION") == datetime(
        2026, 8, 18, 15, 20, tzinfo=_KST
    )


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://decision_worker:secret@postgres:5432/capstone_p1",
        "postgresql://decision_automation_runtime:secret@example.com:5432/capstone_p1",
        "",
    ],
)
def test_repository_rejects_non_runtime_or_non_internal_dsn(dsn: str) -> None:
    with pytest.raises(AutomationRuntimeError):
        PostgresAutomationRuntimeRepository(dsn)


class LineageRecordingRepository(FakeRepository):
    def __init__(self, state: dict[str, Any]) -> None:
        super().__init__(state)
        self.lineage: list[AccountLineageAdvance] = []

    def advance_with_lineage(
        self, command: AdvanceCommand, claim: RuntimeClaim, lineage: AccountLineageAdvance
    ) -> tuple[int, bool]:
        result = self.advance(command)
        self.advance_account_lineage(claim, lineage)
        return result

    def advance_account_lineage(self, claim: RuntimeClaim, lineage: AccountLineageAdvance) -> int:
        assert claim.run_id == self.state["runId"]
        self.lineage.append(lineage)
        return len(self.lineage)

    def advance(self, command: AdvanceCommand) -> tuple[int, bool]:
        result = super().advance(command)
        # 실제 checkpoint는 예약을 durable하게 남긴다. 그래야 다음 tick이 같은 예약으로
        # 제출·대사를 이어갈 수 있다.
        if command.reservation_id is not None and command.exact_intent_json is not None:
            self.state["reservation"] = {
                "exactIntent": json.loads(command.exact_intent_json),
                "limitPriceKrw": command.limit_price_krw,
                "orderId": "ord_mock_" + "e" * 32,
                "quantity": command.quantity,
                "side": command.selected_side,
                "symbol": command.selected_symbol,
            }
        self.state["filledQuantity"] = command.filled_quantity
        self.state["leavesQuantity"] = command.leaves_quantity
        self.state["averageFillPriceKrw"] = command.average_fill_price_krw
        if command.exit_reason is not None:
            self.state["exitReason"] = command.exit_reason
        self._apply_position_effects(command)
        return result

    def _apply_position_effects(self, command: AdvanceCommand) -> None:
        """실제 checkpoint SP가 포지션에 하는 일을 그대로 흉내낸다.

        이게 없으면 다음 tick이 EXIT_PENDING 표시를 못 보고 SELL_POSITION_DRIFT로 HALT한다.
        """

        positions = self.state["positions"]
        if command.selected_side != "SELL" or not isinstance(positions, list):
            return
        for item in positions:
            if item.get("symbol") != command.selected_symbol:
                continue
            if command.next_state == "EXIT_SELECTED":
                item["status"] = "EXIT_PENDING"
                item["exitReason"] = command.exit_reason
            elif command.next_state == "COMPLETED" and command.filled_quantity:
                remaining = int(item["quantity"]) - command.filled_quantity
                item["quantity"] = max(0, remaining)
                item["status"] = "CLOSED" if remaining <= 0 else "OPEN"
                item["entryNotionalKrw"] = max(0, remaining) * int(item["entryAverageFillPriceKrw"])


class LineageRuntimePort(FakeRuntimePort):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.order_id = "ord_mock_" + "e" * 32
        self.lineage_requests: list[tuple[str, str, int, int]] = []

    def submit(self, reservation: OrderReservation) -> Any:
        # 실제 port는 물리 제출을 세므로 tick 뒤 단언이 같은 값을 본다.
        self.physical_submit_calls += 1
        return super().submit(reservation)

    def account_lineage_advance(
        self,
        *,
        symbol: str,
        side: str,
        filled_quantity: int,
        average_fill_price_krw: int,
    ) -> AccountLineageAdvance | None:
        self.lineage_requests.append((symbol, side, filled_quantity, average_fill_price_krw))
        return AccountLineageAdvance(
            reason="BUY_FILL" if side == "BUY" else "SELL_FILL",
            projection={
                "accountId": "acct_" + "b" * 32,
                "cashKrw": 100,
                "positions": [{"quantity": filled_quantity, "symbol": symbol}],
                "schemaVersion": "2",
            },
            digest="a" * 64,
            order_id=str(self.order_id),
            filled_quantity=filled_quantity,
            average_fill_price_krw=average_fill_price_krw,
        )


def test_a_confirmed_fill_advances_the_account_lineage_in_the_same_tick() -> None:
    # 체결 tick에서 기대 계좌 투영을 함께 밀지 않으면 다음 세션이 자기 체결을 외부
    # 드리프트로 보고 HALT하고, HALT는 stop으로 풀리지 않는다.
    repository = LineageRecordingRepository(_state())
    port = LineageRuntimePort(quotes={"005930": Quote("005930", 75_000, 52_500, 97_500)})
    runner = PersistentAutomationRunner(cast(Any, repository))
    now = datetime(2026, 8, 28, 9, 30, tzinfo=_KST)
    projection: dict[str, object] = {}
    for index in range(1, 20):
        projection = runner.run_tick(
            claim=_claim(), tick_id=f"boundary-{index:03d}", now=now, port=port
        )
        if projection["state"] in {"COMPLETED", "HALTED", "SKIPPED_NO_ACTION"}:
            break

    assert projection["state"] == "COMPLETED"
    assert len(repository.lineage) == 1
    assert repository.lineage[0].reason == "BUY_FILL"
    assert port.lineage_requests == [("005930", "BUY", 1, 75_100)]


def test_lineage_failure_does_not_commit_a_completed_run() -> None:
    class UnavailableLineagePort(LineageRuntimePort):
        def account_lineage_advance(self, **kwargs: Any) -> AccountLineageAdvance | None:
            raise RuntimeError("balance temporarily unavailable")

    repository = LineageRecordingRepository(_state())
    port = UnavailableLineagePort(quotes={"005930": Quote("005930", 75_000, 52_500, 97_500)})
    runner = PersistentAutomationRunner(cast(Any, repository))
    with pytest.raises(RuntimeError, match="balance temporarily unavailable"):
        for index in range(1, 20):
            runner.run_tick(
                claim=_claim(),
                tick_id=f"failure-{index}",
                now=datetime(2026, 8, 28, 9, 30, tzinfo=_KST),
                port=port,
            )
    assert repository.state["state"] in {"ORDER_SUBMITTED", "PENDING_RECONCILIATION"}
    assert repository.lineage == []


def _held_position(**overrides: Any) -> dict[str, Any]:
    position: dict[str, Any] = {
        "positionId": "auto_pos_" + "b" * 32,
        "accountId": "acct_" + "b" * 32,
        "symbol": "005930",
        "entrySession": "2026-08-24",
        "expirySession": "2026-09-04",
        "createdAt": "2026-08-24T09:31:00+09:00",
        "closedAt": None,
        "status": "OPEN",
        "quantity": 1,
        "entryAverageFillPriceKrw": 100_000,
        "entryNotionalKrw": 100_000,
        "policyId": "auto_pol_" + "f" * 32,
        "policyVersion": 1,
        "stopLossBps": 500,
        "takeProfitBps": 1_000,
        "exitReason": None,
    }
    position.update(overrides)
    # 진입 약정금액은 수량 x 단가와 정확히 같아야 한다. override를 줘도 어긋나지 않게 다시 맞춘다.
    position["entryNotionalKrw"] = int(position["quantity"]) * int(
        position["entryAverageFillPriceKrw"]
    )
    return position


def _exit_state(quote_price: int, **position_overrides: Any) -> dict[str, Any]:
    state = _state(state="PRECHECK")
    state["positions"] = [_held_position(**position_overrides)]
    state["signals"] = []
    del quote_price
    return state


def _drive_runner(
    repository: FakeRepository, port: FakeRuntimePort, limit: int = 20
) -> dict[str, object]:
    runner = PersistentAutomationRunner(cast(Any, repository))
    now = datetime(2026, 8, 28, 9, 30, tzinfo=_KST)
    projection: dict[str, object] = {}
    for index in range(1, limit):
        projection = runner.run_tick(
            claim=_claim(), tick_id=f"exit-{index:03d}", now=now, port=port
        )
        if projection["state"] in {"COMPLETED", "HALTED", "SKIPPED_NO_ACTION"}:
            break
    return projection


def test_stop_loss_exit_survives_the_durable_runner_path() -> None:
    # 지금까지 청산 사유는 순수 엔진에서만 확인됐다. repository와 checkpoint를 태워
    # production 코드 경로에서도 같은 결론이 나오는지 본다.
    repository = LineageRecordingRepository(_exit_state(0))
    # 진입 100,000에서 매도 지정가가 94,900이면 왕복비용 포함 -545bp라 500bp 손절선을 넘는다.
    port = LineageRuntimePort(quotes={"005930": Quote("005930", 95_000, 70_000, 130_000)})
    projection = _drive_runner(repository, port)

    assert projection["state"] == "COMPLETED"
    assert projection["selectedSide"] == "SELL"
    # durable하게 남는 것은 checkpoint 명령이다. 사유가 거기 실려야 다음 tick과 조회가 안다.
    assert {command.exit_reason for command in repository.commands if command.exit_reason} == {
        "STOP_LOSS"
    }


def test_take_profit_exit_survives_the_durable_runner_path() -> None:
    repository = LineageRecordingRepository(_exit_state(0))
    # 매도 지정가 111,000이면 왕복비용 차감 후 +1065bp로 1000bp 익절선을 넘는다.
    port = LineageRuntimePort(quotes={"005930": Quote("005930", 111_500, 70_000, 130_000)})
    projection = _drive_runner(repository, port)

    assert projection["state"] == "COMPLETED"
    assert {command.exit_reason for command in repository.commands if command.exit_reason} == {
        "TAKE_PROFIT"
    }


def test_partial_fill_then_cancel_applies_only_the_confirmed_quantity() -> None:
    repository = LineageRecordingRepository(_exit_state(0, quantity=3))
    port = LineageRuntimePort(
        quotes={"005930": Quote("005930", 95_000, 70_000, 130_000)},
        reconcile_snapshots=[
            ReconcileSnapshot(
                resolved=True,
                cumulative_quantity=1,
                leaves_quantity=0,
                average_fill_price_krw=94_900,
                cancelled=True,
            )
        ],
    )
    projection = _drive_runner(repository, port)

    assert projection["state"] == "COMPLETED"
    assert projection["filledQuantity"] == 1
    # 취소로 끝난 잔량은 체결로 세지 않는다.
    assert projection["leavesQuantity"] == 0


def test_the_ai_judgement_is_recorded_on_the_tick_that_leaves_that_state() -> None:
    repository = FakeRepository(_state())
    port = FakeRuntimePort(quotes={"005930": Quote("005930", 75_000, 52_500, 97_500)})
    for index in range(1, 4):
        PersistentAutomationRunner(cast(Any, repository)).run_tick(
            claim=_claim(),
            tick_id=f"judge-{index:03d}",
            now=datetime(2026, 8, 28, 9, 30, index, tzinfo=_KST),
            port=port,
        )

    assert len(repository.ai_judgements) == 1
    record = repository.ai_judgements[0]
    # provider가 붙지 않은 배포에서도 기록은 남는다. "묻지 않았다"도 사실이기 때문이다.
    assert record.participation == "NOT_PARTICIPATED"
    assert record.baseline_symbol == "005930"
    assert record.selected_symbol == "005930"
    assert record.candidate_count == 1
    assert not hasattr(record, "confidence_bps")


def test_zero_evidence_v3_boundary_atomically_records_not_participated() -> None:
    repository = FakeRepository(_v3_state())
    port = FakeRuntimePort(quotes={"005930": Quote("005930", 75_000, 52_500, 97_500)})
    for index in range(1, 4):
        PersistentAutomationRunner(cast(Any, repository)).run_tick(
            claim=_claim(),
            tick_id=f"v3-zero-{index:03d}",
            now=datetime(2026, 8, 28, 9, 30, index, tzinfo=_KST),
            port=port,
        )

    assert repository.state["state"] == "BUY_CANDIDATE_SELECTED"
    assert len(repository.ai_judgements) == 1
    record = repository.ai_judgements[0]
    assert record.participation == "NOT_PARTICIPATED"
    assert record.judge_call_count == 0
    assert record.prompt_version == "vertex-news-screen-v2"


def test_ai_audit_failure_cannot_advance_the_checkpoint() -> None:
    class FailingAtomicRepository(FakeRepository):
        def advance_with_ai_judgement(
            self,
            command: AdvanceCommand,
            claim: RuntimeClaim,
            record: AiJudgementRecord,
        ) -> tuple[int, bool]:
            del command, claim, record
            raise AutomationRuntimeError("AI_AUDIT_WRITE_FAILED")

    repository = FailingAtomicRepository(_v3_state("NEWS_SCREENING", 3))
    port = FakeRuntimePort(quotes={"005930": Quote("005930", 75_000, 52_500, 97_500)})

    with pytest.raises(AutomationRuntimeError, match="AI_AUDIT_WRITE_FAILED"):
        PersistentAutomationRunner(cast(Any, repository)).run_tick(
            claim=_claim(),
            tick_id="v3-audit-failure",
            now=datetime(2026, 8, 28, 9, 30, tzinfo=_KST),
            port=port,
        )

    assert repository.state["state"] == "NEWS_SCREENING"
    assert repository.commands == []
    assert repository.ai_judgements == []


def test_runtime_planner_uses_shared_kis_calendar_corrections():
    from datetime import date

    planner = XkrxBoundaryPlanner()
    assert planner.next_session(date(2026, 6, 2)) == date(2026, 6, 4)
    assert planner.next_session(date(2026, 7, 16)) == date(2026, 7, 20)
    assert planner.next_session(date(2026, 8, 14)) == date(2026, 8, 18)


def _fallback_service(
    monkeypatch: pytest.MonkeyPatch,
    *,
    retry_at: datetime | None,
    wakes_at: datetime,
    connectivity: bool = True,
    resume_raises: Exception | None = None,
    owner: str = "usr_demo_user",
) -> tuple[Any, list[str], list[float]]:
    """데이터 공백 fallback 경로만 도는 상주 서비스를 만든다.

    함정. `_owner_user_id` 는 생성자에서 `P1_AUTOMATION_OWNER_USER_ID` 를 읽는다. 생성 전에
    넣지 않으면 서비스가 `retry_at` 을 아예 부르지 않고 테스트가 조용히 통과한다.

    `_wait_until` 은 벽시계를 실제로 기다린다. 여기서는 기다리는 대신 **시계를 `wakes_at`
    으로 전진시킨다** - 실제 코드가 하는 일이 그것이고(경계까지 잠들었다가 다시 `now()` 를
    읽는다), 그 전진이 없으면 "기다리는 동안 날짜가 넘어갔다"를 만들 수 없다.
    """

    from app.p1_owner import automation_runtime as runtime

    monkeypatch.setenv("P1_AUTOMATION_OWNER_USER_ID", owner)
    # 기존 `test_connectivity_failure_does_not_consume_a_session` 과 같은 개장 중 시각에서
    # 출발한다. 이 시각이면 `serve()` 가 wakeup 경계를 지나 claim 까지 내려간다.
    current = datetime(2026, 9, 7, 10, 0, tzinfo=_KST)
    calls: list[str] = []
    waits: list[float] = []

    class Clock(datetime):
        @staticmethod
        def now(tz: Any) -> datetime:
            return current.astimezone(tz)

    class Repository:
        def preflight(self) -> None:
            calls.append("preflight")

        def claim(self, *args: Any) -> None:
            calls.append("claim")
            return None

        def last_completed_session(self, user_id: str) -> date | None:
            # 이 시나리오에는 닫다 만 세션이 없다. 기동 복구가 할 일 없이 지나가야 한다.
            return None

        def retry_at(self, user_id: str, session_date: date) -> datetime | None:
            calls.append(f"retry_at:{user_id}:{session_date.isoformat()}")
            return retry_at

        def resume_data_gap(self, user_id: str, session_date: date) -> int:
            calls.append(f"resume:{user_id}:{session_date.isoformat()}")
            if resume_raises is not None:
                raise resume_raises
            return 1

    class Daily:
        def ensure_daily_signals(self, session: date) -> None:
            calls.append(f"daily:{session.isoformat()}")

        def close(self) -> None:
            calls.append("close")

    class Stop:
        """한 바퀴만 돌린다.

        `serve()` 는 fallback 분기 끝에서 `continue` 하므로 정지 신호가 없으면 영원히 돈다.
        claim 시도를 한 번 본 뒤 정지로 바꿔 한 순회의 순서만 관찰한다.
        """

        def is_set(self) -> bool:
            return "claim" in calls

        def wait(self, seconds: float) -> bool:
            waits.append(seconds)
            return True

    def check() -> bool:
        calls.append("connectivity")
        return connectivity

    service = AutomationRuntimeService(
        cast(Any, Repository()),
        cast(Any, None),
        "x" * 32,
        daily_inference=cast(Any, Daily()),
        connectivity_check=check,
    )
    service._stop = cast(Any, Stop())
    monkeypatch.setattr(runtime, "datetime", Clock)

    def wait_until(self: Any, boundary: datetime) -> bool:
        nonlocal current
        calls.append("wait_until")
        # 재시도 경계를 기다린 뒤에 깨어나는 시각을 시험이 정한다.
        if retry_at is not None and boundary == retry_at:
            current = wakes_at
        return False

    monkeypatch.setattr(type(service), "_wait_until", wait_until)
    return service, calls, waits


def test_data_gap_fallback_resumes_after_the_retry_boundary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """claim 이 없고 DB 가 재시도 시각을 주면 그 시각 뒤에 재개한다.

    순서가 중요하다 - 경계를 기다린 뒤 **날짜와 연결성을 다시 확인하고** 재개해야 한다.
    벽시계가 다음 날로 넘어갔거나 회선이 끊긴 사이에 재개하면 지난 세션을 되살린다.
    """

    service, calls, _ = _fallback_service(
        monkeypatch,
        retry_at=datetime(2026, 9, 7, 10, 2, tzinfo=_KST),
        wakes_at=datetime(2026, 9, 7, 10, 2, tzinfo=_KST),
    )

    service.serve()

    assert "retry_at:usr_demo_user:2026-09-07" in calls
    resume_index = calls.index("resume:usr_demo_user:2026-09-07")
    # 재개 직전에 연결성을 다시 본다.
    assert calls[resume_index - 1] == "connectivity"
    assert "AUTOMATION_FALLBACK=RESUMED" in capsys.readouterr().out


def test_data_gap_fallback_does_not_resume_a_session_that_already_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """경계를 기다리는 동안 날짜가 넘어갔으면 재개하지 않는다."""

    service, calls, _ = _fallback_service(
        monkeypatch,
        retry_at=datetime(2026, 9, 7, 10, 2, tzinfo=_KST),
        # 경계를 기다리는 동안 취소 경계(15:20)를 넘어 깨어난다.
        wakes_at=datetime(2026, 9, 7, 15, 40, tzinfo=_KST),
    )

    service.serve()

    assert not any(item.startswith("resume:") for item in calls)


def test_data_gap_fallback_does_not_resume_while_the_line_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """회선이 끊겼으면 재개하지 않는다. 세션을 소비하지도 않는다."""

    service, calls, waits = _fallback_service(
        monkeypatch,
        retry_at=datetime(2026, 9, 7, 10, 2, tzinfo=_KST),
        wakes_at=datetime(2026, 9, 7, 10, 2, tzinfo=_KST),
        connectivity=False,
    )

    service.serve()

    assert not any(item.startswith("resume:") for item in calls)
    assert waits == [60.0]


def test_a_blocked_resume_is_recorded_and_never_looks_like_a_resume(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """재개가 DB 에서 거부되면 표식이 그것을 말한다.

    조용히 넘어가면 "재개했는데 아무 일도 없었다"와 "재개가 거부됐다"가 구별되지 않는다.
    """

    service, calls, waits = _fallback_service(
        monkeypatch,
        retry_at=datetime(2026, 9, 7, 10, 2, tzinfo=_KST),
        wakes_at=datetime(2026, 9, 7, 10, 2, tzinfo=_KST),
        resume_raises=AutomationRuntimeError("AUTOMATION_RESUME_DENIED"),
    )

    service.serve()
    output = capsys.readouterr().out

    assert "AUTOMATION_FALLBACK=BLOCKED" in output
    assert "AUTOMATION_FALLBACK=RESUMED" not in output
    assert any(item.startswith("resume:") for item in calls)
    assert waits == [60.0]


def test_no_owner_user_id_means_no_fallback_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """소유자 식별자가 없으면 재시도 시각을 묻지도 않는다.

    이 성질이 무너지면 위 테스트들이 `retry_at` 을 부르지 않고도 초록불이 된다.
    """

    service, calls, _ = _fallback_service(
        monkeypatch,
        retry_at=datetime(2026, 9, 7, 10, 2, tzinfo=_KST),
        wakes_at=datetime(2026, 9, 7, 10, 2, tzinfo=_KST),
        owner="",
    )

    service.serve()

    assert not any(item.startswith("retry_at:") for item in calls)
    assert not any(item.startswith("resume:") for item in calls)


class _RecordingPortfolioRunner:
    """continue_session 호출을 그대로 기록해 실제 연결 여부만 본다."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def continue_session(
        self,
        *,
        claim: RuntimeClaim,
        state: dict[str, Any],
        port: Any,
        allow_new_orders: bool = True,
    ) -> Any:
        del port
        self.calls.append(
            {
                "runId": claim.run_id,
                "state": str(state["state"]),
                "allowNewOrders": allow_new_orders,
            }
        )

        class _Result:
            status = "NO_ELIGIBLE_ADJUSTMENT"
            planned_orders = 0
            completed_orders = 0

        return _Result()


def _continuation_service(
    terminal_state: str, runner: _RecordingPortfolioRunner
) -> AutomationRuntimeService:
    """하나의 tick 만에 ``terminal_state`` 로 끝나는 claim 을 구동한다."""

    state = _state()
    state["state"] = "PRECHECK"

    class Repository:
        def read_state(self, claim: RuntimeClaim) -> dict[str, Any]:
            del claim
            return dict(state)

        def roll_schedule(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs

    class Runner:
        def run_tick(self, **kwargs: Any) -> dict[str, Any]:
            del kwargs
            return {"state": terminal_state}

    class Port:
        def close(self) -> None:
            return None

    class PortFactory:
        def build(self, claim: RuntimeClaim, state: dict[str, Any]) -> Any:
            del claim, state
            return Port()

    service = AutomationRuntimeService(
        cast(Any, Repository()),
        cast(Any, PortFactory()),
        "s" * 48,
        portfolio_runner=cast(Any, runner),
    )
    service._runner = cast(Any, Runner())
    service._planner = cast(Any, _ImmediatePlanner())
    return service


class _ImmediatePlanner:
    def next_wakeup(self, now: datetime, state: str, session: date | None = None) -> datetime:
        del state, session
        return now

    def next_session(self, session: date) -> date:
        return session


@pytest.mark.parametrize(
    ("terminal_state", "expected_calls"),
    [
        # 결정 시점마다 한 번씩 - 빈 슬롯을 채울 기회가 하루에 여러 번 있어야 한다.
        ("COMPLETED", len(_DECISION_TIMES)),
        ("CANCELLED_UNFILLED", len(_DECISION_TIMES)),
        # 기존 엔진이 후보를 못 고른 날에도 남은 세션 예산으로 이어가야 한다.
        # 이 분기가 빠져 있어서 2026-09-09 무주문 실행에는 탈락 근거조차 남지 않았다.
        ("SKIPPED_NO_ACTION", len(_DECISION_TIMES)),
        # 데이터 결손과 명시 거부권, 정지는 이어가지 않는다.
        ("SKIPPED_DATA_UNAVAILABLE", 0),
        ("NEWS_VETOED", 0),
        ("HALTED", 0),
    ],
)
def test_portfolio_continuation_runs_for_every_state_that_still_has_order_budget(
    terminal_state: str, expected_calls: int
) -> None:
    runner = _RecordingPortfolioRunner()

    _continuation_service(terminal_state, runner)._drive_claim(_claim())

    assert len(runner.calls) == expected_calls
    if expected_calls:
        assert runner.calls[0]["state"] == "PRECHECK"


def test_continuation_after_the_buy_deadline_may_reconcile_but_not_plan_new_orders() -> None:
    """09:40 이 지난 뒤의 이어가기는 새 매수를 계획하지 않는다.

    세션일이 아닌 날 구동되면 매수 창은 이미 닫힌 것으로 본다.
    """

    runner = _RecordingPortfolioRunner()

    _continuation_service("SKIPPED_NO_ACTION", runner)._drive_claim(_claim())

    assert runner.calls[0]["allowNewOrders"] is False


class _RecoveringRepository:
    """놓친 세션을 마감하고 연쇄를 잇는 저장소.

    **DB 계약을 그대로 흉내낸다.** `p1_roll_automation_schedule_v1` 은 직전 세션이
    COMPLETED 일 때만 다음 칸을 ARM 하고, 새로 만든 칸은 ARMED 로 남는다. 예전 대역은
    굴리자마자 COMPLETED 로 쳐서 "한 칸에서 멈추는" 실제 결함을 숨기고 있었다.
    """

    def __init__(self, *, settled: int, last_completed: date | None) -> None:
        self.settled = settled
        self.cursor_reads = 0
        self.rolled: list[tuple[date, date]] = []
        self.states: dict[date, str] = {}
        if last_completed is not None:
            self.states[last_completed] = "COMPLETED"

    def settle_missed_schedules(self, user_id: str, today: date) -> int:
        """오늘보다 앞선 ARMED 행을 마감한다 - 실행되지 않고 지나간 세션들이다."""

        del user_id
        missed = [
            session
            for session, state in self.states.items()
            if state == "ARMED" and session < today
        ]
        for session in missed:
            self.states[session] = "COMPLETED"
        if not self.rolled:
            # 첫 호출은 시나리오가 정한 값을 쓴다(놓친 행이 없는 경우도 표현하려고).
            return self.settled
        return len(missed)

    def _recovery_cursor(self, user_id: str) -> tuple[date | None, int | None]:
        """실제 저장소와 같은 통로로만 답한다.

        runtime 역할에는 `automation_runtime_schedule`/`automation_control` 의 직접 SELECT
        권한이 없다. 예전 대역은 두 값을 그냥 파이썬으로 돌려줘 그 경계를 통과해 버렸고,
        그래서 프로덕션에서 InsufficientPrivilege 로 끊기는 것을 못 잡았다.
        여기서도 V171 함수 하나만 있는 것처럼 둔다.
        """

        del user_id
        self.cursor_reads += 1
        completed = [s for s, state in self.states.items() if state == "COMPLETED"]
        return (max(completed) if completed else None), 2

    def last_completed_session(self, user_id: str) -> date | None:
        return self._recovery_cursor(user_id)[0]

    def control_version(self, user_id: str) -> int:
        version = self._recovery_cursor(user_id)[1]
        assert version is not None
        return version

    def roll_schedule(
        self,
        user_id: str,
        completed_session: date,
        next_session: date,
        expected_control_version: int,
    ) -> str:
        del user_id, expected_control_version
        if self.states.get(completed_session) != "COMPLETED":
            # SQL 함수의 `prior_schedule.schedule_state<>'COMPLETED'` gate 와 같다.
            raise AutomationRuntimeError("AUTOMATION_ROLL_GATE_CLOSED")
        self.rolled.append((completed_session, next_session))
        self.states[next_session] = "ARMED"
        return "auto_sched_" + "a" * 32


def _recovery_service(repository: _RecoveringRepository) -> AutomationRuntimeService:
    service = AutomationRuntimeService(
        cast(Any, repository),
        cast(Any, object()),
        "s" * 48,
    )
    service._owner_user_id = "usr_automation_runtime_0001"
    return service


def test_a_broken_schedule_chain_recovers_without_a_human() -> None:
    """며칠 멈춰도 사람이 DB 를 만지지 않고 거래일을 다시 따라가야 한다.

    스케줄은 "직전 세션이 COMPLETED 여야 다음을 ARM" 하는 연쇄다. 실행되지 않은 ARMED
    행이 남으면 그 조건을 영원히 만족하지 못해 자동 운용이 돌아오지 않는다.
    2026-09-10 이 그 상태로 남아 09-11~09-14 가 통째로 비었다.
    """

    repository = _RecoveringRepository(settled=1, last_completed=date(2026, 9, 10))

    _recovery_service(repository)._settle_and_arm(date(2026, 9, 15))

    # 09-10 에서 오늘(09-15)까지 한 칸씩 굴려 연쇄를 복원해야 한다.
    assert repository.rolled
    assert repository.rolled[0][0] == date(2026, 9, 10)
    assert repository.rolled[-1][1] >= date(2026, 9, 15)
    # 한 칸(09-11)에서 멈추면 안 된다. 하루에 한 칸씩만 전진하면 영영 오늘을 못 따라잡는다.
    assert len(repository.rolled) >= 3, repository.rolled
    assert repository.states[date(2026, 9, 15)] == "ARMED"
    # 기준점은 테이블 직접 SELECT 가 아니라 하나의 capability 통로로만 읽어야 한다.
    assert repository.cursor_reads >= len(repository.rolled)


def test_recovery_does_nothing_when_the_chain_is_intact() -> None:
    """놓친 세션이 없으면 스케줄을 건드리지 않는다."""

    repository = _RecoveringRepository(settled=0, last_completed=date(2026, 9, 14))

    _recovery_service(repository)._settle_and_arm(date(2026, 9, 15))

    assert repository.rolled == []


def test_recovery_failure_does_not_raise_into_the_tick_loop() -> None:
    """복구가 실패해도 tick 을 죽이지 않는다. 다음 경계에서 다시 시도한다."""

    class FailingRepository(_RecoveringRepository):
        def settle_missed_schedules(self, user_id: str, today: date) -> int:
            raise AutomationRuntimeError("AUTOMATION_SETTLE_UNAVAILABLE")

    _recovery_service(FailingRepository(settled=0, last_completed=None))._settle_and_arm(
        date(2026, 9, 15)
    )


def test_a_no_order_decision_time_leaves_its_reason_in_the_funnel() -> None:
    """무주문으로 끝난 결정 시점의 사유가 화면까지 가야 한다.

    2026-09-15 실거래일에 포트폴리오 경로가 09:45/11:00 모두 `NO_ELIGIBLE_ADJUSTMENT`
    였는데 그 사유는 **로그에만** 있었다. 단일 주문 엔진 경로에는 퍼널이 붙어 있고
    이쪽만 비어 있어서, 화면에는 또 "주문 없이 종료"만 남았다.
    """

    runner = _RecordingPortfolioRunner()
    service = _continuation_service("SKIPPED_NO_ACTION", runner)

    recorded: list[tuple[str, str, str, str | None]] = []

    def _record(claim: RuntimeClaim, outcomes: tuple[Any, ...]) -> int:
        del claim
        for item in outcomes:
            recorded.append((item.stage, item.symbol, item.outcome, item.reason_code))
        return len(outcomes)

    service._repository.record_stage_outcomes = _record  # type: ignore[attr-defined]
    service._drive_claim(_claim())

    assert recorded, "무주문 결정 시점의 사유가 한 건도 남지 않았다"
    stage, symbol, outcome, reason = recorded[0]
    assert stage == "ORDER"
    assert symbol == "000000"
    assert outcome == "DROPPED"
    assert reason == "NO_ELIGIBLE_ADJUSTMENT"


def test_recording_the_portfolio_reason_never_breaks_the_session() -> None:
    """진단 기록 실패가 거래를 멈추면 안 된다."""

    runner = _RecordingPortfolioRunner()
    service = _continuation_service("SKIPPED_NO_ACTION", runner)

    def _boom(claim: RuntimeClaim, outcomes: tuple[Any, ...]) -> int:
        raise RuntimeError("stage outcome store unavailable")

    service._repository.record_stage_outcomes = _boom  # type: ignore[attr-defined]
    service._drive_claim(_claim())

    assert runner.calls, "기록 실패로 이어가기가 멈췄다"


def test_every_decision_time_can_still_place_a_buy() -> None:
    """세 결정 시점 중 하나라도 매수를 못 하면 그 시점은 존재하지 않는 것과 같다.

    2026-09-15 14:00 에 실측으로 `BUY_WINDOW_CLOSED` 가 났다. 판정이
    `<= _LAST_DECISION_TIME`(14:00) 이라, `_wait_until` 이 깨어난 뒤 실제 실행이
    14:00:0x 여서 마지막 시점은 **구조적으로 항상** 닫혔다. 매수 창의 끝은 결정 시점이
    아니라 제출 마감(`_BUY_SUBMIT_DEADLINE`)이고, 그 값은 DB 와 맞춰져 있다.
    """

    from datetime import datetime, timedelta

    from app.p1_owner.automation_runtime import (
        _BUY_SUBMIT_DEADLINE,
        _CANCEL_BOUNDARY,
        _DECISION_TIMES,
    )

    for boundary in _DECISION_TIMES:
        # 경계에서 깨어나 실제로 도는 순간은 언제나 그보다 조금 뒤다.
        woke = (datetime(2026, 9, 15, boundary.hour, boundary.minute) + timedelta(seconds=5)).time()
        assert woke <= _BUY_SUBMIT_DEADLINE, f"{boundary} 시점이 매수 창 밖이다"

    assert _DECISION_TIMES[-1] < _BUY_SUBMIT_DEADLINE < _CANCEL_BOUNDARY


def test_the_buy_window_is_bounded_by_the_submit_deadline_not_the_last_decision_time() -> None:
    """판정식이 다시 `_LAST_DECISION_TIME` 으로 돌아가지 않게 고정한다."""

    import pathlib as _pathlib

    from app.p1_owner import automation_runtime

    source = _pathlib.Path(automation_runtime.__file__).read_text(encoding="utf-8")
    start = source.index("allow_new_orders = (")
    clause = source[start : source.index("try:", start)]
    assert "_BUY_SUBMIT_DEADLINE" in clause
    assert "_LAST_DECISION_TIME" not in clause


def test_a_failed_continuation_says_which_failure_it_was() -> None:
    """종류 이름만 찍으면 "마감 넘김"과 "DB 장애"를 다음 날 아침에 구분할 수 없다.

    claim 이 RELEASED 인 채 15:20 을 넘기면 실행 읽기가 42501 로 닫힌다. 그것은 미체결
    잔량의 장부 마감이 미뤄진 것이지 장애가 아니다(체결분은 관측 즉시 들어간다).
    DB 일시 장애는 진짜 장애다. 로그 한 줄로 갈릴 수 있어야 한다.
    """

    from app.p1_owner.automation_runtime import _bounded_error_detail

    class _Denied(Exception):
        sqlstate = "42501"

    denied = _Denied("automation portfolio claim unavailable")
    assert _bounded_error_detail(denied) == "automation portfolio claim unavailable"
    assert getattr(denied, "sqlstate", None) == "42501"

    # 줄바꿈이 섞인 긴 메시지도 한 줄로, 길이를 묶어서 남긴다.
    noisy = RuntimeError("first line\n   second line\t third")
    assert _bounded_error_detail(noisy) == "first line second line third"
    assert len(_bounded_error_detail(RuntimeError("x" * 500))) == 203

    # 메시지가 비어도 빈 줄을 남기지 않는다.
    assert _bounded_error_detail(RuntimeError()) == "(no message)"


# ---------------------------------------------------------------------------
# 멈춘 세션 복구 - 2026-09-16 사고의 회귀
#
# 15:20 정산 시각에 컨테이너가 죽어 있었다. 되살린 프로세스는 미완 실행을 보지도 않고
# 다음 거래일까지 잤고, ACTIVE 로 남은 claim 이 다음 세션 claim 을 40001 로 막았다.
# 프로세스 한 번의 죽음이 자동 운용을 영구히 멈추는 구조였다.
# ---------------------------------------------------------------------------


def test_a_settlement_that_arrives_late_does_not_get_pushed_to_the_next_session() -> None:
    """마감 경계를 지나 도착한 미정산 실행은 지금 정산한다.

    이것이 사고의 핵심이었다. 15:20 **정각**은 원래도 즉시 처리했지만(경계가 `<=`),
    한 순간이라도 늦으면 다음 거래일 개장으로 밀려 아무도 그 실행을 닫지 않았다.
    """

    planner = XkrxBoundaryPlanner()
    session = date(2026, 9, 16)

    on_time = datetime(2026, 9, 16, 15, 20, tzinfo=_KST)
    assert planner.next_wakeup(on_time, "PENDING_RECONCILIATION", session) == on_time

    late = datetime(2026, 9, 16, 16, 12, tzinfo=_KST)
    assert planner.next_wakeup(late, "PENDING_RECONCILIATION", session) == late
    assert planner.next_wakeup(late, "ORDER_SUBMITTED", session) == late


def test_the_planner_plans_for_the_claims_session_not_for_todays() -> None:
    """기준일을 넘기면 그 실행의 시간표로 계산한다.

    넘기지 않으면 15:20 을 지난 시각에서 `current_or_next_session` 이 **다음** 거래일을
    돌려주므로, 오늘 실행을 정산하려던 계산이 내일을 가리킨다.
    """

    planner = XkrxBoundaryPlanner()
    late = datetime(2026, 9, 16, 16, 12, tzinfo=_KST)

    assert planner.next_wakeup(late, "PENDING_RECONCILIATION") == datetime(
        2026, 9, 17, 15, 20, tzinfo=_KST
    )
    assert planner.next_wakeup(late, "PENDING_RECONCILIATION", date(2026, 9, 16)) == late


def test_waiting_for_the_cancel_boundary_is_unchanged() -> None:
    """경계 **전**에는 종전대로 그 경계까지 기다린다. 일찍 정산하지 않는다."""

    planner = XkrxBoundaryPlanner()
    session = date(2026, 9, 16)
    before = datetime(2026, 9, 16, 10, 0, tzinfo=_KST)

    assert planner.next_wakeup(before, "PENDING_RECONCILIATION", session) == datetime(
        2026, 9, 16, 15, 20, tzinfo=_KST
    )


def _stranded_recovery_service(
    monkeypatch: pytest.MonkeyPatch,
    *,
    now: datetime,
    last_completed: date | None,
    claim_found: bool = True,
    final_state: str = "COMPLETED",
    drive_raises: BaseException | None = None,
) -> tuple[Any, list[str]]:
    """복구 한 번만 관찰하는 서비스. `serve()` 는 부르지 않는다."""

    from app.p1_owner import automation_runtime as runtime

    calls: list[str] = []

    claim = RuntimeClaim(
        user_id="usr_demo_user",
        run_id="run_stranded",
        control_version=14,
        account_id="acc",
        principle_id="prn",
        strategy_id="stg",
        baseline_account_digest="d" * 64,
        replayed=True,
        session_date=date(2026, 9, 16),
        claim_token_hash="h" * 64,
    )

    class Repository:
        def last_completed_session(self, user_id: str) -> date | None:
            calls.append(f"cursor:{user_id}")
            return last_completed

        def claim(self, session_date: date, claim_token_hash: str) -> RuntimeClaim | None:
            calls.append(f"claim:{session_date.isoformat()}")
            return claim if claim_found else None

        def read_state(self, _claim: RuntimeClaim) -> dict[str, Any]:
            calls.append("read_state")
            return {"state": final_state}

    class Clock(datetime):
        @staticmethod
        def now(tz: Any = None) -> datetime:
            return now.astimezone(tz) if tz is not None else now

    service = AutomationRuntimeService(
        cast(Any, Repository()),
        cast(Any, None),
        "x" * 32,
    )
    monkeypatch.setenv("P1_AUTOMATION_OWNER_USER_ID", "usr_demo_user")
    service._owner_user_id = "usr_demo_user"
    monkeypatch.setattr(runtime, "datetime", Clock)

    def drive(self: Any, driven: RuntimeClaim) -> None:
        calls.append(f"drive:{driven.run_id}")
        if drive_raises is not None:
            raise drive_raises

    monkeypatch.setattr(type(service), "_drive_claim", drive)
    return service, calls


def test_a_stranded_session_is_settled_on_the_next_start(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """마지막 COMPLETED 다음 거래일을 이어받아 기존 정산 경로로 넘긴다.

    스케줄 연쇄가 "직전 세션이 COMPLETED 여야 다음을 ARM" 이므로 미완 세션은 많아야
    하나다. 새 조회를 만들지 않고 이미 있는 복구 커서로 그 하나를 짚는다.
    """

    service, calls = _stranded_recovery_service(
        monkeypatch,
        now=datetime(2026, 9, 16, 16, 12, tzinfo=_KST),
        last_completed=date(2026, 9, 15),
    )

    service._recover_stranded_session()

    assert calls == [
        "cursor:usr_demo_user",
        "claim:2026-09-16",
        "drive:run_stranded",
        "read_state",
    ]
    out = capsys.readouterr().out
    assert "AUTOMATION_RECOVERY=RESUMED session=2026-09-16 run=run_stranded" in out
    assert "AUTOMATION_RECOVERY=SETTLED session=2026-09-16 state=COMPLETED" in out


def test_recovery_does_not_touch_a_session_whose_close_has_not_arrived(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """아직 마감 전인 세션은 건드리지 않는다 - 평소 흐름이 제 시각에 처리한다.

    여기서 claim 을 잡아 버리면 기동할 때마다 그날 운용을 복구 경로가 가로챈다.
    """

    service, calls = _stranded_recovery_service(
        monkeypatch,
        now=datetime(2026, 9, 16, 9, 0, tzinfo=_KST),
        last_completed=date(2026, 9, 15),
    )

    service._recover_stranded_session()

    assert calls == ["cursor:usr_demo_user"]
    assert "AUTOMATION_RECOVERY=NONE session=2026-09-16" in capsys.readouterr().out


def test_nothing_to_recover_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """claim 이 없으면 정산할 것도 없다. 평상시 기동 비용은 조회 두 번이다."""

    service, calls = _stranded_recovery_service(
        monkeypatch,
        now=datetime(2026, 9, 16, 16, 12, tzinfo=_KST),
        last_completed=date(2026, 9, 15),
        claim_found=False,
    )

    service._recover_stranded_session()

    assert calls == ["cursor:usr_demo_user", "claim:2026-09-16"]
    assert "AUTOMATION_RECOVERY=NONE session=2026-09-16" in capsys.readouterr().out


def test_a_failed_recovery_never_stops_the_runtime_from_starting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """오늘 하나를 못 닫는 것보다 runtime 이 아예 안 뜨는 쪽이 훨씬 나쁘다."""

    service, calls = _stranded_recovery_service(
        monkeypatch,
        now=datetime(2026, 9, 16, 16, 12, tzinfo=_KST),
        last_completed=date(2026, 9, 15),
        drive_raises=RuntimeError("boom"),
    )

    service._recover_stranded_session()

    assert "read_state" not in calls
    out = capsys.readouterr().out
    assert "AUTOMATION_RECOVERY=FAILED stage=drive session=2026-09-16" in out
    assert "detail=boom" in out


def test_recovery_is_skipped_without_an_owner(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    service, calls = _stranded_recovery_service(
        monkeypatch,
        now=datetime(2026, 9, 16, 16, 12, tzinfo=_KST),
        last_completed=date(2026, 9, 15),
    )
    service._owner_user_id = ""

    service._recover_stranded_session()

    assert calls == []
    assert capsys.readouterr().out == ""


def test_an_already_published_report_is_not_recorded_as_a_failure() -> None:
    """같은 입력 generation 이 이미 발행돼 있으면 그것은 실패가 아니다.

    `p1_publish_owner_performance_report_v1` 은 그 경우 23505 를 올리고(V158:143),
    파이썬은 `ScenarioMaterializationError` 로 감싸 던진다. 원인 사슬을 따라가지 않으면
    "할 일이 없음"과 진짜 장애가 같은 줄로 찍혀 구분되지 않는다.
    """

    from app.p1_owner.automation_runtime import _is_already_published

    class Conflict(Exception):
        sqlstate = "23505"

    class Broken(Exception):
        sqlstate = "08006"

    wrapped = RuntimeError("SCENARIO_PUBLISH_FAILED")
    wrapped.__cause__ = Conflict("owner performance source identity conflict")
    assert _is_already_published(wrapped) is True

    outage = RuntimeError("SCENARIO_PUBLISH_FAILED")
    outage.__cause__ = Broken("connection failure")
    assert _is_already_published(outage) is False

    assert _is_already_published(RuntimeError("no cause at all")) is False


def test_the_already_published_check_terminates_on_a_cyclic_cause() -> None:
    """원인 사슬이 자기 자신을 가리켜도 멈춘다. 로그 한 줄 때문에 런타임이 돌지 않게 한다."""

    from app.p1_owner.automation_runtime import _is_already_published

    first = RuntimeError("first")
    second = RuntimeError("second")
    first.__cause__ = second
    second.__cause__ = first

    assert _is_already_published(first) is False


def test_a_recovery_that_did_not_terminalize_is_not_called_settled(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """미terminal 로 끝났으면 그렇게 적는다.

    2026-09-16 복구는 예산이 바닥나 `PENDING_RECONCILIATION` 으로 끝났는데 마커는
    `SETTLED` 였다. 그러면 claim 이 아직 ACTIVE 로 남아 다음 세션을 막고 있다는 사실이
    로그에서 사라진다.
    """

    service, _ = _stranded_recovery_service(
        monkeypatch,
        now=datetime(2026, 9, 16, 16, 12, tzinfo=_KST),
        last_completed=date(2026, 9, 15),
        final_state="PENDING_RECONCILIATION",
    )

    service._recover_stranded_session()

    out = capsys.readouterr().out
    assert "AUTOMATION_RECOVERY=NOT_SETTLED session=2026-09-16 state=PENDING_RECONCILIATION" in out
    assert "AUTOMATION_RECOVERY=SETTLED" not in out


def test_an_exhausted_call_budget_stops_the_tick_loop_instead_of_burning_retries(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """예산이 바닥나면 즉시 멈춘다.

    같은 호출을 반복해도 절대 성공하지 못한다. 실제로 재시도 15칸 중 6칸을 여기에 태웠고,
    그동안 진짜 원인은 로그 마지막 줄에 묻혀 있었다.
    """

    from app.data.kis.accounting import KISCallBudgetExceeded

    ticks: list[str] = []

    class Runner:
        def run_tick(self, **kwargs: Any) -> dict[str, Any]:
            ticks.append(str(kwargs["tick_id"]))
            raise KISCallBudgetExceeded("brokerage cap 20 reached")

    class Repository:
        def read_state(self, _claim: RuntimeClaim) -> dict[str, Any]:
            return {"state": "PENDING_RECONCILIATION", "checkpointVersion": 3}

    class Port:
        def close(self) -> None:
            ticks.append("close")

    class PortFactory:
        def build(self, _claim: RuntimeClaim, _state: dict[str, Any]) -> Port:
            return Port()

    service = AutomationRuntimeService(
        cast(Any, Repository()),
        cast(Any, PortFactory()),
        "x" * 32,
    )
    service._runner = cast(Any, Runner())
    service._planner = cast(Any, _ImmediatePlanner())
    monkeypatch.setattr(type(service), "_wait_until", lambda self, boundary: False)

    service._drive_claim(_claim())

    # 한 번 부딪히면 끝이다. 재시도 15칸을 태우지 않는다.
    assert len([t for t in ticks if t != "close"]) == 1
    assert "AUTOMATION_TICK_BUDGET_EXHAUSTED" in capsys.readouterr().out


def test_a_hole_in_the_chain_is_filled_when_the_claim_comes_back_empty() -> None:
    """ARMED 행이 없어 claim 이 빈손이면, 자러 가기 전에 연쇄를 이어 붙인다.

    2026-09-16 이 COMPLETED 로 끝났는데 09-17 ARMED 행이 생기지 않았다. `_drive_claim` 은
    tick 이 **그 호출 안에서** terminal 로 바뀔 때만 `roll_schedule` 을 부르므로, 이미
    terminal 인 상태를 읽으면 그냥 반환한다. 그렇게 생긴 빈칸은 마감할 행도 없어
    `_settle_and_arm` 이 조기 반환하고, 아무도 메우지 않아 자동 운용이 조용히 멈춘다.
    """

    repository = _RecoveringRepository(settled=0, last_completed=date(2026, 9, 16))

    armed = _recovery_service(repository)._advance_schedule_to(date(2026, 9, 17))

    assert repository.rolled == [(date(2026, 9, 16), date(2026, 9, 17))]
    # 굴렸다고 알려야 호출자가 그날 claim 을 다시 시도한다. 여기서 False 를 돌려주면
    # 빈칸을 메워 놓고도 다음 세션까지 자서 그날 운용이 통째로 날아간다.
    assert armed is True


def test_advancing_stops_once_the_cursor_reaches_the_target() -> None:
    """이미 따라잡았으면 아무것도 굴리지 않는다. 이 경로는 멱등해야 한다."""

    repository = _RecoveringRepository(settled=0, last_completed=date(2026, 9, 17))

    armed = _recovery_service(repository)._advance_schedule_to(date(2026, 9, 17))

    assert repository.rolled == []
    assert armed is False
