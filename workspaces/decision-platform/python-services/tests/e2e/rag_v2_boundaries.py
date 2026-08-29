"""RAG v2 실검색 경로의 경계를 실행 중인 스택에서 확인한다.

이 파일은 테스트 전용이다. production 이미지나 business logic에서 import 하지 않으며 `test_`
접두사가 아니라 pytest 수집 대상도 아니다. `tests/e2e/full_pipeline_e2e.py`와 같은 경계다.

무엇을 확인하나.
  1. 검색 역할(`decision_rag_query`)에 직접 표 권한이 없다. 함수 EXECUTE만으로 산다.
  2. Voyage 문서 배치 계획 행이 있고 tokenizer 해시가 런타임 설정과 같다.
  3. 로컬 루트가 컨테이너 안에서 0700/0600 소유자 전용이고 leaf가 정확히 셋이다.
  4. gRPC reflection이 꺼져 있고 bind 주소가 loopback이다.
  5. 동의를 철회하면 물리 호출이 0이다. 예약 원장이 한 줄도 늘지 않는다.

무엇을 확인하지 않나. 동의가 있는 상태의 실제 질의는 provider 물리 호출 1건을 쓰므로 여기서
자동으로 돌리지 않는다. 그것은 `--with-live-query`를 명시할 때만 한다.

실행:
  P1_RAG_V2_BOUNDARY_CHECK=1 python tests/e2e/rag_v2_boundaries.py
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
from pathlib import Path
from typing import Any, Final

_OPT_IN: Final = "P1_RAG_V2_BOUNDARY_CHECK"
_REPOSITORY: Final = Path(__file__).resolve().parents[5]
_STATE: Final = _REPOSITORY / "deploy/p1/.state-app"
_DOCKER: Final = "/usr/bin/docker" if Path("/usr/bin/docker").exists() else "docker"
_POSTGRES: Final = "capstone-p1-postgres-1"
_PLATFORM: Final = "capstone-p1-decision-platform-1"
_QUERY_ROLE: Final = "decision_rag_query"
_LOCAL_ROOT: Final = "/tmp/rag-v2-root"
_TOKENIZER_SHA256: Final = "c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539"
_PROFILE: Final = "voyage_context_4_1024_v1"
_LEAVES: Final = (
    "control/pre-s5-voyage-query-runtime.json",
    "secrets/rag-v2-voyage-query-writer-dsn",
    "artifacts/voyage-context-4/tokenizer.json",
)


class BoundaryError(RuntimeError):
    """확인 절차 자체가 성립하지 못했다. 개별 단언 실패와 구분한다."""


@dataclass
class Recorder:
    steps: list[dict[str, str]] = field(default_factory=list)

    def add(self, name: str, verdict: str, detail: str) -> None:
        if verdict not in {"PASS", "FAIL", "INFO"}:
            raise BoundaryError(f"unknown verdict: {verdict}")
        self.steps.append({"detail": detail[:4000], "name": name, "verdict": verdict})
        marker = {"PASS": "PASS", "FAIL": "FAIL", "INFO": "····"}[verdict]
        print(f"[{marker}] {name}: {detail[:4000]}", flush=True)

    def failed(self) -> bool:
        return any(step["verdict"] == "FAIL" for step in self.steps)


def _run(command: list[str], *, stdin: str | None = None) -> str:
    result = subprocess.run(
        command, input=stdin, capture_output=True, text=True, timeout=120, check=False
    )
    if result.returncode != 0:
        raise BoundaryError(
            f"command failed ({result.returncode}): {Path(command[0]).name}…\n"
            f"{result.stdout.strip()[-2000:]}\n{result.stderr.strip()[-2000:]}"
        )
    return result.stdout


def _psql(sql: str) -> str:
    return _run(
        [
            _DOCKER,
            "exec",
            "-i",
            _POSTGRES,
            "psql",
            "-U",
            "postgres",
            "-d",
            "capstone_p1",
            "-v",
            "ON_ERROR_STOP=1",
            "-tAq",
        ],
        stdin=sql,
    ).strip()


def _platform(command: str) -> str:
    return _run([_DOCKER, "exec", _PLATFORM, "sh", "-c", command]).strip()


def _request(
    method: str, path: str, token: str | None = None, body: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"http://127.0.0.1:18080{path}", method=method, data=data)
    request.add_header("Content-Type", "application/json")
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            return error.code, (json.loads(raw) if raw else {})
        except json.JSONDecodeError:
            return error.code, {}
    except (urllib.error.URLError, TimeoutError) as error:
        raise BoundaryError(f"api unreachable: {error}") from error


def _login() -> str:
    password = (_STATE / "secrets/demo-user.password").read_text(encoding="utf-8").strip()
    status, payload = _request(
        "POST", "/api/v1/auth/login", body={"username": "demo-user", "password": password}
    )
    token = (payload.get("data") or {}).get("accessToken")
    if status != 200 or not isinstance(token, str):
        raise BoundaryError(f"demo-user login failed: HTTP {status}")
    return token


def _reservation_count() -> int:
    return int(
        _psql("select count(*) from public.rag_v2_immutable_voyage_query_usage_reservations;") or 0
    )


def check_query_role_privileges(recorder: Recorder) -> None:
    grants = _psql(
        "select coalesce(string_agg(distinct table_name || ':' || privilege_type, ', '), '')"
        " from information_schema.table_privileges"
        f" where grantee = '{_QUERY_ROLE}';"
    )
    executes = _psql(
        "select count(*) from information_schema.routine_privileges"
        f" where grantee = '{_QUERY_ROLE}' and privilege_type = 'EXECUTE';"
    )
    recorder.add(
        "검색 역할 권한",
        "PASS" if grants == "" and int(executes or 0) > 0 else "FAIL",
        f"직접 표 권한=[{grants}] 함수 EXECUTE={executes} "
        "(표 권한이 하나라도 생기면 회귀다. 검색은 definer 함수로만 산다)",
    )


def check_batch_plan(recorder: Recorder) -> None:
    row = _psql(
        "select state || '|' || official_tokenizer_sha256 || '|' || expected_chunk_count"
        " from public.rag_v2_immutable_voyage_document_batch_plans"
        f" where embedding_profile_id = '{_PROFILE}' and state = 'COMPLETE';"
    )
    recorder.add(
        "Voyage 문서 배치 계획",
        "PASS" if row == f"COMPLETE|{_TOKENIZER_SHA256}|7871" else "FAIL",
        f"{row or 'no row'} (없으면 모든 질의 예약이 55000으로 닫힌다)",
    )


def check_local_root(recorder: Recorder) -> None:
    listing = _platform(
        f"stat -c '%n %a %u' {_LOCAL_ROOT} {_LOCAL_ROOT}/control {_LOCAL_ROOT}/secrets "
        f"{_LOCAL_ROOT}/artifacts {_LOCAL_ROOT}/artifacts/voyage-context-4 "
        + " ".join(f"{_LOCAL_ROOT}/{leaf}" for leaf in _LEAVES)
        + " 2>&1 || true"
    )
    uid = _platform("id -u")
    rows = [line.split() for line in listing.splitlines() if line]
    directories_ok = all(row[1] == "700" for row in rows[:5] if len(row) == 3)
    files_ok = all(row[1] == "600" for row in rows[5:] if len(row) == 3)
    owned = all(row[2] == uid for row in rows if len(row) == 3)
    found = len(rows) == 5 + len(_LEAVES)
    recorder.add(
        "로컬 루트 경계",
        "PASS" if found and directories_ok and files_ok and owned else "FAIL",
        f"uid={uid} 항목={len(rows)} 디렉터리700={directories_ok} 파일600={files_ok} 소유일치={owned}",
    )
    digest = _platform(
        f"sha256sum {_LOCAL_ROOT}/artifacts/voyage-context-4/tokenizer.json"
    ).split()[0]
    recorder.add(
        "공식 tokenizer 해시",
        "PASS" if digest == _TOKENIZER_SHA256 else "FAIL",
        f"{digest} (배치 계획이 요구하는 해시와 같아야 예약이 열린다)",
    )


def check_transport_settings(recorder: Recorder) -> None:
    settings = _platform(
        "printenv RAG_V2_GRPC_ENABLE_REFLECTION RAG_V2_GRPC_BIND_ADDRESS "
        "RAG_V2_GRPC_TARGET S4_9_RUNTIME_VOYAGE_QUERY_ENABLED RAG_V2_VERTEX_ENABLED || true"
    ).splitlines()
    expected = ["false", "127.0.0.1:50054", "127.0.0.1:50054", "true", "false"]
    recorder.add(
        "전송 설정",
        "PASS" if settings == expected else "FAIL",
        f"{settings} (reflection off, loopback 고정, Vertex 생성은 꺼진 상태)",
    )


def check_consent_gate(recorder: Recorder, token: str) -> None:
    """동의를 철회한 상태에서는 물리 호출이 0이어야 한다."""

    before = _reservation_count()
    status, _ = _request(
        "POST",
        "/api/v2/rag/consents",
        token,
        body={
            "contractId": "s4-rag-v2-external-consent-v1",
            "schemaVersion": 1,
            "consentType": "EXTERNAL_AI_RAG_V2",
            "action": "REVOKE",
            "disclosureDigest": "0" * 64,
            "policyDigest": "0" * 64,
            "processorSetDigest": "0" * 64,
        },
    )
    if status != 204:
        raise BoundaryError(f"consent revoke failed: HTTP {status}")
    ask_status, ask_body = _request(
        "POST",
        "/api/v2/rag/ask",
        token,
        body={
            "question": "What does the Sharpe ratio measure?",
            "answerMode": "CONCISE",
            "topics": ["FINANCIAL_ENGINEERING", "RISK"],
        },
    )
    after = _reservation_count()
    closed = ask_status == 409 and (ask_body.get("code") == "EXTERNAL_AI_CONSENT_REQUIRED")
    recorder.add(
        "동의 없는 질의는 물리 호출 0",
        "PASS" if closed and after == before else "FAIL",
        f"HTTP {ask_status} {ask_body.get('code')} 예약 {before}→{after}",
    )


def check_live_query(recorder: Recorder, token: str, question: str) -> None:
    """물리 호출 1건을 실제로 쓴다. 명시적으로 요청했을 때만 돈다."""

    before = _reservation_count()
    status, _ = _request(
        "POST",
        "/api/v2/rag/consents",
        token,
        body={
            "contractId": "s4-rag-v2-external-consent-v1",
            "schemaVersion": 1,
            "consentType": "EXTERNAL_AI_RAG_V2",
            "action": "GRANT",
            "disclosureDigest": "1" * 64,
            "policyDigest": "1" * 64,
            "processorSetDigest": "1" * 64,
        },
    )
    if status != 204:
        raise BoundaryError(f"consent grant failed: HTTP {status}")
    ask_status, ask_body = _request(
        "POST",
        "/api/v2/rag/ask",
        token,
        body={
            "question": question,
            "answerMode": "CONCISE",
            "topics": ["FINANCIAL_ENGINEERING", "RISK"],
        },
    )
    after = _reservation_count()
    citations = ask_body.get("citations") or []
    recorder.add(
        "실검색 1회",
        "PASS"
        if ask_status == 200
        and ask_body.get("generationStatus") == "RETRIEVAL_ONLY"
        and len(citations) >= 1
        and after == before + 1
        else "FAIL",
        f"HTTP {ask_status} {ask_body.get('generationStatus')} 인용 {len(citations)}건 "
        f"예약 {before}→{after} 첫 출처="
        f"{(citations[0].get('canonicalUrl') if citations else '-')}",
    )


def main(argv: list[str]) -> int:
    if os.environ.get(_OPT_IN) != "1":
        print(f"{_OPT_IN}=1 must be set explicitly", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="")
    parser.add_argument("--with-live-query", action="store_true")
    parser.add_argument("--question", default="What does the Sharpe ratio measure?")
    args = parser.parse_args(argv[1:])

    recorder = Recorder()
    try:
        check_query_role_privileges(recorder)
        check_batch_plan(recorder)
        check_local_root(recorder)
        check_transport_settings(recorder)
        token = _login()
        check_consent_gate(recorder, token)
        if args.with_live_query:
            check_live_query(recorder, token, args.question)
        else:
            recorder.add(
                "실검색 1회",
                "INFO",
                "--with-live-query 없이는 돌리지 않는다. provider 물리 호출을 쓰는 단계다",
            )
    except BoundaryError as error:
        recorder.add("확인 중단", "FAIL", str(error))

    report = {
        "contractId": "p1-rag-v2-boundaries.v1",
        "steps": recorder.steps,
        "verdict": "FAIL" if recorder.failed() else "PASS",
    }
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n판정표: {path}")
    print(f"\nP1_RAG_V2_BOUNDARIES={report['verdict']}")
    return 1 if recorder.failed() else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
