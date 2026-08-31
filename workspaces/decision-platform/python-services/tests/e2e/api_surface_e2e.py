"""관통 테스트가 지나가지 않는 REST 표면을 실행 중인 스택에서 전부 한 번씩 태운다.

이 파일은 테스트 전용이다. production 이미지나 business logic에서 import 하지 않으며 `test_`
접두사가 아니라 pytest 수집 대상도 아니다.

무엇을 확인하나. `full_pipeline_e2e.py`는 `번들→신호→판단→주문→체결` 한 줄기만 지난다. 그
줄기 밖에 있는 소유자 표면 — 원칙 CRUD, 일지 CRUD, 동의, kill switch **쓰기**, 주문 판단
단건 조회와 감사, 대시보드 ViewModel 4종, ADMIN 관측 3종, 신호 조회 — 은 한 번도 스택에서
불린 적이 없다. 여기서 그것들을 부른다.

무엇을 확인하지 않나. 화면 렌더링(Playwright가 담당)과 v2 자동운용 상태기(관통 테스트가 담당).

정리. 이 runner가 만드는 것은 원칙·원칙버전·일지·동의·관측뿐이다. 시작 시 스냅샷을 찍고 끝에서
차집합만 지운다. kill switch는 켠 값을 반드시 원래대로 되돌린다. 정리에 실패하면 FAIL이다.

실행:
  P1_API_SURFACE_E2E=1 python -m tests.e2e.api_surface_e2e \\
    --out artifacts/decision-platform/e2e/api-surface.json
"""

from __future__ import annotations

import argparse
import sys
import uuid
from typing import Any, Final

from .full_pipeline_e2e import (
    quiesce_rival_portfolio_contexts,
    restore_portfolio_contexts,
    seed_risk_metrics,
)
from .harness import (
    Api,
    HarnessError,
    Recorder,
    cleanup,
    psql,
    require_opt_in,
    snapshot,
    write_report,
)

_OPT_IN: Final = "P1_API_SURFACE_E2E"
_SYMBOL: Final = "005930"


def _unknown_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """이 API의 목록 응답은 전부 `data.items`다. 한 곳에서만 그 모양을 안다."""

    data = payload.get("data")
    if isinstance(data, dict):
        items = data.get("items")
        return items if isinstance(items, list) else []
    return data if isinstance(data, list) else []


def check_system_and_admin(recorder: Recorder, owner: Api, admin: Api) -> None:
    """관측 표면. ADMIN 전용인 셋은 소유자에게 닫혀 있어야 한다."""

    health_status, health = owner.request("GET", "/api/v1/system/health")
    recorder.add(
        "시스템 상태",
        "PASS" if health_status == 200 and health else "FAIL",
        f"HTTP {health_status} keys={sorted(health.keys())[:6]}",
    )

    admin_paths = (
        "/api/v1/async-jobs",
        "/api/v1/stream-metrics",
        "/api/v1/artifacts/ingest-status",
    )
    owner_codes = [owner.request("GET", path)[0] for path in admin_paths]
    admin_codes = [admin.request("GET", path)[0] for path in admin_paths]
    recorder.add(
        "ADMIN 관측 3종",
        "PASS"
        if all(code == 403 for code in owner_codes) and admin_codes == [200, 200, 200]
        else "FAIL",
        f"소유자={owner_codes} 관리자={admin_codes} (소유자에게 닫히고 관리자에게 열려야 한다)",
    )

    job_status, _ = admin.request("GET", f"/api/v1/async-jobs/{_unknown_id('job')}")
    recorder.add(
        "없는 비동기 작업 조회",
        "PASS" if job_status == 404 else "FAIL",
        f"HTTP {job_status} (없는 식별자는 404로 닫혀야 한다)",
    )


