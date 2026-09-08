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
    _required_atr_periods,
    inputs_from_state,
)

_KST = ZoneInfo("Asia/Seoul")


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
