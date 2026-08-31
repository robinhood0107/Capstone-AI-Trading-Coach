"""RAG v1 표면을 실행 중인 스택에서 한 바퀴 돌리고, v2와 나란히 사는지 본다.

이 파일은 테스트 전용이다. production 이미지나 business logic에서 import 하지 않으며 `test_`
접두사가 아니라 pytest 수집 대상도 아니다.

무엇을 확인하나.
  1. v1 `ask`가 답을 만들고 이력에 남는다.
  2. 이력 목록·단건·삭제가 이어진다. 삭제한 답은 다시 읽히지 않는다.
  3. 답변 피드백이 기록된다.
  4. `sources`가 열린다.
  5. v2를 켠 상태에서도 v1이 그대로 산다. 두 이력이 서로 섞이지 않는다.

무엇을 확인하지 않나. v2 검색·생성 경계는 `rag_v2_boundaries.py`가 담당한다.

정리. 이 runner가 만드는 답변은 스스로 지운다. 지우지 못한 것은 스냅샷 차집합으로 되돌린다.

실행:
  P1_RAG_V1_E2E=1 python -m tests.e2e.rag_v1_e2e \\
    --out artifacts/decision-platform/e2e/rag-v1.json
"""

from __future__ import annotations

import argparse
import sys
import uuid
from typing import Any, Final

from .harness import (
    Api,
    HarnessError,
    OWNER,
    Recorder,
    cleanup,
    psql,
    require_opt_in,
    snapshot,
    write_report,
)

_OPT_IN: Final = "P1_RAG_V1_E2E"
_QUESTION: Final = "분산투자가 위험을 줄이는 이유를 근거와 함께 설명해 주세요."


def _key() -> str:
    return f"idem_{uuid.uuid4().hex}"


def _items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, dict):
        items = data.get("items")
        return items if isinstance(items, list) else []
    return data if isinstance(data, list) else []


def _v1_history_count() -> int:
    return int(
        psql(f"select count(*) from public.rag_answer_history where owner_user_id = '{OWNER}';")
        or 0
    )


def check_ask_and_history(recorder: Recorder, owner: Api) -> str | None:
    before = _v1_history_count()
    status, answered = owner.request(
        "POST",
        "/api/v1/rag/ask",
        {"question": _QUESTION, "answerMode": "CONCISE", "topics": [], "relatedSymbols": []},
        idempotency_key=_key(),
    )
    data = answered.get("data") or {}
    answer_id = data.get("answerId")
    after = _v1_history_count()
    recorder.add(
        "v1 질문과 이력 적재",
        "PASS" if status == 200 and answer_id and after == before + 1 else "FAIL",
        f"HTTP {status} answerId={'있음' if answer_id else '없음'} "
        f"상태={data.get('generationStatus')} 이력 {before}→{after} "
        f"code={(answered.get('error') or {}).get('code')}",
    )
    return str(answer_id) if answer_id else None


def check_history_read_and_delete(recorder: Recorder, owner: Api, answer: str | None) -> None:
    if answer is None:
        recorder.add("v1 이력 조회와 삭제", "FAIL", "삭제할 답변이 없다")
        return
    list_status, listed = owner.request("GET", "/api/v1/rag/history?limit=20")
    present = answer in [item.get("answerId") for item in _items(listed)]
    single_status, single = owner.request("GET", f"/api/v1/rag/history/{answer}")
    delete_status, _ = owner.request("DELETE", f"/api/v1/rag/history/{answer}")
    after_status, _ = owner.request("GET", f"/api/v1/rag/history/{answer}")
    recorder.add(
        "v1 이력 조회와 삭제",
        "PASS"
        if list_status == 200
        and present
        and single_status == 200
        and (single.get("data") or {}).get("answerId") == answer
        and delete_status in (200, 204)
        and after_status == 404
        else "FAIL",
        f"목록 HTTP {list_status} 포함={present} 단건 HTTP {single_status} "
        f"삭제 HTTP {delete_status} 삭제후 HTTP {after_status}",
    )


