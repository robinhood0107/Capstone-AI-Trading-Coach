"""RAG v2 실검색·생성 경로의 경계를 실행 중인 스택에서 확인한다.

이 파일은 테스트 전용이다. production 이미지나 business logic에서 import 하지 않으며 `test_`
접두사가 아니라 pytest 수집 대상도 아니다. `tests/e2e/full_pipeline_e2e.py`와 같은 경계다.

무엇을 확인하나.
  1. 검색 역할(`decision_rag_query`)에 직접 표 권한이 없다. 함수 EXECUTE만으로 산다.
  2. Voyage 문서 배치 계획 행이 있고 tokenizer 해시가 런타임 설정과 같다.
  3. 로컬 루트가 컨테이너 안에서 0700/0600 소유자 전용이고 leaf가 정확히 셋이다.
  4. gRPC reflection이 꺼져 있고 bind 주소가 loopback이다.
  5. 동의를 철회하면 물리 호출이 0이다. 예약 원장이 한 줄도 늘지 않는다.
  6. (`--with-live-query`) 동의가 있는 상태의 실검색 1회.
  7. (`--with-vertex-generation`) 활성화 패킷을 저술한 뒤의 생성형 답변 1회.

기대값은 상수로 박지 않는다. Vertex 생성이 켜져 있는지는 컨테이너 환경에서 **읽어서** 알아내고,
그 관측값에 따라 실검색의 기대 상태(`ANSWERED`/`RETRIEVAL_ONLY`)를 정한다.

무엇을 확인하지 않나. 6·7번은 provider 물리 호출을 쓰므로 자동으로 돌리지 않는다.

실행:
  P1_RAG_V2_BOUNDARY_CHECK=1 python -m tests.e2e.rag_v2_boundaries
  P1_RAG_V2_BOUNDARY_CHECK=1 python -m tests.e2e.rag_v2_boundaries \\
    --with-live-query --with-vertex-generation
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from .harness import (
    Api,
    HarnessError,
    Recorder,
    STATE,
    DOCKER,
    PLATFORM,
    platform,
    psql,
    require_opt_in,
    write_report,
)

_OPT_IN: Final = "P1_RAG_V2_BOUNDARY_CHECK"
_QUERY_ROLE: Final = "decision_rag_query"
_LOCAL_ROOT: Final = "/tmp/rag-v2-root"
_TOKENIZER_SHA256: Final = "c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539"
_PROFILE: Final = "voyage_context_4_1024_v1"
_LEAVES: Final = (
    "control/pre-s5-voyage-query-runtime.json",
    "secrets/rag-v2-voyage-query-writer-dsn",
    "artifacts/voyage-context-4/tokenizer.json",
)
_CONSENT_BODY: Final = {
    "contractId": "s4-rag-v2-external-consent-v1",
    "schemaVersion": 1,
    "consentType": "EXTERNAL_AI_RAG_V2",
}
_TOPICS: Final = ["FINANCIAL_ENGINEERING", "RISK"]
# 검증기가 허용하는 경고 어휘. 답변이 나왔다는 사실을 뒤집지 않는다.
_GENERATION_WARNINGS: Final = frozenset(
    {
        "SINGLE_SOURCE",
        "STALE_SOURCE",
        "CONFLICTING_SOURCES",
        "LOW_RELEVANCE",
        "SECONDARY_SOURCE",
    }
)
# 운영자만 갖는 배포 고유 값이다. 저장소에 박지 않는다. 없으면 생성 확인은 INFO로 건너뛴다.
_EVIDENCE_FILE: Final = STATE / "vertex-activation-evidence.json"
_EVIDENCE_FIELDS: Final = (
    "projectId",
    "serviceAccountSecurityEvidenceSha256",
    "dataGovernanceStateEvidenceSha256",
    "abuseMonitoringStateEvidenceSha256",
    "modelAvailabilityEvidenceSha256",
)


def _reservation_count() -> int:
    return int(
        psql("select count(*) from public.rag_v2_immutable_voyage_query_usage_reservations;") or 0
    )


def _consent(api: Api, action: str, digest: str) -> int:
    status, _ = api.request(
        "POST",
        "/api/v2/rag/consents",
        {
            **_CONSENT_BODY,
            "action": action,
            "disclosureDigest": digest * 64,
            "policyDigest": digest * 64,
            "processorSetDigest": digest * 64,
        },
    )
    return status


def _vertex_settings() -> dict[str, str]:
    """생성 경로의 런타임 설정을 컨테이너에서 읽는다. 기대값을 코드에 박지 않기 위해서다."""

    names = (
        "RAG_V2_VERTEX_ENABLED",
        "RAG_V2_VERTEX_AUTO_ACTIVATION_ENABLED",
        "VERTEX_MODEL_ID",
        "RAG_V2_VERTEX_HEAD_COMMIT",
        "RAG_V2_VERTEX_TREE_DIGEST",
        "RAG_V2_VERTEX_CI_DIGEST",
        "RAG_V2_VERTEX_SECURITY_DIGEST",
    )
    printed = platform(
        "for name in " + " ".join(names) + '; do printf "%s\\n" "$(printenv "$name")"; done'
    ).splitlines()
    return dict(zip(names, printed + [""] * len(names), strict=False))


def check_query_role_privileges(recorder: Recorder) -> None:
    grants = psql(
        "select coalesce(string_agg(distinct table_name || ':' || privilege_type, ', '), '')"
        " from information_schema.table_privileges"
        f" where grantee = '{_QUERY_ROLE}';"
    )
    executes = psql(
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
    row = psql(
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
    listing = platform(
        f"stat -c '%n %a %u' {_LOCAL_ROOT} {_LOCAL_ROOT}/control {_LOCAL_ROOT}/secrets "
        f"{_LOCAL_ROOT}/artifacts {_LOCAL_ROOT}/artifacts/voyage-context-4 "
        + " ".join(f"{_LOCAL_ROOT}/{leaf}" for leaf in _LEAVES)
        + " 2>&1 || true"
    )
    uid = platform("id -u")
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
    digest = platform(f"sha256sum {_LOCAL_ROOT}/artifacts/voyage-context-4/tokenizer.json").split()[
        0
    ]
    recorder.add(
        "공식 tokenizer 해시",
        "PASS" if digest == _TOKENIZER_SHA256 else "FAIL",
        f"{digest} (배치 계획이 요구하는 해시와 같아야 예약이 열린다)",
    )


def check_transport_settings(recorder: Recorder, settings: dict[str, str]) -> None:
    """전송 경계는 값이 고정이다. 생성 활성화 여부는 고정이 아니므로 여기서 판정하지 않는다."""

    observed = platform(
        "printenv RAG_V2_GRPC_ENABLE_REFLECTION RAG_V2_GRPC_BIND_ADDRESS "
        "RAG_V2_GRPC_TARGET S4_9_RUNTIME_VOYAGE_QUERY_ENABLED || true"
    ).splitlines()
    expected = ["false", "127.0.0.1:50054", "127.0.0.1:50054", "true"]
    recorder.add(
        "전송 설정",
        "PASS" if observed == expected else "FAIL",
        f"{observed} (reflection off, loopback 고정, 질의 런타임 on)",
    )
    recorder.add(
        "생성 활성화 관측",
        "INFO",
        f"RAG_V2_VERTEX_ENABLED={settings['RAG_V2_VERTEX_ENABLED'] or '<unset>'} "
        f"자동저술={settings['RAG_V2_VERTEX_AUTO_ACTIVATION_ENABLED'] or '<unset>'} "
        f"model={settings['VERTEX_MODEL_ID'] or '<unset>'} "
        "(이 값들이 아래 질의의 기대 상태를 정한다)",
    )


def _remove_activation_packet() -> None:
    """이전 실행이 남긴 활성화 패킷을 지운다. 없어야 성립하는 확인이 아래 둘이다."""

    subprocess.run(
        [
            DOCKER,
            "exec",
            PLATFORM,
            "rm",
            "-f",
            f"{_LOCAL_ROOT}/control/pre-s5-vertex-activation.json",
        ],
        capture_output=True,
        timeout=60,
        check=False,
    )


def check_consent_gate(
    recorder: Recorder, api: Api, vertex_enabled: bool, auto_activation: bool
) -> None:
    """동의를 철회한 상태에서는 물리 호출이 0이어야 한다.

    닫히는 자리는 생성 활성화 여부에 따라 다르다. 생성이 켜져 있으면 scope claim 없는 질의는
    consent를 읽기도 전에 `GENERATION_UNAVAILABLE`로 끊긴다(`RagV2RuntimeService.ask`). 생성이
    꺼져 있으면 consent 자리에서 typed 409로 닫힌다. 두 경우 모두 예약은 늘지 않아야 한다.
    """

    _remove_activation_packet()
    before = _reservation_count()
    status = _consent(api, "REVOKE", "0")
    if status != 204:
        raise HarnessError(f"consent revoke failed: HTTP {status}")
    ask_status, ask_body = api.request(
        "POST",
        "/api/v2/rag/ask",
        {
            "question": "What does the Sharpe ratio measure?",
            "answerMode": "CONCISE",
            "topics": _TOPICS,
        },
    )
    after = _reservation_count()
    flags = ask_body.get("guardrailFlags") or []
    if vertex_enabled and not auto_activation:
        closed = (
            ask_status == 200
            and flags == ["GENERATION_UNAVAILABLE"]
            and not (ask_body.get("citations") or [])
        )
        expectation = "생성 활성화 상태: scope claim 없는 질의는 provider socket 전에 끊긴다"
    else:
        closed = ask_status == 409 and ask_body.get("code") == "EXTERNAL_AI_CONSENT_REQUIRED"
        expectation = (
            "자동 저술 상태: 준비 단계의 consent 확인에서 typed 409로 닫힌다"
            if auto_activation
            else "생성 비활성 상태: consent 자리에서 typed 409로 닫힌다"
        )
    recorder.add(
        "동의 없는 질의는 물리 호출 0",
        "PASS" if closed and after == before else "FAIL",
        f"HTTP {ask_status} code={ask_body.get('code')} guardrail={flags} "
        f"예약 {before}→{after} ({expectation})",
    )


def _prepare_scope(api: Api, question: str) -> dict[str, Any]:
    if _consent(api, "GRANT", "1") != 204:
        raise HarnessError("consent grant failed")
    status, preparation = api.request(
        "POST",
        "/api/v2/rag/vertex-preparations",
        {"question": question, "answerMode": "CONCISE", "topics": _TOPICS},
        headers={"X-Request-Id": f"req_{uuid.uuid4().hex}"},
    )
    if status != 201:
        raise HarnessError(f"vertex scope preparation failed: HTTP {status}")
    return preparation


def check_live_query(recorder: Recorder, api: Api, question: str, vertex_enabled: bool) -> None:
    """물리 호출 1건을 실제로 쓴다. 명시적으로 요청했을 때만 돈다.

    생성이 켜져 있으면 scope claim을 받아 검색까지 태우고, 활성화 패킷은 일부러 두지 않는다.
    그러면 검색은 돌고 생성만 닫히므로 두 경계를 한 번에 본다.
    """

    _remove_activation_packet()
    before = _reservation_count()
    if vertex_enabled:
        preparation = _prepare_scope(api, question)
        headers = {
            "X-Request-Id": preparation["requestId"],
            "X-Rag-V2-Vertex-Scope-Claim": preparation["scopeClaimId"],
        }
    else:
        if _consent(api, "GRANT", "1") != 204:
            raise HarnessError("consent grant failed")
        headers = {}
    ask_status, ask_body = api.request(
        "POST",
        "/api/v2/rag/ask",
        {"question": question, "answerMode": "CONCISE", "topics": _TOPICS},
        headers=headers,
    )
    after = _reservation_count()
    citations = ask_body.get("citations") or []
    flags = ask_body.get("guardrailFlags") or []
    status_text = ask_body.get("generationStatus")
    if vertex_enabled:
        # 활성화 패킷이 없으면 생성이 닫히고, 그 답변은 검색 결과를 싣지 않는다
        # (`RagV2RuntimeService.vertexUnavailableAnswer`). 검색이 실제로 돌았다는 증거는
        # 인용이 아니라 예약 원장이 한 줄 늘었다는 사실이다.
        expected = (
            status_text == "GENERATION_UNAVAILABLE"
            and flags == ["GENERATION_UNAVAILABLE"]
            and not citations
        )
        note = "생성 활성화 상태: 검색은 돌고 생성만 닫힌다. 답변은 인용을 싣지 않는다"
    else:
        expected = status_text == "RETRIEVAL_ONLY" and len(citations) >= 1 and not flags
        note = "생성 비활성 상태: 검색 전용 답변에 인용이 실린다"
    recorder.add(
        "실검색 1회",
        "PASS" if ask_status == 200 and expected and after == before + 1 else "FAIL",
        f"HTTP {ask_status} {status_text} 인용 {len(citations)}건 "
        f"예약 {before}→{after} guardrail={flags} ({note})",
    )


def _load_activation_evidence() -> dict[str, str] | None:
    if not _EVIDENCE_FILE.exists():
        return None
    parsed = json.loads(_EVIDENCE_FILE.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict) or any(field not in parsed for field in _EVIDENCE_FIELDS):
        raise HarnessError(f"{_EVIDENCE_FILE.name} lacks the required operator fields")
    return {field: str(parsed[field]) for field in _EVIDENCE_FIELDS}


def _write_activation_packet(document: dict[str, Any]) -> None:
    """컨테이너 로컬 루트에 0600으로 활성화 패킷을 저술한다. 내용은 출력하지 않는다."""

    script = (
        f"set -e; install -d -m 700 {_LOCAL_ROOT}/control; "
        f"cat > {_LOCAL_ROOT}/control/pre-s5-vertex-activation.json; "
        f"chmod 600 {_LOCAL_ROOT}/control/pre-s5-vertex-activation.json"
    )
    result = subprocess.run(
        [DOCKER, "exec", "-i", PLATFORM, "sh", "-c", script],
        input=json.dumps(document, sort_keys=True, separators=(",", ":")),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise HarnessError(f"activation packet write failed ({result.returncode})")


def check_vertex_generation(
    recorder: Recorder, api: Api, question: str, settings: dict[str, str]
) -> None:
    """활성화 패킷을 저술하고 생성형 답변 1회를 실제로 받는다."""

    if settings["RAG_V2_VERTEX_ENABLED"] != "true":
        recorder.add(
            "생성형 답변 1회",
            "INFO",
            "RAG_V2_VERTEX_ENABLED가 true가 아니다. 이 배포에서는 생성 경로가 닫혀 있다",
        )
        return
    evidence = _load_activation_evidence()
    if evidence is None:
        recorder.add(
            "생성형 답변 1회",
            "INFO",
            f"{_EVIDENCE_FILE.name}이 없다. 운영자 evidence 해시 없이는 패킷을 저술하지 않는다",
        )
        return

    preparation = _prepare_scope(api, question)

    now = datetime.now(UTC)
    project_id = evidence["projectId"]
    model_id = settings["VERTEX_MODEL_ID"]
    packet = {
        "contractId": "pre-s5-vertex-activation/v3",
        "provider": "VERTEX_AI",
        "authenticationMode": "SERVICE_ACCOUNT_OAUTH",
        "origin": "https://aiplatform.googleapis.com",
        "endpoint": (
            f"POST /v1/projects/{project_id}/locations/global/publishers/google/models/"
            f"{model_id}:generateContent"
        ),
        "authOrigin": "https://oauth2.googleapis.com",
        "authEndpoint": "POST /token",
        "projectId": project_id,
        "modelId": model_id,
        "headCommit": settings["RAG_V2_VERTEX_HEAD_COMMIT"],
        "treeDigest": settings["RAG_V2_VERTEX_TREE_DIGEST"],
        "ciDigest": settings["RAG_V2_VERTEX_CI_DIGEST"],
        "securityDigest": settings["RAG_V2_VERTEX_SECURITY_DIGEST"],
        "serviceAccountSecurityEvidenceSha256": evidence["serviceAccountSecurityEvidenceSha256"],
        "dataGovernanceStateEvidenceSha256": evidence["dataGovernanceStateEvidenceSha256"],
        "abuseMonitoringStateEvidenceSha256": evidence["abuseMonitoringStateEvidenceSha256"],
        "modelAvailabilityEvidenceSha256": evidence["modelAvailabilityEvidenceSha256"],
        "requestId": preparation["requestId"],
        "scopeClaimId": preparation["scopeClaimId"],
        "questionFingerprintHmac": preparation["questionFingerprintHmac"],
        "answerMode": preparation["answerMode"],
        "consentEventId": preparation["consentEventId"],
        "policySha256": preparation["policyDigest"],
        "processorSetSha256": preparation["processorSetDigest"],
        "issuedAt": now.isoformat().replace("+00:00", "Z"),
        "expiresAt": (now + timedelta(minutes=4)).isoformat().replace("+00:00", "Z"),
        "logicalCallCap": 1,
        "physicalCallCap": 2,
        "tokenPhysicalCallCap": 1,
        "generateContentPhysicalCallCap": 1,
        "inputTokenCap": 60_512,
        "outputTokenCap": 1_000,
        "inputByteCap": 60_000,
        "costCapMicrousd": 131_000,
        "inputMicrousdPerToken": 2,
        "outputMicrousdPerToken": 9,
        "retryCount": 0,
        "rawArtifactCount": 0,
        "operator": "local-operator",
        "nonce": f"ps5_vertex_live_{uuid.uuid4().hex}",
    }
    _write_activation_packet(packet)

    ask_status, answer = api.request(
        "POST",
        "/api/v2/rag/ask",
        {"question": question, "answerMode": "CONCISE", "topics": _TOPICS},
        headers={
            "X-Request-Id": preparation["requestId"],
            "X-Rag-V2-Vertex-Scope-Claim": preparation["scopeClaimId"],
        },
    )
    citations = answer.get("citations") or []
    flags = answer.get("guardrailFlags") or []
    # 경고는 검증기가 허용한 어휘다. 인용이 하나뿐이면 SINGLE_SOURCE가 붙는 것이 정상이고,
    # 실패로 볼 것은 생성이 닫혔다는 표시뿐이다.
    blocking = [flag for flag in flags if flag not in _GENERATION_WARNINGS]
    answered = (
        ask_status == 200
        and answer.get("generationStatus") == "ANSWERED"
        and len(citations) >= 1
        and not blocking
    )
    recorder.add(
        "생성형 답변 1회",
        "PASS" if answered else "FAIL",
        f"HTTP {ask_status} {answer.get('generationStatus')} 인용 {len(citations)}건 "
        f"인용커버리지={answer.get('citationCoverage')} guardrail={flags} 차단성={blocking}",
    )


def check_auto_activation(
    recorder: Recorder, api: Api, question: str, settings: dict[str, str]
) -> None:
    """대시보드가 실제로 쓰는 경로 그대로 확인한다.

    헤더도 없고 운영자 패킷도 없다. 자동 저술이 켜져 있으면 이 한 번의 호출이 준비→저술→생성까지
    끝내고 `ANSWERED`가 나와야 한다. 이것이 화면에서 답이 보이는지와 같은 질문이다.
    """

    if settings["RAG_V2_VERTEX_AUTO_ACTIVATION_ENABLED"] != "true":
        recorder.add(
            "대시보드 경로 생성",
            "INFO",
            "자동 저술이 꺼져 있다. 이 배포에서 화면은 검색 전용으로 동작한다",
        )
        return
    _remove_activation_packet()
    if _consent(api, "GRANT", "1") != 204:
        raise HarnessError("consent grant failed")
    status, answer = api.request(
        "POST",
        "/api/v2/rag/ask",
        {"question": question, "answerMode": "CONCISE", "topics": _TOPICS},
    )
    citations = answer.get("citations") or []
    flags = answer.get("guardrailFlags") or []
    blocking = [flag for flag in flags if flag not in _GENERATION_WARNINGS]
    recorder.add(
        "대시보드 경로 생성",
        "PASS"
        if status == 200
        and answer.get("generationStatus") == "ANSWERED"
        and len(citations) >= 1
        and not blocking
        else "FAIL",
        f"HTTP {status} {answer.get('generationStatus')} 인용 {len(citations)}건 "
        f"인용커버리지={answer.get('citationCoverage')} guardrail={flags} 차단성={blocking} "
        "(헤더 없이 부른 질의다)",
    )


def main(argv: list[str]) -> int:
    require_opt_in(_OPT_IN)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="")
    parser.add_argument("--with-live-query", action="store_true")
    parser.add_argument("--with-vertex-generation", action="store_true")
    parser.add_argument("--question", default="What does the Sharpe ratio measure?")
    args = parser.parse_args(argv[1:])

    recorder = Recorder()
    try:
        settings = _vertex_settings()
        check_query_role_privileges(recorder)
        check_batch_plan(recorder)
        check_local_root(recorder)
        check_transport_settings(recorder, settings)
        api = Api()
        api.login("demo-user")
        vertex_enabled = settings["RAG_V2_VERTEX_ENABLED"] == "true"
        auto_activation = settings["RAG_V2_VERTEX_AUTO_ACTIVATION_ENABLED"] == "true"
        check_consent_gate(recorder, api, vertex_enabled, auto_activation)
        if args.with_live_query:
            check_live_query(recorder, api, args.question, vertex_enabled)
        else:
            recorder.add(
                "실검색 1회",
                "INFO",
                "--with-live-query 없이는 돌리지 않는다. provider 물리 호출을 쓰는 단계다",
            )
        if args.with_vertex_generation:
            check_vertex_generation(recorder, api, args.question, settings)
            check_auto_activation(recorder, api, args.question, settings)
        else:
            for name in ("생성형 답변 1회", "대시보드 경로 생성"):
                recorder.add(
                    name,
                    "INFO",
                    "--with-vertex-generation 없이는 돌리지 않는다. provider 물리 호출을 쓰는 단계다",
                )
    except HarnessError as error:
        recorder.add("확인 중단", "FAIL", str(error))

    report = write_report(
        contract_id="p1-rag-v2-boundaries.v1",
        marker="P1_RAG_V2_BOUNDARIES",
        recorder=recorder,
        out=args.out,
    )
    return 1 if report["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
