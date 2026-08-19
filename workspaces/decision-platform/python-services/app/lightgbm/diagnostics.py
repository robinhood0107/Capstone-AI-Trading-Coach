"""S5 자율 운영의 append-only 진단 원장.

지금까지 실패는 코드 이름만 남겼고, 어떤 단위에서 어떤 수치로 걸렸는지는 버려졌다. 그래서 원인을
찾을 때마다 사람이 진단 스크립트를 써야 했다. 이 원장은 그 비용을 없애는 것이 목적이며, 코드가
스스로 재시도·제외·정지를 고르는 근거이기도 하다.

경계:
- append-only. 기존 줄은 고치지 않는다.
- 신원·분류·수치만 담는다. provider 응답 조각은 담지 않는다.
- 회계 원장이 아니다. 누적 호출 회계는 progress journal이 유지한다.
- 기록 실패가 수집 결과를 바꾸지 않는다. 진단을 남기지 못하는 것과 데이터가 틀린 것은 다르다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from app.data._shared.canonical_json import canonical_json_bytes
from app.lightgbm.outcomes import CollectionUnit, OutcomeClass

DIAGNOSTIC_LEDGER_FILENAME = "diagnostics.jsonl"
DIAGNOSTIC_EVENT_VERSION = "s5-diagnostic-event-v1"
# 실패가 아닌 보고다. OutcomeClass를 실패 분류로만 유지하기 위해 값을 분리한다.
COVERAGE_REPORT_OUTCOME = "COVERAGE_REPORT"
# 모델 gate 판정은 계약 위반도 증거 결손도 아니다. 재검증 루프가 읽는 보고다.
QUALIFICATION_REPORT_OUTCOME = "QUALIFICATION_REPORT"
# 달력 divergence는 차단 게이트 토큰이 따로 있고 원장에는 읽기 편의를 위해 미러링한다.
DIVERGENCE_MIRROR_OUTCOME = "CALENDAR_DIVERGENCE_SUSPECTED"
# 한 실행이 남길 수 있는 진단은 승인 호출 수보다 많을 수 없다. 무한 성장을 구조적으로 막는다.
MAX_DIAGNOSTIC_BYTES = 16 * 1024 * 1024


def record_diagnostic(
    *,
    source_root: Path,
    phase: str,
    outcome: OutcomeClass,
    unit: CollectionUnit | None = None,
    measured: Mapping[str, object] | None = None,
) -> None:
    """한 진단 사건을 원장에 append한다.

    같은 사건이 반복되면 줄이 늘어난다. 중복을 접지 않는 것은 의도적이다. 몇 번째 tick에서
    몇 번 발생했는지가 재시도 판단의 근거이기 때문이다.
    """

    _append_event(
        source_root=source_root,
        phase=phase,
        outcome=str(outcome),
        unit=unit,
        measured=measured,
    )


def _append_event(
    *,
    source_root: Path,
    phase: str,
    outcome: str,
    unit: CollectionUnit | None,
    measured: Mapping[str, object] | None,
) -> None:
    event = {
        "eventVersion": DIAGNOSTIC_EVENT_VERSION,
        "phase": phase,
        "outcome": outcome,
        "unit": unit.as_dict() if unit is not None else {},
        "measured": _canonical_measured(measured),
    }
    line = canonical_json_bytes(event) + b"\n"
    target = source_root / DIAGNOSTIC_LEDGER_FILENAME
    try:
        if target.exists() and target.stat().st_size + len(line) > MAX_DIAGNOSTIC_BYTES:
            return
        descriptor = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
        try:
            os.write(descriptor, line)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        # 진단을 남기지 못하는 것이 수집 결과를 바꾸지는 않는다.
        return


def record_report(
    *,
    source_root: Path,
    phase: str,
    report: str,
    measured: Mapping[str, object],
    unit: CollectionUnit | None = None,
) -> None:
    """실패가 아닌 보고를 남긴다. OutcomeClass는 실패 분류로만 유지한다."""

    _append_event(
        source_root=source_root,
        phase=phase,
        outcome=report,
        unit=unit,
        measured=measured,
    )


def record_coverage_report(
    *, source_root: Path, phase: str, measured: Mapping[str, object]
) -> None:
    """한 phase의 수집·제외·보류 수를 남긴다."""

    record_report(
        source_root=source_root,
        phase=phase,
        report=COVERAGE_REPORT_OUTCOME,
        measured=measured,
    )


def _canonical_measured(measured: Mapping[str, object] | None) -> dict[str, object]:
    """수치와 짧은 문자열만 남긴다. 구조를 열어두면 provider payload가 흘러들 수 있다."""

    output: dict[str, object] = {}
    for key, value in sorted((measured or {}).items()):
        if isinstance(value, bool) or isinstance(value, int):
            output[str(key)] = value
        elif isinstance(value, float):
            output[str(key)] = round(value, 6)
        elif isinstance(value, str):
            output[str(key)] = value[:64]
    return output


def read_diagnostics(*, source_root: Path) -> tuple[Mapping[str, object], ...]:
    """원장을 읽어 사건 목록을 준다. 손상된 줄은 조용히 건너뛰지 않고 거부한다."""

    import json

    target = source_root / DIAGNOSTIC_LEDGER_FILENAME
    if not target.exists():
        return ()
    events: list[Mapping[str, object]] = []
    with target.open(encoding="utf-8") as handle:
        for raw in handle:
            if not raw.strip():
                continue
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("diagnostic ledger event is invalid")
            events.append(value)
    return tuple(events)
