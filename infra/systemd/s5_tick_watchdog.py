#!/usr/bin/env python3
"""S5 자율 tick의 watchdog. 사람이 봐야 할 때만 말한다.

조용하지 않은 watchdog은 곧 무시된다. 그래서 NEEDS_HUMAN이거나 연속 무진척이 임계치를 넘을
때만 출력한다. 무진척 자체는 실패가 아니다. 그 주기에 할 일이 없었다는 뜻이다.

승인된 root의 상태 파일과 전이 이력만 읽는다. provider도 DB도 열지 않는다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

STATE_FILENAME = "state.json"
HISTORY_FILENAME = "state-history.jsonl"
DEFAULT_STALL_TICKS = 6


def main() -> int:
    parser = argparse.ArgumentParser(description="S5 tick watchdog")
    parser.add_argument("--root", required=True, help="approved S5 source root")
    parser.add_argument(
        "--stall-ticks",
        type=int,
        default=DEFAULT_STALL_TICKS,
        help="이 횟수만큼 연속으로 단계가 그대로면 알린다",
    )
    arguments = parser.parse_args()

    root = Path(arguments.root)
    runs = sorted(root.glob("run-*"))
    if not runs:
        print("S5_WATCHDOG=NO_RUN")
        return 1

    alerted = False
    for run_root in runs:
        state_path = run_root / STATE_FILENAME
        if not state_path.exists():
            continue
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print(f"S5_WATCHDOG=STATE_UNREADABLE run={run_root.name}")
            alerted = True
            continue
        phase = str(state.get("phase", ""))
        if phase == "NEEDS_HUMAN":
            print(
                f"S5_WATCHDOG=NEEDS_HUMAN run={run_root.name} "
                f"tick={state.get('tick')} outcome={state.get('lastOutcome')}"
            )
            alerted = True
            continue
        stalled = _trailing_same_phase(run_root / HISTORY_FILENAME)
        if stalled >= arguments.stall_ticks:
            print(
                f"S5_WATCHDOG=STALLED run={run_root.name} phase={phase} "
                f"consecutiveTicks={stalled}"
            )
            alerted = True
    return 1 if alerted else 0


def _trailing_same_phase(history: Path) -> int:
    """마지막 전이부터 거슬러 올라가며 단계가 그대로인 tick 수를 센다."""

    if not history.exists():
        return 0
    phases: list[str] = []
    try:
        with history.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                event = json.loads(line)
                phases.append(f"{event.get('fromPhase')}->{event.get('toPhase')}")
    except (OSError, ValueError):
        return 0
    if not phases:
        return 0
    last = phases[-1]
    if "->" in last:
        source, target = last.split("->", 1)
        if source != target:
            return 0
    count = 0
    for item in reversed(phases):
        if item != last:
            break
        count += 1
    return count


if __name__ == "__main__":
    sys.exit(main())
