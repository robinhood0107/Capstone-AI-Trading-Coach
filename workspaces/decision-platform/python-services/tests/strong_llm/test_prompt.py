from __future__ import annotations

from app.strong_llm.models import Evidence, RunRequest
from app.strong_llm.prompt import render_discovery_prompt, render_prompt, require_google_grounding


def test_owner_text_is_absent_from_google_discovery_prompt() -> None:
    owner_secret = "OWNER_PRIVATE_NEVER_DISCOVER"
    public_text = "PUBLIC_CONTEXT_ALLOWED"
    request = RunRequest(
        run_id="s49_run_" + "1" * 32,
        model_id="gemini-3.5-flash",
        question="공개 질문",
        answer_mode="DETAILED",
        related_symbols=(),
        topics=("RISK",),
        public_evidence=(Evidence(1, "cit_1", "rag_v2_chk_" + "b" * 32, public_text, "b" * 64),),
        owner_evidence=(
            Evidence(1, "cit_1", "rag_v2_chk_" + "a" * 32, owner_secret, "a" * 64, True),
        ),
        google_search_enabled=True,
        max_tool_rounds=3,
        current_time="2026-08-15T00:00:00Z",
        timezone="Asia/Seoul",
    )

    discovery = render_discovery_prompt(request)
    final = render_prompt(request, request.owner_evidence)

    assert owner_secret not in discovery.system + discovery.user
    assert public_text in discovery.user
    assert owner_secret in final.user


def test_google_prompt_requires_search_for_explicit_current_web_request_and_forbids_invented_sources() -> (
    None
):
    request = RunRequest(
        run_id="s49_run_" + "1" * 32,
        model_id="gemini-3.5-flash",
        question="현재 실제 웹 근거를 찾아 주세요.",
        answer_mode="CONCISE",
        related_symbols=(),
        topics=("RISK",),
        public_evidence=(),
        owner_evidence=(),
        google_search_enabled=True,
        max_tool_rounds=3,
        current_time="2026-08-15T00:00:00Z",
        timezone="Asia/Seoul",
    )

    prompt = require_google_grounding(render_prompt(request, ()))

    assert prompt.system.startswith("MANDATORY SEARCH POLICY")
    assert "use Google Search before drafting the response" in prompt.system
    assert "Do not answer such a request from memory" in prompt.system
    assert "Never invent citations or URLs" in prompt.system
    assert "INSUFFICIENT_EVIDENCE" in prompt.system
