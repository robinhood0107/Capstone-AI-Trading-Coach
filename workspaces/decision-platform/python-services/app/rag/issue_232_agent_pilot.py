"""Separate #232 linked-document pilot with at most two official reads."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections.abc import Iterator

from app.rag.issue_232_pilot import (
    DOC_FIELDS,
    FIXTURE,
    OUTPUT_FIELDS,
    Case,
    Document,
    context_for,
    fixture_output,
    generate,
    hold,
    score,
)

AGENT_FIXTURE = FIXTURE.with_name("issue-232-agent.v1.json")
ARMS = ("single_lookup", "deterministic_two_step", "bounded_model_router")


class AgentCase(Case):
    relation: str


def load_agent_fixture() -> tuple[
    str, str, dict[str, Document], dict[str, dict[str, str]], list[AgentCase]
]:
    raw = AGENT_FIXTURE.read_bytes()
    data = json.loads(raw)
    docs = {row[0]: dict(zip(DOC_FIELDS, row, strict=True)) for row in data["documents"]}
    return (
        data["protocol"],
        hashlib.sha256(raw).hexdigest(),
        docs,
        data["links_by_document"],
        data["cases"],
    )


def routing_question(case: AgentCase) -> str:
    return (
        f'The original question is "{case["question"]}" The required linked document relation '
        f'is "{case["relation"]}". Read the first official document and its list of link labels '
        "and catalog keys. Answer with only the catalog key matching that relation, and cite "
        "the first document ID as the source of the link. Hold if the relation is missing."
    )


def allowed_route(result: object, links: dict[str, str], source: str) -> str | None:
    if (
        isinstance(result, dict)
        and set(result) == set(OUTPUT_FIELDS)
        and result.get("status") == "answer"
        and isinstance(key := result.get("value"), str)
        and key in links.values()
        and result.get("citations") == [source]
    ):
        return key
    return None


def evaluate_agent(port: int | None) -> Iterator[dict[str, object]]:
    protocol, checksum, docs, links_by_document, cases = load_agent_fixture()
    for index, case in enumerate(cases):
        for arm in ARMS[index:] + ARMS[:index]:
            started = time.perf_counter_ns()
            initial_docs, _ = context_for("official_lookup", case, docs)
            (initial,) = initial_docs
            links = links_by_document[initial["id"]]
            desired_key = links.get(case["relation"])
            route_result: object = None
            route_raw = route_finish = route_ms = None
            calls = 0
            route_usage: dict[str, int] = {}
            key = desired_key if arm == "deterministic_two_step" else None
            if arm == "bounded_model_router":
                route_case: AgentCase = {**case, "question": routing_question(case)}
                route_doc = {**initial, "linked_catalog_keys": json.dumps(links, sort_keys=True)}
                if port is None:
                    route_result = (
                        {"status": "answer", "value": desired_key, "citations": [initial["id"]]}
                        if desired_key
                        else hold()
                    )
                else:
                    route_start = time.perf_counter_ns()
                    route_result, route_raw, route_usage, route_finish = generate(
                        port, route_case, [route_doc]
                    )
                    route_ms = (time.perf_counter_ns() - route_start) / 1e6
                    calls += 1
                key = allowed_route(route_result, links, initial["id"])

            followup_case: AgentCase = {**case, "lookup": key}
            followup, _ = context_for("official_lookup", followup_case, docs)
            supplied_docs = initial_docs + followup
            supplied = {doc["id"] for doc in supplied_docs}
            raw = finish_reason = answer_ms = None
            answer_usage: dict[str, int] = {}
            answer: object
            if port is None:
                answer = fixture_output(case, supplied)
            else:
                answer_start = time.perf_counter_ns()
                answer, raw, answer_usage, finish_reason = generate(port, case, supplied_docs)
                answer_ms = (time.perf_counter_ns() - answer_start) / 1e6
                calls += 1
            yield {
                "protocol": protocol,
                "fixture_sha256": checksum,
                "backend": "fixture_oracle" if port is None else "llama_cpp_loopback",
                "arm": arm,
                "case": case["id"],
                "group": case["group"],
                "lookup_calls": 1 + int(key is not None),
                "inference_calls": calls,
                "route_result": route_result,
                "route_raw": route_raw,
                "route_finish": route_finish,
                "route_key": key,
                "route_expected": desired_key,
                "route_ms": route_ms,
                "answer_ms": answer_ms,
                "followup_found": bool(followup),
                "supplied": [doc["id"] for doc in supplied_docs],
                "result": answer,
                "raw": raw,
                "finish_reason": finish_reason,
                "route_usage": route_usage,
                "answer_usage": answer_usage,
                "total_ms": None if port is None else (time.perf_counter_ns() - started) / 1e6,
                **score(case, answer, supplied),
            }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llama-port", type=int, choices=range(1024, 65536), metavar="PORT")
    options = parser.parse_args()
    for row in evaluate_agent(options.llama_port):
        print(json.dumps(row, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
