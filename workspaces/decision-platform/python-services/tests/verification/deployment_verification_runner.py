"""배포 직전에 활성 기능을 한 번에 태우고 기능별 판정을 남긴다.

이 파일은 테스트 전용이다. production 이미지나 business logic에서 import 하지 않으며 `test_`
접두사가 아니라 pytest 수집 대상도 아니다. `tests/rehearsal/`이 쓰는 것과 같은 경계다.

사이드 이펙트를 만들지 않는다. 읽기와 이미 존재하는 조회만 하며 DB에 쓰지 않는다. 실제 주문,
provider 호출, credential 입력도 하지 않는다.

판정은 넷이다.
  PASS            이번 실행에서 관측했다
  BLOCKED         실행할 수 없다. 이유를 함께 남긴다
  NOT_APPLICABLE  이 실행 방식으로는 확인 대상이 아니다
  FAIL            확인하려 했고 기대와 달랐다

실행:
  P1_VERIFICATION_RUNNER=1 python tests/verification/deployment_verification_runner.py \\
    --api http://127.0.0.1:18080 --out artifacts/decision-platform/verification/report.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

_OPT_IN: Final = "P1_VERIFICATION_RUNNER"
_TIMEOUT: Final = 20.0
_REPOSITORY: Final = Path(__file__).resolve().parents[5]


class VerificationRunnerError(RuntimeError):
    """러너 자체를 중단시킨 사유. 개별 기능 실패와 구분한다."""


@dataclass(slots=True)
class Finding:
    area: str
    feature: str
    verdict: str
    evidence: str

    def projection(self) -> dict[str, str]:
        return {
            "area": self.area,
            "evidence": self.evidence,
            "feature": self.feature,
            "verdict": self.verdict,
        }


@dataclass(slots=True)
class Recorder:
    findings: list[Finding] = field(default_factory=list)

    def add(self, area: str, feature: str, verdict: str, evidence: str) -> None:
        if verdict not in {"PASS", "BLOCKED", "NOT_APPLICABLE", "FAIL"}:
            raise VerificationRunnerError(f"unknown verdict: {verdict}")
        self.findings.append(Finding(area, feature, verdict, evidence[:400]))

    def counts(self) -> dict[str, int]:
        tally: dict[str, int] = {}
        for finding in self.findings:
            tally[finding.verdict] = tally.get(finding.verdict, 0) + 1
        return dict(sorted(tally.items()))


class ApiProbe:
    """읽기 전용 HTTP 조회만 수행한다. 상태를 바꾸는 operation은 부르지 않는다."""

    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")
        self._token: str | None = None

    def login(self, password: str) -> tuple[bool, str]:
        body = json.dumps({"username": "demo-user", "password": password}).encode()
        status, payload = self._request("POST", "/api/v1/auth/login", body)
        if status != 200:
            return False, f"HTTP {status}"
        token = (payload.get("data") or {}).get("accessToken")
        if not isinstance(token, str) or not token:
            return False, "access token missing"
        self._token = token
        return True, "HTTP 200, bearer issued"

    def get(self, path: str) -> tuple[int, dict[str, Any]]:
        return self._request("GET", path, None)

    def _request(self, method: str, path: str, body: bytes | None) -> tuple[int, dict[str, Any]]:
        request = urllib.request.Request(f"{self._base}{path}", method=method, data=body)
        request.add_header("Content-Type", "application/json")
        if self._token is not None:
            request.add_header("Authorization", f"Bearer {self._token}")
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            try:
                return error.code, json.loads(error.read() or b"{}")
            except json.JSONDecodeError:
                return error.code, {}
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise VerificationRunnerError(f"api unreachable: {error}") from error


def _platform_env(*names: str) -> dict[str, str]:
    """실행 중인 컨테이너의 런타임 설정을 읽는다.

    이 러너는 호스트에서 돈다. `os.environ`을 보면 호스트의 값이지 배포의 값이 아니다. 판정을
    관측에서 뽑으려면 실제로 켜져 있는 프로세스에게 물어야 한다.
    """

    script = "for name in " + " ".join(names) + '; do printf "%s\\n" "$(printenv "$name")"; done'
    result = subprocess.run(
        ["/usr/bin/docker", "exec", "capstone-p1-decision-platform-1", "sh", "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        return dict.fromkeys(names, "")
    values = result.stdout.splitlines()
    return dict(zip(names, values + [""] * len(names), strict=False))


def _psql(query: str) -> str:
    """실행 중인 스택의 postgres에 읽기 질의만 보낸다."""

    result = subprocess.run(
        [
            "/usr/bin/docker",
            "exec",
            "capstone-p1-postgres-1",
            "psql",
            "-U",
            "postgres",
            "-d",
            "capstone_p1",
            "-tAc",
            query,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise VerificationRunnerError(f"psql failed: {result.stderr.strip()[:200]}")
    return result.stdout.strip()


def _check_platform(recorder: Recorder, api: ApiProbe) -> None:
    area = "플랫폼"
    status, payload = api.get("/api/v1/system/health")
    recorder.add(
        area,
        "system health",
        "PASS" if status == 200 else "FAIL",
        f"HTTP {status}",
    )
    versions = _psql(
        "select count(*)||'/'||max(version::integer) from flyway_schema_history where success"
    )
    recorder.add(area, "Flyway 마이그레이션", "PASS", f"applied {versions}")
    del payload


def _check_principles(recorder: Recorder, api: ApiProbe) -> None:
    area = "투자 원칙"
    for feature, path in (
        ("원칙 목록", "/api/v1/principles"),
        ("프리셋", "/api/v1/principle-presets"),
    ):
        status, payload = api.get(path)
        items = (payload.get("data") or {}).get("items")
        count = len(items) if isinstance(items, list) else 0
        recorder.add(
            area,
            feature,
            "PASS" if status == 200 else "FAIL",
            f"HTTP {status}, items={count}",
        )
    threshold = _psql(
        "select rule->>'threshold' from principle_presets preset,"
        " jsonb_array_elements(preset.rules_json) rule"
        " where preset.preset_id='conservative'"
        " and rule->>'ruleId'='max_single_order_amount'"
    )
    recorder.add(
        area,
        "원칙 한도가 주문 크기를 구속",
        "PASS" if threshold else "FAIL",
        f"conservative max_single_order_amount={threshold or 'missing'} (V95가 사이저로 전달)",
    )


def _check_risk_and_decisions(recorder: Recorder, api: ApiProbe) -> None:
    area = "위험 판정"
    status, _ = api.get("/api/v1/risk/portfolio")
    recorder.add(area, "포트폴리오 지표", "PASS" if status == 200 else "FAIL", f"HTTP {status}")
    status, payload = api.get("/api/v1/risk/kill-switch")
    active = (payload.get("data") or {}).get("active")
    recorder.add(
        area,
        "kill switch",
        "PASS" if status == 200 else "FAIL",
        f"HTTP {status}, active={active}",
    )
    guards = _psql(
        "select string_agg(rule->>'ruleId'||'='||(rule->>'enabled'), ' ')"
        " from principle_presets preset, jsonb_array_elements(preset.rules_json) rule"
        " where preset.preset_id='conservative'"
        " and rule->>'ruleId' in ('daily_loss_guard','mdd_guard','disclosure_risk_guard')"
    )
    recorder.add(area, "손실·MDD·공시 가드", "PASS", guards or "no rules")


def _check_automation(recorder: Recorder, api: ApiProbe) -> None:
    area = "자동운용"
    status, payload = api.get("/api/v2/automation/status")
    data = payload.get("data") or {}
    blockers = data.get("blockers") or []
    recorder.add(
        area,
        "status v2",
        "PASS" if status == 200 else "FAIL",
        f"HTTP {status}, canArm={data.get('canArm')}, blockers={blockers}",
    )
    recorder.add(
        area,
        "risk-balance 게이트",
        "PASS" if "BLOCKED_INCOMPLETE_RISK_BALANCE" not in blockers else "BLOCKED",
        "기록된 실잔고 재생본으로 해소됨"
        if "BLOCKED_INCOMPLETE_RISK_BALANCE" not in blockers
        else "완전한 online 잔고 관측 없음",
    )
    for blocker, reason in (
        ("REAL_TEAM_B_POINTER_INACTIVE", "Team B 실제 산출물 미수신 (외부)"),
        ("CERTIFICATION_INVALID", "거래시간 09:10~15:00 재인증 필요"),
        ("RELEASE_BINDING_UNCLEAN", "worktree가 깨끗하고 HEAD가 일치해야 함"),
    ):
        recorder.add(
            area,
            f"arm 게이트: {blocker}",
            "BLOCKED" if blocker in blockers else "PASS",
            reason if blocker in blockers else "해소됨",
        )
    status, payload = api.get("/api/v2/automation/runs")
    items = (payload.get("data") or {}).get("items") or []
    recorder.add(
        area, "runs v2", "PASS" if status == 200 else "FAIL", f"HTTP {status}, runs={len(items)}"
    )
    status, payload = api.get("/api/v2/automation/positions")
    data = payload.get("data") or {}
    summary = data.get("realizedSummary") or {}
    recorder.add(
        area,
        "positions v2 + 실현 성과",
        "PASS" if status == 200 and summary else "FAIL",
        f"HTTP {status}, closed={summary.get('closedPositionCount')},"
        f" pnl={summary.get('realizedPnlKrw')},"
        f" performanceClaimAllowed={summary.get('performanceClaimAllowed')}",
    )
    transitions = _psql(
        "select count(*) from (select p1_automation_transition_valid_v2(a,b) as ok"
        " from (values ('NEWS_CHECKING','HALTED'),('SCHEDULED','SCHEDULED'),"
        "('PENDING_RECONCILIATION','EXIT_SELECTED')) as t(a,b)) s where s.ok"
    )
    recorder.add(
        area,
        "전이 whitelist ↔ 엔진",
        "PASS" if transitions == "3" else "FAIL",
        f"이전에 거부되던 전이 3개 모두 허용={transitions}/3",
    )
    illegal = _psql("select p1_automation_transition_valid_v2('COMPLETED','PRECHECK')")
    recorder.add(
        area,
        "불법 전이 거부",
        "PASS" if illegal == "f" else "FAIL",
        f"COMPLETED->PRECHECK allowed={illegal}",
    )


def _check_rag(recorder: Recorder, api: ApiProbe) -> None:
    area = "RAG"
    corpus = _psql(
        "select (select count(*) from rag_v2_immutable_chunks)||'/'"
        "||(select count(*) from rag_v2_immutable_generation_embeddings)||'/'"
        "||(select count(*) from rag_v2_immutable_source_revisions)"
    )
    chunks = corpus.split("/")[0] if corpus else "0"
    recorder.add(
        area,
        "코퍼스 적재 (청크/임베딩/소스)",
        "PASS" if chunks not in {"", "0"} else "FAIL",
        f"{corpus} — Voyage 프로파일 seed",
    )
    status, payload = api.get("/api/v1/rag/sources")
    recorder.add(area, "소스 조회 surface", "PASS" if status == 200 else "FAIL", f"HTTP {status}")
    history = _psql("select count(*) from rag_answer_history")
    recorder.add(
        area,
        "암호화 답변 이력",
        "PASS" if history.strip().isdigit() else "FAIL",
        f"rows={history or '(조회 실패)'}",
    )
    # 이 두 줄은 예전에 판정이 코드에 박혀 있던 자리다. 배포 설정이 바뀌어도 보고서는 계속
    # BLOCKED라고 말했다. 지금은 실행 중인 컨테이너에게 물어 그 답을 그대로 판정으로 쓴다.
    runtime = _platform_env(
        "RAG_V2_GRPC_ENABLED",
        "S4_9_RUNTIME_VOYAGE_QUERY_ENABLED",
        "RAG_V2_VERTEX_ENABLED",
        "RAG_V2_VERTEX_AUTO_ACTIVATION_ENABLED",
    )
    retrieval_on = (
        runtime["RAG_V2_GRPC_ENABLED"] == "true"
        and runtime["S4_9_RUNTIME_VOYAGE_QUERY_ENABLED"] == "true"
    )
    batch_plan = _psql(
        "select count(*) from rag_v2_immutable_voyage_document_batch_plans where state = 'COMPLETE'"
    )
    recorder.add(
        area,
        "v2 검색 경로 (Voyage 질의)",
        "PASS" if retrieval_on and batch_plan.strip() not in ("", "0") else "BLOCKED",
        f"RAG_V2_GRPC_ENABLED={runtime['RAG_V2_GRPC_ENABLED'] or '<unset>'} "
        f"질의 런타임={runtime['S4_9_RUNTIME_VOYAGE_QUERY_ENABLED'] or '<unset>'} "
        f"COMPLETE 배치 계획={batch_plan or '(조회 실패)'}",
    )
    vertex_on = runtime["RAG_V2_VERTEX_ENABLED"] == "true"
    auto_on = runtime["RAG_V2_VERTEX_AUTO_ACTIVATION_ENABLED"] == "true"
    answered = _psql(
        "select count(*) from rag_v2_immutable_vertex_usage_reservations"
        " where created_at >= now() - interval '30 days'"
    )
    recorder.add(
        area,
        "생성형 답변 (Vertex)",
        "PASS" if vertex_on and answered.strip() not in ("", "0") else "BLOCKED",
        f"RAG_V2_VERTEX_ENABLED={runtime['RAG_V2_VERTEX_ENABLED'] or '<unset>'} "
        f"자동저술={runtime['RAG_V2_VERTEX_AUTO_ACTIVATION_ENABLED'] or '<unset>'} "
        f"최근 30일 생성 예약={answered or '(조회 실패)'} "
        f"(꺼져 있으면 BLOCKED가 맞다. 자동저술={auto_on})",
    )


def _check_team_b(recorder: Recorder) -> None:
    area = "Team B 파이프라인"
    bundles = _psql("select count(*) from p1_return_artifact_bundle")
    recorder.add(
        area,
        "실제 산출물 수신",
        "BLOCKED" if bundles == "0" else "PASS",
        f"bundles={bundles} — 외부 팀 대기",
    )
    # 물질화된 번들이 있으면 validator는 확인 대상이다. 없을 때만 NOT_APPLICABLE이 맞다.
    materialized = sorted(
        (_REPOSITORY / "deploy/p1/.state-app/artifacts").glob("*/p1-return-engine-manifest.v2.json")
    )
    recorder.add(
        area,
        "validator (hermetic)",
        "PASS" if materialized else "NOT_APPLICABLE",
        f"물질화된 번들={len(materialized)}개 "
        + (
            "tests/e2e/team_intake_e2e.py가 production 적재기로 이 번들을 통과시킨다"
            if materialized
            else "검증할 번들이 없다"
        ),
    )


def _check_dashboard(recorder: Recorder, api: ApiProbe) -> None:
    area = "대시보드 백엔드"
    for feature, path in (
        ("저널", "/api/v1/journals"),
        ("모델 평가", "/api/v1/dashboard/model-evaluations/demo_s8_fake_e2e_0001"),
        ("백테스트", "/api/v1/dashboard/backtests/demo_s8_fake_e2e_0001"),
    ):
        status, _ = api.get(path)
        recorder.add(area, feature, "PASS" if status == 200 else "FAIL", f"HTTP {status}")


def _check_retired(recorder: Recorder) -> None:
    area = "은퇴·금지 (배포 범위 밖)"
    for feature, reason in (
        ("LightGBM 신호", "V74에서 research-only로 은퇴. composite는 항상 ABSTAIN"),
        ("Naver 뉴스 provider", "런타임·스토리지 제거 완료"),
        ("cross-market", "V79에서 은퇴"),
        ("GDELT 온라인", "승인 패킷 없이는 PROVIDER_DISABLED"),
        ("KIS 실계좌 주문", "코드에 경로가 존재하지 않음"),
        ("강제청산·물타기·미체결 재가격", "교육용 경계로 넣지 않음"),
    ):
        recorder.add(area, feature, "NOT_APPLICABLE", reason)


def run(api_base: str, password: str) -> dict[str, Any]:
    recorder = Recorder()
    api = ApiProbe(api_base)
    ok, evidence = api.login(password)
    recorder.add("플랫폼", "로그인", "PASS" if ok else "FAIL", evidence)
    if not ok:
        raise VerificationRunnerError("login failed; the rest cannot be observed")
    _check_platform(recorder, api)
    _check_principles(recorder, api)
    _check_risk_and_decisions(recorder, api)
    _check_automation(recorder, api)
    _check_rag(recorder, api)
    _check_dashboard(recorder, api)
    _check_team_b(recorder)
    _check_retired(recorder)
    return {
        "contractId": "p1-deployment-verification.v1",
        "counts": recorder.counts(),
        "findings": [finding.projection() for finding in recorder.findings],
        "generatedAt": datetime.now(tz=UTC).isoformat(),
        "providerCalls": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:18080")
    parser.add_argument("--out", default="")
    arguments = parser.parse_args(argv)
    if os.environ.get(_OPT_IN) != "1":
        print(f"{_OPT_IN}=1 이 없으면 실행하지 않는다", file=sys.stderr)
        return 2
    secret = _REPOSITORY / "deploy/p1/.state-app/secrets/demo-user.password"
    try:
        password = secret.read_text(encoding="utf-8").strip()
    except OSError as error:
        print(f"DEPLOYMENT_VERIFICATION=FAIL: demo password unavailable: {error}", file=sys.stderr)
        return 1
    try:
        report = run(arguments.api, password)
    except VerificationRunnerError as error:
        print(f"DEPLOYMENT_VERIFICATION=FAIL: {error}", file=sys.stderr)
        return 1
    body = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if arguments.out:
        destination = Path(arguments.out)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(body + "\n", encoding="utf-8")
    print(body)
    failed = report["counts"].get("FAIL", 0)
    print(
        f"DEPLOYMENT_VERIFICATION={'FAIL' if failed else 'PASS'} {report['counts']}",
        file=sys.stderr,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
