"""같은 내용의 한·영 질문 쌍으로 검색과 생성이 어디서 갈리는지 잰다.

기존 기록은 한국어 "분산투자"와 영어 "Sharpe ratio"를 비교했다. 두 질문의 주제가 달라서
언어 때문에 닫힌 것인지 그 주제의 근거가 없어서 닫힌 것인지 구분되지 않는다. 여기서는 같은
내용을 두 언어로 물어 그 둘을 갈라 본다.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from harness import Api, HarnessError  # noqa: E402

TOPICS = ["FINANCIAL_ENGINEERING", "RISK"]

PAIRS = (
    (
        "sharpe",
        "What does the Sharpe ratio measure in portfolio risk analysis?",
        "포트폴리오 위험 분석에서 샤프 지수는 무엇을 측정하는가?",
    ),
    (
        "diversification",
        "Why does diversification reduce portfolio risk?",
        "분산투자가 포트폴리오 위험을 줄이는 이유는 무엇인가?",
    ),
    (
        "drawdown",
        "What does maximum drawdown measure?",
        "최대낙폭은 무엇을 측정하는가?",
    ),
)


CONSENT_BODY = {
    "contractId": "s4-rag-v2-external-consent-v1",
    "schemaVersion": 1,
    "consentType": "EXTERNAL_AI_RAG_V2",
}


def consent(api: Api, action: str, digest: str = "1") -> int:
    status, _ = api.request(
        "POST",
        "/api/v2/rag/consents",
        {
            **CONSENT_BODY,
            "action": action,
            "disclosureDigest": digest * 64,
            "policyDigest": digest * 64,
            "processorSetDigest": digest * 64,
        },
        headers={"X-Request-Id": f"req_{uuid.uuid4().hex}"},
    )
    return status


def ask(api: Api, question: str) -> dict[str, object]:
    """자동 저술이 켜진 배포의 실제 클라이언트 경로다.

    scope claim을 직접 준비해 보내면 서버는 운영자가 패킷을 저술했다고 보고 자동 저술을
    건너뛴다. 그러면 패킷이 없어 생성이 늘 닫히고, 그 결과로는 언어를 잴 수 없다.
    """

    ask_status, body = api.request(
        "POST",
        "/api/v2/rag/ask",
        {"question": question, "answerMode": "DETAILED", "topics": TOPICS},
        headers={"X-Request-Id": f"req_{uuid.uuid4().hex}"},
    )
    answer = body.get("answer")
    return {
        "askStatus": ask_status,
        "generationStatus": body.get("generationStatus"),
        "citations": len(body.get("citations") or []),
        "guardrailFlags": body.get("guardrailFlags") or [],
        "citationCoverage": body.get("citationCoverage"),
        "answerChars": len(answer) if isinstance(answer, str) else 0,
        "answer": answer,
    }


def main() -> int:
    api = Api()
    api.login()
    if consent(api, "GRANT") not in {200, 201, 204}:
        raise HarnessError("consent grant failed")
    observed: dict[str, object] = {}
    for name, english, korean in PAIRS:
        observed[f"{name}.en"] = ask(api, english)
        observed[f"{name}.ko"] = ask(api, korean)
    print(json.dumps(observed, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