def check_principles(recorder: Recorder, owner: Api) -> str | None:
    """원칙 생성·조회·수정·버전. 수정하면 버전이 하나 더 쌓여야 한다."""

    preset_status, presets = owner.request("GET", "/api/v1/principle-presets")
    preset_items = _items(presets)
    recorder.add(
        "원칙 프리셋 목록",
        "PASS" if preset_status == 200 and preset_items else "FAIL",
        f"HTTP {preset_status} 프리셋 {len(preset_items)}종",
    )
    if not preset_items:
        return None

    preset_id = preset_items[0].get("presetId") or preset_items[0].get("id")
    create_status, created = owner.request(
        "POST",
        "/api/v1/principles",
        {"presetId": preset_id, "title": "e2e 원칙", "mode": "GUIDE"},
        idempotency_key=_unknown_id("idem"),
    )
    principle = (created.get("data") or {}).get("principleId")
    recorder.add(
        "원칙 생성",
        "PASS" if create_status in (200, 201) and principle else "FAIL",
        f"HTTP {create_status} principleId={'있음' if principle else '없음'}",
    )
    if not principle:
        return None

    get_status, fetched = owner.request("GET", f"/api/v1/principles/{principle}")
    list_status, listed = owner.request("GET", "/api/v1/principles")
    listed_ids = [item.get("principleId") for item in _items(listed)]
    recorder.add(
        "원칙 단건·목록 조회",
        "PASS"
        if get_status == 200
        and (fetched.get("data") or {}).get("principleId") == principle
        and list_status == 200
        and principle in listed_ids
        else "FAIL",
        f"단건 HTTP {get_status} 목록 HTTP {list_status} 목록에 포함={principle in listed_ids}",
    )

    before = int(
        psql(f"select count(*) from public.principle_versions where principle_id = '{principle}';")
        or 0
    )
    current = fetched.get("data") or {}
    update_status, _ = owner.request(
        "PUT",
        f"/api/v1/principles/{principle}",
        {
            "title": "e2e 원칙 (수정)",
            "mode": current.get("mode") or "GUIDE",
            "status": "ACTIVE",
            "expectedVersion": current.get("version") or 1,
            # 규칙은 바꾸지 않는다. 이 단계가 보는 것은 수정이 버전을 하나 쌓는가이다.
            "rules": current.get("rules") or [],
        },
        idempotency_key=_unknown_id("idem"),
    )
    after = int(
        psql(f"select count(*) from public.principle_versions where principle_id = '{principle}';")
        or 0
    )
    versions_status, versions = owner.request("GET", f"/api/v1/principles/{principle}/versions")
    recorder.add(
        "원칙 수정과 버전 적재",
        "PASS"
        if update_status == 200
        and after == before + 1
        and versions_status == 200
        and len(_items(versions)) == after
        else "FAIL",
        f"HTTP {update_status} 버전 {before}→{after} 버전목록 HTTP {versions_status} "
        f"{len(_items(versions))}건",
    )
    return str(principle)


def check_journals(recorder: Recorder, owner: Api) -> None:
    """일지 생성·목록·수정·삭제. 삭제 후에는 목록에서 사라져야 한다."""

    create_status, created = owner.request(
        "POST",
        "/api/v1/journals",
        {
            "title": "e2e 일지",
            "content": "관통 밖 표면 확인",
            "tags": ["e2e"],
            # 링크는 전부 선택이지만 키 자체는 필수다. 아무 것에도 매달지 않은 일지를 만든다.
            "links": {},
        },
        idempotency_key=_unknown_id("idem"),
    )
    journal = (created.get("data") or {}).get("journalId")
    list_status, listed = owner.request("GET", "/api/v1/journals")
    present = journal in [item.get("journalId") for item in _items(listed)]
    recorder.add(
        "일지 생성과 목록",
        "PASS"
        if create_status in (200, 201) and journal and list_status == 200 and present
        else "FAIL",
        f"생성 HTTP {create_status} 목록 HTTP {list_status} 목록에 포함={present}",
    )
    if not journal:
        return

    created_journal = created.get("data") or {}
    patch_status, patched = owner.request(
        "PATCH",
        f"/api/v1/journals/{journal}",
        {
            "title": "e2e 일지 (수정)",
            "content": "관통 밖 표면 확인",
            "tags": ["e2e"],
            "links": {},
            "expectedVersion": created_journal.get("version") or 1,
        },
        idempotency_key=_unknown_id("idem"),
    )
    # 삭제도 낙관적 잠금을 요구한다. 수정으로 버전이 하나 올라갔으므로 그 값을 실어야 한다.
    delete_status, _ = owner.request(
        "DELETE",
        f"/api/v1/journals/{journal}",
        {"expectedVersion": (patched.get("data") or {}).get("version") or 2},
        idempotency_key=_unknown_id("idem"),
    )
    _, after_delete = owner.request("GET", "/api/v1/journals")
    gone = journal not in [item.get("journalId") for item in _items(after_delete)]
    recorder.add(
        "일지 수정과 삭제",
        "PASS"
        if patch_status == 200
        and (patched.get("data") or {}).get("title") == "e2e 일지 (수정)"
        and delete_status in (200, 204)
        and gone
        else "FAIL",
        f"수정 HTTP {patch_status} 삭제 HTTP {delete_status} 목록에서 사라짐={gone}",
    )


