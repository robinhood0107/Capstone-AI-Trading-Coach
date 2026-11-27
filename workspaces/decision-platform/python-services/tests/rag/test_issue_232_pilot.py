from app.rag.issue_232_pilot import ARMS, context_for, evaluate, hold, load_fixture, score


def test_frozen_pilot_has_every_arm_and_keeps_restricted_and_future_text_out():
    _, _, docs, cases = load_fixture()
    rows = list(evaluate(None))
    assert len(rows) == 32
    assert len({(row["case"], row["arm"]) for row in rows}) == 32
    assert {row["arm"] for row in rows} == set(ARMS)
    assert all(row["backend"] == "fixture_oracle" and row["inference_ms"] is None for row in rows)
    assert all({"private", "future"}.isdisjoint(row["supplied"]) for row in rows)
    assert all(row["passed"] for row in rows if row["group"] == "permission_hold")
    by_case = {case["id"]: case for case in cases}
    picked, failure = context_for("rag_v2", by_case["T2"], docs)
    assert failure is None
    assert {"limit", "override"} <= {doc["id"] for doc in picked}
    picked, _ = context_for("official_lookup", by_case["T1"], docs)
    assert [doc["id"] for doc in picked] == ["correction"]


def test_a_correct_number_with_stale_or_unsupplied_citations_fails():
    _, _, _, cases = load_fixture()
    case = next(case for case in cases if case["id"] == "T1")
    for cites, supplied in [(["original"], {"original"}), (["correction"], set())]:
        verdict = score(case, {"status": "answer", "value": "37", "citations": cites}, supplied)
        assert verdict["answer_ok"]
        assert not verdict["citations_ok"]
        assert not verdict["passed"]


def test_hold_requires_no_value_and_detects_the_synthetic_restricted_marker():
    _, _, _, cases = load_fixture()
    case = next(case for case in cases if case["id"] == "H1")
    assert score(case, hold(), {"public"})["passed"]
    verdict = score(case, {**hold(), "value": "orchid-8841"}, {"public"})
    assert not verdict["valid"]
    assert verdict["value_exposed"]
    assert not verdict["passed"]
