from __future__ import annotations

import pytest

from app.rag.authorized_retrieval import (
    ALLOWED_RAG_TOPICS,
    QueryNormalizer,
    QueryValidationError,
)

# 실제로 일어난 사고를 부류째 막는다.
#
# topics 상한이 5 인데 허용 토픽은 여섯 종이었다. 화면이 여섯 종을 모두 보내면 질의가
# RAG_QUERY_INVALID 로 닫혔고, 금융 Agent 가 어떤 질문에도 답하지 못했다. 실패 코드는
# 사유를 담지 않아 증상만 보고는 원인을 알 수 없었다.
#
# 아래 검사는 "허용한 값을 전부 골라도 통과한다"를 못박는다. 토픽을 더 추가하면서
# 상한을 같이 올리지 않으면 여기서 먼저 걸린다.


def test_selecting_every_allowed_topic_is_accepted() -> None:
    """허용 목록에 있는 값을 전부 고르는 것은 언제나 유효한 요청이다."""

    query = QueryNormalizer().normalize(
        {
            "question": "MDD란 무엇인가요?",
            "answerMode": "CONCISE",
            "topics": sorted(ALLOWED_RAG_TOPICS),
        }
    )
    assert set(query.topics) == set(ALLOWED_RAG_TOPICS)


def test_topic_bound_is_never_smaller_than_the_allowed_list() -> None:
    """상한이 목록보다 작아지는 순간을 코드로 잡는다."""

    topics = sorted(ALLOWED_RAG_TOPICS)
    for size in range(1, len(topics) + 1):
        query = QueryNormalizer().normalize(
            {
                "question": "Sharpe 비율은 무엇인가요?",
                "answerMode": "CONCISE",
                "topics": topics[:size],
            }
        )
        assert len(query.topics) == size


def test_unknown_topic_is_still_rejected() -> None:
    """상한을 넓혔다고 아무 값이나 받지는 않는다."""

    with pytest.raises(QueryValidationError):
        QueryNormalizer().normalize(
            {
                "question": "MDD란 무엇인가요?",
                "answerMode": "CONCISE",
                "topics": ["NOT_A_TOPIC"],
            }
        )
