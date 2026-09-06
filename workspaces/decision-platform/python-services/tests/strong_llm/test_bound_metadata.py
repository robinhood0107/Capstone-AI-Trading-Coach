"""근거 원문을 바꾸지 않고 중복 메타데이터 오류만 복구한다."""

from app.strong_llm.vertex_provider import _align_bound_metadata


def test_local_quotes_bind_citations_and_repeated_numbers_in_sentence_order():
    payload = {
        "basis": "EVIDENCE",
        "sentences": [
            {
                "text": "5%와 5%를 비교합니다.",
                "citationIds": ["cit_1", "cit_2"],
                "evidenceSpans": [{"citationId": "cit_1", "quote": "금리는 5%입니다."}],
                "numericSpans": [],
            }
        ],
    }
    _align_bound_metadata(payload)
    sentence = payload["sentences"][0]
    assert sentence["citationIds"] == ["cit_1"]
    assert sentence["numericSpans"] == [
        {"value": "5%", "citationIds": ["cit_1"]},
        {"value": "5%", "citationIds": ["cit_1"]},
    ]
    assert sentence["evidenceSpans"] == [{"citationId": "cit_1", "quote": "금리는 5%입니다."}]


def test_unbacked_number_is_not_given_a_fabricated_span():
    payload = {
        "basis": "EVIDENCE",
        "sentences": [
            {
                "text": "수익률은 9%입니다.",
                "citationIds": ["cit_1"],
                "evidenceSpans": [{"citationId": "cit_1", "quote": "수익률은 5%입니다."}],
                "numericSpans": [],
            }
        ],
    }
    _align_bound_metadata(payload)
    assert payload["sentences"][0]["numericSpans"] == []