def check_feedback(recorder: Recorder, owner: Api) -> None:
    """피드백은 살아 있는 답변에만 달린다. 그래서 답변을 하나 더 만든다."""

    status, answered = owner.request(
        "POST",
        "/api/v1/rag/ask",
        {"question": _QUESTION, "answerMode": "CONCISE", "topics": [], "relatedSymbols": []},
        idempotency_key=_key(),
    )
    answer = (answered.get("data") or {}).get("answerId")
    if status != 200 or not answer:
        recorder.add("v1 답변 피드백", "FAIL", f"피드백 대상 생성 실패 HTTP {status}")
        return
    feedback_status, body = owner.request(
        "POST", f"/api/v1/rag/answers/{answer}/feedback", {"helpful": True}
    )
    _, single = owner.request("GET", f"/api/v1/rag/history/{answer}")
    stored = (single.get("data") or {}).get("helpful")
    owner.request("DELETE", f"/api/v1/rag/history/{answer}")
    recorder.add(
        "v1 답변 피드백",
        "PASS" if feedback_status in (200, 204) and stored is True else "FAIL",
        f"HTTP {feedback_status} 저장된 값={stored} code={(body.get('error') or {}).get('code')}",
    )


def check_sources(recorder: Recorder, owner: Api) -> None:
    status, sources = owner.request("GET", "/api/v1/rag/sources")
    recorder.add(
        "v1 출처 목록",
        "PASS" if status == 200 else "FAIL",
        f"HTTP {status} {len(_items(sources))}건 (등록된 출처가 0건인 것과 열리지 않는 것은 다르다)",
    )


def check_v1_and_v2_are_separate(recorder: Recorder, owner: Api) -> None:
    """v2를 켠 상태에서도 v1이 살아 있고, 두 이력이 섞이지 않아야 한다."""

    v1_status, v1 = owner.request("GET", "/api/v1/rag/history?limit=5")
    v2_status, v2 = owner.request("GET", "/api/v2/rag/history?limit=5")
    v1_ids = {item.get("answerId") for item in _items(v1)}
    v2_ids = {item.get("answerId") for item in _items(v2)}
    overlap = v1_ids & v2_ids
    recorder.add(
        "v1과 v2 이력 분리",
        "PASS" if v1_status == 200 and v2_status == 200 and not overlap else "FAIL",
        f"v1 HTTP {v1_status} {len(v1_ids)}건 v2 HTTP {v2_status} {len(v2_ids)}건 "
        f"겹침={len(overlap)}건 (겹치면 두 이력이 한 표를 쓰고 있다는 뜻이다)",
    )


def main(argv: list[str]) -> int:
    require_opt_in(_OPT_IN)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv[1:])

    recorder = Recorder()
    before: dict[str, list[str]] = {}
    try:
        before = snapshot()
        owner = Api()
        owner.login("demo-user")
        answer = check_ask_and_history(recorder, owner)
        check_history_read_and_delete(recorder, owner, answer)
        check_feedback(recorder, owner)
        check_sources(recorder, owner)
        check_v1_and_v2_are_separate(recorder, owner)
    except HarnessError as error:
        recorder.add("확인 중단", "FAIL", str(error))
    except Exception as error:  # noqa: BLE001 - 어떤 실패든 정리는 반드시 돈다
        recorder.add("확인 중단", "FAIL", f"{type(error).__name__}: {error}")
    finally:
        if before:
            cleanup(before, recorder)
        else:
            recorder.add("정리", "FAIL", "스냅샷을 찍지 못해 되돌릴 범위를 알 수 없다")

    report = write_report(
        contract_id="p1-rag-v1-e2e.v1",
        marker="P1_RAG_V1_E2E",
        recorder=recorder,
        out=args.out,
    )
    return 1 if report["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