def check_consents(recorder: Recorder, owner: Api) -> None:
    status, _ = owner.request(
        "POST",
        "/api/v1/consents",
        {
            "consentType": "EXTERNAL_AI_RAG_V1",
            "policyVersion": "EXTERNAL_AI_RAG_V1",
            "action": "GRANT",
        },
        idempotency_key=_unknown_id("idem"),
    )
    recorder.add(
        "v1 동의 기록",
        "PASS" if status in (200, 201, 204) else "FAIL",
        f"HTTP {status}",
    )


def check_kill_switch(recorder: Recorder, owner: Api, admin: Api) -> None:
    """켠 뒤 반드시 원래대로 되돌린다. 되돌리지 못하면 FAIL이다.

    비대칭이 이 단계의 요점이다. **켜는 것은 소유자가 할 수 있고 끄는 것은 ADMIN만 할 수 있다.**
    사용자가 스스로 멈출 수는 있어도 스스로 다시 열 수는 없다는 뜻이다. 소유자가 끄려 하면
    403이어야 하고, 그 상태에서 모든 주문 판단은 `RISK_BLOCKED`로 닫힌다.
    """

    read_status, before = owner.request("GET", "/api/v1/risk/kill-switch")
    initial = (before.get("data") or {}).get("active")
    on_status, _ = owner.request(
        "POST",
        "/api/v1/risk/kill-switch",
        {"active": True, "reason": "e2e 표면 확인"},
        idempotency_key=_unknown_id("idem"),
    )
    _, engaged_body = owner.request("GET", "/api/v1/risk/kill-switch")
    engaged = (engaged_body.get("data") or {}).get("active")
    owner_off_status, _ = owner.request(
        "POST",
        "/api/v1/risk/kill-switch",
        {"active": False, "reason": "소유자는 스스로 열 수 없어야 한다"},
        idempotency_key=_unknown_id("idem"),
    )
    off_status, _ = admin.request(
        "POST",
        "/api/v1/risk/kill-switch",
        {"active": False, "reason": "e2e 표면 확인 복구"},
        idempotency_key=_unknown_id("idem"),
    )
    _, restored_body = owner.request("GET", "/api/v1/risk/kill-switch")
    restored = (restored_body.get("data") or {}).get("active")
    recorder.add(
        "kill switch 읽기·켜기·되돌리기",
        "PASS"
        if read_status == 200
        and on_status in (200, 204)
        and engaged is True
        and owner_off_status == 403
        and off_status in (200, 204)
        and restored == initial
        else "FAIL",
        f"초기={initial} 켠뒤={engaged} 소유자해제={owner_off_status} 관리자해제={off_status} "
        f"복구={restored} (복구값이 초기값과 달라지면 회귀다)",
    )


def check_risk_and_signals(recorder: Recorder, owner: Api) -> None:
    portfolio_status, portfolio = owner.request("GET", "/api/v1/risk/portfolio")
    recorder.add(
        "포트폴리오 위험 조회",
        "PASS" if portfolio_status == 200 else "FAIL",
        f"HTTP {portfolio_status} keys={sorted((portfolio.get('data') or {}).keys())[:6]}",
    )
    signal_status, signal = owner.request("GET", f"/api/v2/signals/{_SYMBOL}")
    recorder.add(
        "신호 조회",
        "PASS" if signal_status in (200, 404) else "FAIL",
        f"HTTP {signal_status} (번들이 없으면 404가 맞다. 5xx면 회귀다) code={signal.get('code')}",
    )


def check_decisions(recorder: Recorder, owner: Api, principle: str | None) -> str | None:
    """주문 판단을 실제로 한 번 만들고 단건·감사까지 읽는다."""

    if principle is None:
        recorder.add("주문 판단 생성", "FAIL", "원칙이 없어 판단을 요청할 수 없다")
        return None
    price = 70_100
    quantity = 1
    status, evaluated = owner.request(
        "POST",
        "/api/v1/decisions/evaluate-order",
        {
            "principleId": principle,
            "portfolioSource": "KIS_MOCK",
            "orderIntent": {
                "symbol": _SYMBOL,
                "side": "BUY",
                "quantity": quantity,
                "orderType": "LIMIT",
                "estimatedPrice": price,
                "estimatedAmount": price * quantity,
                "strategyId": "p1-e2e-api-surface",
                "timeframe": "1d",
            },
        },
        idempotency_key=_unknown_id("idem"),
    )
    data = evaluated.get("data") or {}
    decision = data.get("decisionId")
    recorder.add(
        "주문 판단 생성",
        "PASS" if status in (200, 201) and decision else "FAIL",
        f"HTTP {status} verdict={data.get('verdict')} decisionId={'있음' if decision else '없음'} "
        f"code={evaluated.get('code')}",
    )
    if not decision:
        return None

    get_status, fetched = owner.request("GET", f"/api/v1/decisions/{decision}")
    audit_status, audit = owner.request("GET", f"/api/v1/decisions/{decision}/audit")
    recorder.add(
        "판단 단건과 감사 조회",
        "PASS"
        if get_status == 200
        and (fetched.get("data") or {}).get("decisionId") == decision
        and audit_status == 200
        and (audit.get("data") or {})
        else "FAIL",
        f"단건 HTTP {get_status} 감사 HTTP {audit_status}",
    )
    unknown_status, _ = owner.request("GET", f"/api/v1/decisions/{_unknown_id('dec')}")
    recorder.add(
        "없는 판단 조회",
        "PASS" if unknown_status == 404 else "FAIL",
        f"HTTP {unknown_status} (다른 소유자의 판단도 같은 404로 닫혀야 한다)",
    )
    return str(decision)


