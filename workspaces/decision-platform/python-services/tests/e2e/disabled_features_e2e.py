"""꺼져 있어야 하는 것이 실제로 닫혀 있는지 실행 중인 스택에서 확인한다.

이 파일은 테스트 전용이다. production 이미지나 business logic에서 import 하지 않으며 `test_`
접두사가 아니라 pytest 수집 대상도 아니다.

왜 이것이 테스트인가. "안 켰다"는 설정 문장이고, "닫혀 있다"는 관측이다. 둘은 다르다. 설정만
보고 안심하면 조용한 fallback이 생겨도 알 수 없다. 여기서는 **설정과 그 설정이 만드는 결과를
같이** 본다.

무엇을 확인하나.
  1. 네 기능의 런타임 플래그가 실제로 `false`다 — SearXNG 웹검색, S4.9 Strong LLM, S4.9 MCP,
     금융공학 gRPC.
  2. 그 넷이 root API 표면에 없다. 꺼진 기능이 endpoint만 남아 있으면 언젠가 열린다.
     예외는 `/api/v2/strong-llm/settings` 하나다. 이름은 닮았지만 꺼진 기능이 아니라 어떤
     모델을 쓸지 고르는 설정이고, 그 선택은 언제나 열려 있어야 한다.
  3. LightGBM은 은퇴다. V74가 세 역할에서 stage/activate/publish 권한을 회수했고 지금도 0이다.
  4. 자동운용 브로커리지는 모의다. 실계좌 주문 경로가 배포에 열려 있지 않다.
  5. Vertex 뉴스 거부권 전송이 provider 호출 전에 닫힌다.

무엇을 확인하지 않나. 켜면 동작하는지는 보지 않는다. 그 넷은 릴리스 범위 밖으로 설계된 것이지
배선을 안 한 것이 아니다.

실행:
  P1_DISABLED_FEATURES_E2E=1 python -m tests.e2e.disabled_features_e2e \\
    --out artifacts/decision-platform/e2e/disabled-features.json
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Final

from .harness import (
    Api,
    HarnessError,
    Recorder,
    platform,
    psql,
    require_opt_in,
    write_report,
)

_OPT_IN: Final = "P1_DISABLED_FEATURES_E2E"
_FLAGS: Final = (
    ("RAG_WEB_ENABLED", "SearXNG 웹검색"),
    ("S4_9_STRONG_LLM_ENABLED", "S4.9 Strong LLM"),
    ("S4_9_MCP_ENABLED", "S4.9 MCP"),
    ("FINANCIAL_ENGINEERING_GRPC_ENABLED", "금융공학 gRPC"),
)
# 이름에 `strong-llm`이 들어가지만 꺼진 기능이 아닌 표면이다.
#
# S4.9 agent 자체는 여전히 꺼져 있고 그 endpoint도 없다. 이것은 **어떤 모델을 쓸지 고르는**
# 설정 쓰기이고, 언제나 열려 있어야 한다 - provider를 바꾸는 길이 배포 환경변수뿐이면 그
# 선택이 사용자의 것이 아니라 운영자의 것이 된다. 그래서 여기서만 예외로 둔다. 목록으로 두는
# 이유는 접두사로 열어 두면 나중에 진짜 agent endpoint가 같은 접두사로 들어와도 통과하기
# 때문이다.
_ALWAYS_PRESENT_PATHS: Final = frozenset({"/api/v2/strong-llm/settings"})
# 은퇴한 LightGBM 경로. V74가 회수한 권한이 다시 생기면 그 자체가 회귀다.
_RETIRED_GRANTS: Final = (
    ("decision_signal_writer", "stage_signal_model_release"),
    ("decision_signal_writer", "stage_signal_batch"),
    ("decision_signal_scheduler", "publish_active_signal_batch"),
    ("decision_signal_admin", "activate_signal_model_and_batch"),
)


def check_flags(recorder: Recorder) -> None:
    names = [name for name, _ in _FLAGS]
    observed = platform(
        "for name in " + " ".join(names) + '; do printf "%s\\n" "$(printenv "$name")"; done'
    ).splitlines()
    values = dict(zip(names, observed + [""] * len(names), strict=False))
    off = [label for name, label in _FLAGS if values.get(name) != "false"]
    recorder.add(
        "네 기능의 런타임 플래그",
        "PASS" if not off else "FAIL",
        ", ".join(f"{label}={values.get(name) or '<unset>'}" for name, label in _FLAGS)
        + (f" / 꺼져 있지 않은 것: {off}" if off else ""),
    )


def check_surface_is_absent(recorder: Recorder) -> None:
    """꺼진 기능이 API 표면에 없어야 한다. 있으면 설정 하나로 열린다."""

    try:
        with urllib.request.urlopen("http://127.0.0.1:18080/v3/api-docs", timeout=60) as response:
            spec = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise HarnessError(f"openapi unreachable: {error}") from error
    paths = list(spec.get("paths") or {})
    forbidden = [
        path
        for path in paths
        if any(
            token in path
            for token in ("/mcp", "/strong-llm", "/financial-engineering", "/web-search")
        )
        and path not in _ALWAYS_PRESENT_PATHS
    ]
    recorder.add(
        "꺼진 기능은 표면에 없다",
        "PASS" if not forbidden else "FAIL",
        f"operation 경로 {len(paths)}개 중 해당 없음={not forbidden} {forbidden[:4]}",
    )


def check_lightgbm_is_retired(recorder: Recorder) -> None:
    conditions = " or ".join(
        f"(grantee = '{role}' and routine_name = '{routine}')" for role, routine in _RETIRED_GRANTS
    )
    granted = psql(
        "select coalesce(string_agg(distinct grantee || ':' || routine_name, ', '), '')"
        f" from information_schema.routine_privileges where {conditions};"
    )
    recorder.add(
        "LightGBM 은퇴 유지",
        "PASS" if granted == "" else "FAIL",
        f"회수됐어야 할 권한 중 남아 있는 것=[{granted}] "
        "(V74가 회수했다. 다시 생기면 은퇴가 풀린 것이다)",
    )


def check_brokerage_is_mock(recorder: Recorder, owner: Api) -> None:
    status, payload = owner.request("GET", "/api/v2/automation/status")
    data = payload.get("data") or {}
    mode = data.get("brokerageMode") or (data.get("control") or {}).get("brokerageMode")
    row = psql(
        "select coalesce(string_agg(distinct brokerage_mode, ','), '(없음)')"
        " from public.automation_control;"
    )
    recorder.add(
        "브로커리지는 모의다",
        "PASS"
        if status == 200 and row.strip() in ("KIS_MOCK", "INTERNAL_PAPER", "(없음)")
        else "FAIL",
        f"status HTTP {status} 응답 모드={mode} DB 모드={row} "
        "(실계좌 모드가 나타나면 그 자체가 금지 위반이다)",
    )


def check_news_veto_transport(recorder: Recorder) -> None:
    from app.p1_owner.automation_runtime_live import (  # noqa: PLC0415 - 확인 대상 자체다
        FailClosedVertexVetoTransport,
    )
    from app.p1_owner.vertex_veto import VertexBudgetExhausted  # noqa: PLC0415

    transport = FailClosedVertexVetoTransport()
    closed = False
    try:
        transport.invoke(system_prompt="", request_bytes=b"")
    except VertexBudgetExhausted:
        closed = True
    recorder.add(
        "뉴스 거부권 전송 fail-closed",
        "PASS" if closed and transport.physical_calls == 0 else "FAIL",
        f"닫힘={closed} 물리 호출={transport.physical_calls}",
    )


def main(argv: list[str]) -> int:
    require_opt_in(_OPT_IN)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv[1:])

    recorder = Recorder()
    try:
        owner = Api()
        owner.login("demo-user")
        check_flags(recorder)
        check_surface_is_absent(recorder)
        check_lightgbm_is_retired(recorder)
        check_brokerage_is_mock(recorder, owner)
        check_news_veto_transport(recorder)
    except HarnessError as error:
        recorder.add("확인 중단", "FAIL", str(error))
    except Exception as error:  # noqa: BLE001
        recorder.add("확인 중단", "FAIL", f"{type(error).__name__}: {error}")

    # 이 runner는 아무 것도 만들지 않는다. 읽기만 하므로 정리할 것이 없다.
    report = write_report(
        contract_id="p1-disabled-features-e2e.v1",
        marker="P1_DISABLED_FEATURES_E2E",
        recorder=recorder,
        out=args.out,
    )
    return 1 if report["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
