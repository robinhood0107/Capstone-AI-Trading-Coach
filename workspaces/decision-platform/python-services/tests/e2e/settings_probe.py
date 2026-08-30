"""설정 저장과 읽기가 실제로 도는지, 키가 어디로도 새지 않는지 확인한다."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from harness import Api  # noqa: E402

SECRET = "sk-probe-0123456789ABCDefgh"


def status(api: Api) -> dict[str, object]:
    _, body = api.request("GET", "/api/v2/rag/corpus-status")
    return {key: value for key, value in body.items() if key.startswith("strongLlm")}


def put(api: Api, body: dict[str, object]) -> tuple[int, dict[str, object]]:
    return api.request(
        "PUT",
        "/api/v2/strong-llm/settings",
        body,
        headers={"X-Request-Id": f"req_{uuid.uuid4().hex}"},
    )


def main() -> int:
    api = Api()
    api.login()
    observed: dict[str, object] = {"before": status(api)}

    settings: dict[str, object] = {
        "provider": "openai",
        "fallbackProvider": "vertex",
        "modelId": "gpt-5",
        "fallbackModelId": None,
        "baseUrl": None,
        "fallbackBaseUrl": None,
        "answerLanguage": "ko",
        "dailyGenerateCallCap": 25,
        "apiKey": SECRET,
    }
    code, body = put(api, settings)
    observed["putStatus"] = code
    # 응답 본문 자체가 없어야 한다. 키를 담을 수 있는 자리를 만들지 않는 것이 요점이다.
    observed["putBodyKeys"] = sorted(body)
    observed["afterKeyWrite"] = status(api)

    # 키 없이 설정만 바꾼다. 저장된 키가 그대로 남아야 한다.
    code, _ = put(api, {**settings, "apiKey": None, "dailyGenerateCallCap": 40})
    observed["putSettingsOnlyStatus"] = code
    observed["afterSettingsOnly"] = status(api)

    # 빈 문자열은 지운다는 뜻이다.
    code, _ = put(api, {**settings, "apiKey": "", "provider": "vertex"})
    observed["putClearStatus"] = code
    observed["afterClear"] = status(api)

    # 알 수 없는 필드와 짧은 키는 닫혀야 한다.
    observed["unknownField"] = put(api, {**settings, "nope": 1})[0]
    observed["shortKey"] = put(api, {**settings, "apiKey": "abc"})[0]
    observed["badProvider"] = put(api, {**settings, "provider": "hacker"})[0]
    observed["customWithoutUrl"] = put(api, {**settings, "provider": "custom"})[0]

    print(json.dumps(observed, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
