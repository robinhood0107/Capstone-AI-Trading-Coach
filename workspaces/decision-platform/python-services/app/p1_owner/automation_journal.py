"""운영자가 status를 직접 들여다보지 않아도 되도록, 알아야 할 순간만 로컬로 내보낸다.

외부로 보내지 않는다. 이 레포는 loopback bind와 provider-free 기본값을 유지하고 있고 공급망·보안
gate가 아직 열려 있어, webhook 하나를 붙이는 것이 곧 새 outbound 경로와 저장된 credential을
만드는 일이 된다. 그래서 파일과 stderr로만 남긴다.

민감값은 쓰지 않는다. 계좌번호, credential, 원문 payload는 들어가지 않고 이미 sanitized된
automation event의 사실만 옮긴다.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

# 운영자가 즉시 알아야 하는 것만 고른다. 나머지는 runs 조회로 충분하다.
_NOTABLE: Final = frozenset({"RUN_HALTED", "DRIFT_DETECTED", "ORDER_OUTCOME_RECORDED"})
_MAX_LINE_BYTES: Final = 4096


@dataclass(frozen=True, slots=True)
class JournalNotice:
    """한 줄로 남길 사실. 값은 전부 이미 공개 가능한 것들이다."""

    occurred_at: str
    run_id: str
    session_date: str
    event_type: str
    state: str
    reason: str | None

    def line(self) -> str:
        payload = {
            "eventType": self.event_type,
            "occurredAt": self.occurred_at,
            "reason": self.reason,
            "runId": self.run_id,
            "sessionDate": self.session_date,
            "state": self.state,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class AutomationJournal:
    """append-only 로컬 저널. 실패해도 tick을 죽이지 않는다."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path

    @classmethod
    def from_environment(cls) -> AutomationJournal:
        raw = os.environ.get("P1_AUTOMATION_JOURNAL_FILE", "").strip()
        return cls(Path(raw) if raw else None)

    def notify(self, notice: JournalNotice) -> None:
        if notice.event_type not in _NOTABLE:
            return
        line = notice.line()
        if len(line.encode("utf-8")) > _MAX_LINE_BYTES:
            return
        # stderr는 항상 나간다. 파일은 설정했을 때만 쓴다.
        print(line, file=sys.stderr, flush=True)
        if self._path is None:
            return
        try:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            # 저널은 관측 편의지 안전장치가 아니다. 쓰지 못해도 운용은 계속한다.
            return


def notice_from_event(
    event: dict[str, object],
    *,
    run_id: str,
    session_date: str,
    state: str,
) -> JournalNotice:
    payload = event.get("payload")
    reason = None
    if isinstance(payload, dict):
        raw = payload.get("reason")
        if isinstance(raw, str):
            reason = raw
    return JournalNotice(
        occurred_at=datetime.now(tz=UTC).isoformat(),
        run_id=run_id,
        session_date=session_date,
        event_type=str(event.get("eventType", "")),
        state=state,
        reason=reason,
    )
