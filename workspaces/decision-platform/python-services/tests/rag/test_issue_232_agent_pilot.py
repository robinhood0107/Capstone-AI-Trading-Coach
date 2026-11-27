from app.rag.issue_232_agent_pilot import ARMS, allowed_route, evaluate_agent


def test_linked_pilot_preserves_call_budget_and_as_of_hold():
    rows = {(row["case"], row["arm"]): row for row in evaluate_agent(None)}
    assert len(rows) == 9
    assert {arm for _, arm in rows} == set(ARMS)
    for arm in ARMS:
        result = rows["A3", arm]
        assert result["passed"]
        assert result["supplied"] == ["ocean_notice"]
        assert result["lookup_calls"] == (1 if arm == "single_lookup" else 2)
        assert result["inference_calls"] == 0
    for case in ("A1", "A2"):
        assert not rows[case, "single_lookup"]["passed"]
        assert rows[case, "deterministic_two_step"]["passed"]
        assert rows[case, "bounded_model_router"]["passed"]


def test_router_rejects_unlinked_keys_and_missing_first_source_citation():
    links = {"correction": "IRIS-CORRECTION"}
    result = {"status": "answer", "value": "IRIS-CORRECTION", "citations": ["iris_notice"]}
    assert allowed_route(result, links, "iris_notice") == "IRIS-CORRECTION"
    assert allowed_route({**result, "value": "OTHER-CATALOG"}, links, "iris_notice") is None
    assert allowed_route({**result, "citations": []}, links, "iris_notice") is None
