#!/usr/bin/env python3
"""레포 없는 서버에서 설정을 주입한다.

왜 있나
-------
`full-appctl` 이 호스트에서 하던 일 중에는 **설정 주입**이 섞여 있다. 정책 v3, 자본정책,
강한 LLM 설정이 그것이다. 새 서버에는 그 스크립트가 없고, 그래서 컨테이너가 전부 healthy
여도 자동운용은 `POLICY_NOT_CONFIGURED` 로 막힌다.

그 세 가지를 `deploy-profile.json` 에서 읽어 API 로 넣는다. 스크립트가 아니라 이미지에
들어 있으므로 레포 없이도 돈다.

무엇을 하지 않나
---------------
**무장하지 않는다.** 무장은 인증·시장데이터·잔고 관측이 모두 선 뒤에야 의미가 있고,
그 순서는 `mock_automation_cli` 가 이미 지킨다. 여기서 앞당기면 조용히 실패한다.

프로필이 없어도 돈다
------------------
기본 프로필이 이미지에 구워져 있다. 그래서 새 서버는 `.env` 와 `google.json` 만으로도
정책이 서고, 자동운용이 `POLICY_NOT_CONFIGURED` 로 막히지 않는다. 마운트된 프로필이
있으면 그것이 이긴다 - 공유받은 설정으로 같은 상태를 재현하기 위해서다.

기본값에는 개인정보가 없다. 담긴 것은 자본한도·손절·익절·ATR·보유기간, Vertex 상한,
강한 LLM 설정뿐이다. 인증 영수증은 읽지 않는다 - 그것은 `.state-app/mock/` 에 있다.

멱등하다
-------
이미 값이 있으면 건드리지 않는다. 다시 돌려도 같은 상태가 된다 - 그래야 재기동마다
운영자가 넣은 값을 덮어쓰지 않는다.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Final

_TIMEOUT: Final = 10
_READY_ATTEMPTS: Final = 60
#: 이미지에 구워진 기본 프로필. 마운트된 것이 없을 때 쓴다.
_DEFAULT_PROFILE: Final = "/opt/capstone/deploy-profile-default.json"


class BootstrapError(RuntimeError):
    """주입할 수 없다."""


def _key(prefix: str) -> str:
    """멱등 키. 16-128자에 `[A-Za-z0-9._:-]` 만 쓴다(IdempotencyKeyPolicy)."""

    return f"{prefix}-{secrets.token_hex(16)}"


def _call(
    base: str,
    path: str,
    *,
    method: str = "GET",
    token: str | None = None,
    body: dict[str, Any] | None = None,
    idempotency: str | None = None,
) -> tuple[int, dict[str, Any]]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if idempotency:
        headers["X-Idempotency-Key"] = idempotency
    request = urllib.request.Request(base + path, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        # 4xx 는 상태를 읽는 정보다. 본문이 없거나 JSON 이 아니어도 코드는 살린다.
        try:
            return error.code, json.loads(error.read() or b"{}")
        except ValueError:
            return error.code, {}


def _login(base: str, username: str, password: str) -> str:
    for _ in range(_READY_ATTEMPTS):
        try:
            status, value = _call(
                base,
                "/api/v1/auth/login",
                method="POST",
                body={"username": username, "password": password},
            )
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(2)
            continue
        if status == 200:
            token = value.get("data", {}).get("accessToken")
            if isinstance(token, str) and token:
                return token
            raise BootstrapError("P1_DEPLOY_BOOTSTRAP_LOGIN_MALFORMED")
        if status in (502, 503):
            time.sleep(2)
            continue
        raise BootstrapError(f"P1_DEPLOY_BOOTSTRAP_LOGIN_REJECTED:{status}")
    raise BootstrapError("P1_DEPLOY_BOOTSTRAP_API_UNREACHABLE")


def _put_policy(base: str, token: str, profile: dict[str, Any]) -> str:
    status, value = _call(base, "/api/v3/automation/status", token=token)
    if status != 200:
        raise BootstrapError(f"P1_DEPLOY_BOOTSTRAP_STATUS_UNREADABLE:{status}")
    if value.get("data", {}).get("policy"):
        return "PRESENT"
    policy = profile["automationPolicy"]
    body = {
        "capitalLimitKrw": policy["capitalLimitKrw"],
        "stopLossBps": policy["stopLossBps"],
        "takeProfitBps": policy["takeProfitBps"],
        "maxHoldingSessions": policy["maxHoldingSessions"],
        "atrPeriod": policy["atrPeriod"],
        "atrMultiplierMilli": policy["atrMultiplierMilli"],
        "modelSellEnabled": policy["modelSellEnabled"],
        "expectedVersion": 0,
    }
    # 프로필이 이 둘을 담고 있을 때만 보낸다. 없으면 제품 기본값이 이긴다.
    for optional in ("maxOpenPositions", "riskPerTradeBps"):
        if optional in policy:
            body[optional] = policy[optional]
    status, _ = _call(
        base,
        "/api/v3/automation/policy",
        method="PUT",
        token=token,
        body=body,
        idempotency=_key("deploy-policy"),
    )
    if status != 200:
        raise BootstrapError(f"P1_DEPLOY_BOOTSTRAP_POLICY_REJECTED:{status}")
    return "APPLIED"


def _put_capital_policy(base: str, token: str, profile: dict[str, Any]) -> str:
    status, _ = _call(base, "/api/v4/automation/capital-policy", token=token)
    if status == 200:
        return "PRESENT"
    if status not in (404, 409):
        raise BootstrapError(f"P1_DEPLOY_BOOTSTRAP_CAPITAL_UNREADABLE:{status}")
    reinvest = bool(profile.get("capitalPolicy", {}).get("reinvestRealizedPnl", False))
    status, _ = _call(
        base,
        "/api/v4/automation/capital-policy",
        method="PUT",
        token=token,
        body={"reinvestRealizedPnl": reinvest, "expectedVersion": 0},
        idempotency=_key("deploy-capital"),
    )
    if status != 200:
        raise BootstrapError(f"P1_DEPLOY_BOOTSTRAP_CAPITAL_REJECTED:{status}")
    return "APPLIED"


def _put_strong_llm(base: str, token: str, profile: dict[str, Any]) -> str:
    strong = profile["strongLlm"]
    body = {
        "provider": strong["provider"],
        "modelId": strong["modelId"],
        "answerLanguage": strong["answerLanguage"],
        "thinkingLevel": strong["thinkingLevel"],
        "aiJudgementEnabled": strong["aiJudgementEnabled"],
        "dailyGenerateCallCap": profile["vertexCaps"]["dailyGenerateCallCap"],
    }
    status, _ = _call(base, "/api/v2/strong-llm/settings", method="PUT", token=token, body=body)
    if status != 200:
        raise BootstrapError(f"P1_DEPLOY_BOOTSTRAP_STRONG_LLM_REJECTED:{status}")
    return "APPLIED"


def _readable(path: str) -> bool:
    """읽을 수 있는지 본다. 존재 여부만 보면 0700 디렉터리가 "없음"으로 읽힌다."""

    return os.path.isfile(path) and os.access(path, os.R_OK)


def _read_profile(path: str) -> dict[str, Any]:
    try:
        value = json.loads(open(path, "rb").read())
    except (OSError, ValueError) as error:
        raise BootstrapError("P1_DEPLOY_BOOTSTRAP_PROFILE_UNREADABLE") from error
    if not isinstance(value, dict) or value.get("contractId") != "p1-deploy-profile/v1":
        raise BootstrapError("P1_DEPLOY_BOOTSTRAP_PROFILE_INVALID")
    for required in ("automationPolicy", "strongLlm", "vertexCaps"):
        if not isinstance(value.get(required), dict):
            raise BootstrapError(f"P1_DEPLOY_BOOTSTRAP_PROFILE_MISSING:{required}")
    return value


def main() -> int:
    profile_path = os.environ.get("P1_DEPLOY_PROFILE", "/run/deploy/deploy-profile.json")
    source = "MOUNTED"
    if not _readable(profile_path):
        # 마운트된 프로필이 없으면 이미지에 구워진 기본값으로 선다. 새 서버가 .env 와
        # google.json 만으로 돌아야 한다는 것이 이 배포의 요구사항이다.
        profile_path, source = _DEFAULT_PROFILE, "DEFAULT"
    if not _readable(profile_path):
        print("P1_DEPLOY_BOOTSTRAP=FAILED reason=NO_PROFILE", file=sys.stderr)
        return 1
    base = os.environ.get("P1_BOOTSTRAP_BASE_URL", "http://decision-platform:8080")
    username = os.environ.get("P1_BOOTSTRAP_USERNAME", "demo-user")
    password_file = os.environ.get("P1_USER_PASSWORD_FILE", "/run/secrets/demo_user_password")
    try:
        profile = _read_profile(profile_path)
        password = open(password_file, encoding="utf-8").read().strip()
        token = _login(base, username, password)
        policy = _put_policy(base, token, profile)
        capital = _put_capital_policy(base, token, profile)
        strong = _put_strong_llm(base, token, profile)
    except (BootstrapError, OSError) as error:
        print(f"CAPSTONE_ERROR={error}", file=sys.stderr)
        print("P1_DEPLOY_BOOTSTRAP=FAILED")
        return 1
    print(
        f"P1_DEPLOY_BOOTSTRAP=PASS profile={source} policy={policy}"
        f" capitalPolicy={capital} strongLlm={strong}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
