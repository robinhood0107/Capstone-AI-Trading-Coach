"""도는 스택에서 공유 가능한 배포 설정을 뽑는다.

왜 있나
-------
새 서버를 올리려면 정책·상한·인증이 필요한데, 지금은 그 값들이 DB 와 런타임 디렉터리에
흩어져 있다. 손으로 옮기면 빠뜨리거나 개인정보를 같이 흘린다.

이 도구는 **공유해도 되는 것만** 한 파일로 모은다. 받은 사람이 그대로 올리면 같은
상태가 되고, 그래서 바로 검증할 수 있다.

개인정보를 흘리지 않는 방법
-------------------------
허용 키를 화이트리스트로 못 박는다. 목록에 없는 키는 **조용히 빠지는 게 아니라
실패한다.** 새 필드가 생겼을 때 사람이 한 번 보고 판단하게 만드는 것이 목적이다.

담지 않는 것: KIS 앱키·시크릿·계좌번호, 서비스 계정, DB 비밀번호, JWT 시크릿,
사용자 식별자. 이것들은 `.env` 와 `google.json` 에만 둔다.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Final

CONTRACT_ID: Final = "p1-deploy-profile/v1"

#: 정책에서 공유해도 되는 값. 계좌·사용자 식별자는 의도적으로 빠져 있다.
_POLICY_KEYS: Final = (
    "presetId",
    "capitalLimitKrw",
    "stopLossBps",
    "takeProfitBps",
    "atrPeriod",
    "atrMultiplierMilli",
    "maxHoldingSessions",
    "modelSellEnabled",
)
_VERTEX_CAP_KEYS: Final = (
    "dailyGenerateCallCap",
    "inputTokenCap",
    "outputTokenCap",
    "inputByteCap",
    "costCapMicrousd",
    "inputMicrousdPerToken",
    "outputMicrousdPerToken",
)
#: 자본정책에서 공유해도 되는 값. 나머지 칸(완충·리밸런싱 편차 등)은 제품이 정한다.
_CAPITAL_POLICY_KEYS: Final = ("reinvestRealizedPnl",)
_STRONG_LLM_KEYS: Final = (
    "provider",
    "modelId",
    "answerLanguage",
    "thinkingLevel",
    "aiJudgementEnabled",
)
_REQUEST_KEYS: Final = (
    "branch",
    "commitSha",
    "imageDigest",
    "pullRequest",
    "quantity",
    "requiredChecks",
    "securityEvidenceDigest",
    "symbol",
)
_RECEIPT_KEYS: Final = (
    "commitSha",
    "imageDigest",
    "inputSha256",
    "physicalCalls",
    "status",
    "timestamp",
)

#: 어떤 이유로도 프로필에 들어가면 안 되는 이름. 값이 아니라 키 이름으로 막는다.
#: 낱말 경계로 본다. `inputTokenCap` 의 token 처럼 상한 이름에 섞인 것까지 막으면
#: 정상 설정이 막혀 도구가 쓸모없어진다. 자격증명을 뜻하는 이름만 정확히 잡는다.
_FORBIDDEN: Final = re.compile(
    r"(?i)^(app_?key|app_?secret|api_?key|account_?no|account_?number|password|passphrase|"
    r"secret|access_?token|refresh_?token|bearer_?token|private_?key|credentials?|"
    r"user_?id|owner_?user_?id|dsn|connection_?string)$"
)


class ExportError(RuntimeError):
    """뽑을 수 없거나, 개인정보가 섞였다."""


def _reject_personal(value: object, path: str = "") -> None:
    """키 이름 기준으로 개인정보를 막는다. 값이 비어 있어도 키가 있으면 거부한다."""

    if isinstance(value, dict):
        for key, item in value.items():
            if _FORBIDDEN.search(str(key)):
                raise ExportError(f"P1_DEPLOY_PROFILE_PERSONAL_FIELD:{path}/{key}")
            _reject_personal(item, f"{path}/{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_personal(item, f"{path}[{index}]")


def _pick(source: dict[str, object], keys: tuple[str, ...], label: str) -> dict[str, object]:
    missing = [key for key in keys if key not in source]
    if missing:
        raise ExportError(f"P1_DEPLOY_PROFILE_MISSING:{label}:{','.join(missing)}")
    return {key: source[key] for key in keys}


def _psql(container: str, query: str) -> str:
    result = subprocess.run(
        ("docker", "exec", container, "psql", "-U", "postgres", "-d", "capstone_p1", "-At", "-c", query),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ExportError("P1_DEPLOY_PROFILE_DATABASE_UNREADABLE")
    return result.stdout.strip()


def build_profile(
    *,
    container: str,
    request_path: Path,
    receipt_path: Path,
    policy_file: Path | None,
) -> dict[str, object]:
    row = _psql(
        container,
        "select json_build_object("
        "'presetId', lower(risk_profile),"
        "'capitalLimitKrw', capital_limit_krw,"
        "'stopLossBps', stop_loss_bps,"
        "'takeProfitBps', take_profit_bps,"
        "'atrPeriod', atr_period,"
        "'atrMultiplierMilli', atr_multiplier_milli,"
        "'maxHoldingSessions', max_holding_sessions,"
        "'modelSellEnabled', model_sell_enabled)::text"
        " from automation_policy_versions order by version desc limit 1",
    )
    if not row:
        raise ExportError("P1_DEPLOY_PROFILE_POLICY_MISSING")
    policy = _pick(json.loads(row), _POLICY_KEYS, "automationPolicy")

    if policy_file is not None and policy_file.is_file():
        caps_source = json.loads(policy_file.read_bytes())
    else:
        caps_source = json.loads(
            (Path(__file__).parent / "deploy-profile.example.json").read_bytes()
        )["vertexCaps"]
    caps = _pick(caps_source, _VERTEX_CAP_KEYS, "vertexCaps")

    strong = _psql(
        container,
        "select json_build_object("
        "'provider', provider,"
        "'modelId', model_id,"
        "'answerLanguage', answer_language,"
        "'thinkingLevel', thinking_level,"
        "'aiJudgementEnabled', ai_judgement_enabled)::text"
        " from strong_llm_owner_settings limit 1",
    )
    strong_llm = _pick(json.loads(strong) if strong else {}, _STRONG_LLM_KEYS, "strongLlm")

    capital_row = _psql(
        container,
        "select json_build_object('reinvestRealizedPnl', reinvest_realized_pnl)::text"
        " from automation_capital_policy_versions_v1 order by version desc limit 1",
    )
    # 자본정책은 아직 없을 수 있다. 그때는 제품 기본값(재투자 없음)을 적는다.
    capital_policy = _pick(
        json.loads(capital_row) if capital_row else {"reinvestRealizedPnl": False},
        _CAPITAL_POLICY_KEYS,
        "capitalPolicy",
    )

    request = _pick(json.loads(request_path.read_bytes()), _REQUEST_KEYS, "certification.request")
    receipt = _pick(json.loads(receipt_path.read_bytes()), _RECEIPT_KEYS, "certification.receipt")

    profile = {
        "contractId": CONTRACT_ID,
        "automationPolicy": policy,
        "vertexCaps": caps,
        "capitalPolicy": capital_policy,
        "strongLlm": strong_llm,
        "certification": {"request": request, "receipt": receipt},
    }
    _reject_personal(profile)
    return profile


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default="capstone-p1-postgres-1")
    parser.add_argument("--request", type=Path, default=Path(".state-app/mock/certification-request.json"))
    parser.add_argument("--receipt", type=Path, default=Path(".state-app/mock/certification.json"))
    parser.add_argument("--vertex-policy", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        profile = build_profile(
            container=arguments.container,
            request_path=arguments.request,
            receipt_path=arguments.receipt,
            policy_file=arguments.vertex_policy,
        )
    except (ExportError, OSError, ValueError) as error:
        print(f"CAPSTONE_ERROR={error}", file=sys.stderr)
        return 1
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # 이 파일에는 개인정보가 없다(위 화이트리스트가 보장한다). 부트스트랩 컨테이너는
    # 65532 로 도므로 0700 으로 두면 프로필이 없는 것으로 읽혀 조용히 건너뛴다.
    arguments.out.parent.chmod(0o755)
    arguments.out.chmod(0o644)
    print("P1_DEPLOY_PROFILE=EXPORTED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