def check_dashboard_view_models(recorder: Recorder, owner: Api, decision: str | None) -> None:
    """ViewModel 4종. 있으면 200, 없으면 404다. 어느 쪽이든 5xx는 회귀다."""

    # 식별자 형식은 각 ViewModel이 검사한다. 형식이 틀리면 400이 맞고, 그건 이 단계가 보려는
    # 것이 아니다. 그래서 형식이 맞는 없는 식별자를 만든다.
    targets: list[tuple[str, str, tuple[int, ...]]] = [
        ("백테스트", "/api/v1/dashboard/backtests/run_" + uuid.uuid4().hex, (404,)),
        ("모델 평가", "/api/v1/dashboard/model-evaluations/run_" + uuid.uuid4().hex, (404,)),
        ("RAG 출처", "/api/v1/dashboard/rag-sources/rag_ans_" + uuid.uuid4().hex, (404,)),
    ]
    if decision:
        targets.append(("위험 결과", f"/api/v1/dashboard/risk-results/{decision}", (200, 404)))
    observed = []
    ok = True
    for name, path, allowed in targets:
        status, _ = owner.request("GET", path)
        observed.append(f"{name}={status}")
        ok = ok and status in allowed
    recorder.add(
        "대시보드 ViewModel 4종",
        "PASS" if ok else "FAIL",
        " ".join(observed) + " (없는 식별자는 404, 만든 판단은 200 또는 404)",
    )


def check_rag_v1(recorder: Recorder, owner: Api) -> None:
    """v1 RAG는 v2와 무관하게 계속 살아 있어야 한다."""

    sources_status, sources = owner.request("GET", "/api/v1/rag/sources")
    history_status, history = owner.request("GET", "/api/v1/rag/history")
    recorder.add(
        "v1 RAG 출처·이력",
        "PASS" if sources_status == 200 and history_status == 200 else "FAIL",
        f"출처 HTTP {sources_status} {len(_items(sources))}건 "
        f"이력 HTTP {history_status} {len(_items(history))}건",
    )


def main(argv: list[str]) -> int:
    require_opt_in(_OPT_IN)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv[1:])

    recorder = Recorder()
    before: dict[str, list[str]] = {}
    rivals: list[str] = []
    try:
        before = snapshot()
        owner = Api()
        owner.login("demo-user")
        admin = Api()
        admin.login("demo-admin")
        check_system_and_admin(recorder, owner, admin)
        principle = check_principles(recorder, owner)
        check_journals(recorder, owner)
        check_consents(recorder, owner)
        # kill switch를 먼저 본다. 켜진 채로 남으면 아래 주문 판단이 전부 RISK_BLOCKED가 되어
        # 원인이 제품이 아니라 이 runner가 된다.
        check_kill_switch(recorder, owner, admin)
        # 판단은 지표가 있어야 성립한다. 관통 테스트와 같은 오프라인 수집 writer로 넣는다.
        rivals = quiesce_rival_portfolio_contexts()
        seed_risk_metrics()
        check_risk_and_signals(recorder, owner)
        decision = check_decisions(recorder, owner, principle)
        check_dashboard_view_models(recorder, owner, decision)
        check_rag_v1(recorder, owner)
    except HarnessError as error:
        recorder.add("확인 중단", "FAIL", str(error))
    except Exception as error:  # noqa: BLE001 - 어떤 실패든 정리는 반드시 돈다
        recorder.add("확인 중단", "FAIL", f"{type(error).__name__}: {error}")
    finally:
        restore_portfolio_contexts(rivals)
        if before:
            cleanup(before, recorder)
        else:
            recorder.add("정리", "FAIL", "스냅샷을 찍지 못해 되돌릴 범위를 알 수 없다")

    report = write_report(
        contract_id="p1-api-surface-e2e.v1",
        marker="P1_API_SURFACE_E2E",
        recorder=recorder,
        out=args.out,
    )
    return 1 if report["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
