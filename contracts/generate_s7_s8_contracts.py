"""Generate the closed S7 async and solo-S8 dashboard contracts."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
from typing import Any, Final, Mapping

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.generated_artifact_io import write_generated_path  # noqa: E402
from contracts.generate_principle_contracts import ContractValidationError  # noqa: E402

SCHEMA_IDS: Final[tuple[str, ...]] = (
    "async-event-envelope.v1",
    "async-dlq-envelope.v1",
    "async-job-status.v1",
    "async-job-list.v1",
    "stream-metric-status.v1",
    "artifact-ingest-status.v1",
    "dashboard-model-evaluation.v1",
    "dashboard-backtest.v1",
    "dashboard-risk-result.v1",
    "dashboard-rag-sources.v1",
)
SCHEMA_PATHS: Final[dict[str, str]] = {
    schema_id: f"contracts/schemas/{schema_id}.schema.json" for schema_id in SCHEMA_IDS
}
BASE_TOPICS: Final[tuple[str, ...]] = (
    "artifact.ingest-requested.v1",
    "artifact.ingested.v1",
    "signal.received.v1",
    "feature.updated.v1",
    "lightgbm.signal-generated.v1",
    "risk.context-updated.v1",
    "risk.decision-created.v1",
    "order.event-created.v1",
    "rag.index-requested.v1",
    "rag.index-completed.v1",
    "model.eval-requested.v1",
    "model.eval-completed.v1",
)
PUBLIC_PATHS: Final[tuple[str, ...]] = (
    "/api/v1/async-jobs/{jobId}",
    "/api/v1/async-jobs",
    "/api/v1/stream-metrics",
    "/api/v1/artifacts/ingest-status",
    "/api/v1/dashboard/model-evaluations/{runId}",
    "/api/v1/dashboard/backtests/{runId}",
    "/api/v1/dashboard/risk-results/{decisionId}",
    "/api/v1/dashboard/rag-sources/{answerId}",
)


def _closed(required: list[str], properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def _timestamp(*, nullable: bool = False) -> dict[str, Any]:
    value = {"type": "string", "format": "date-time"}
    return {"oneOf": [value, {"type": "null"}]} if nullable else value


def _nullable_string(pattern: str | None = None) -> dict[str, Any]:
    string: dict[str, Any] = {"type": "string", "maxLength": 128}
    if pattern is not None:
        string["pattern"] = pattern
    return {"oneOf": [string, {"type": "null"}]}


def _schema(schema_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_PATHS[schema_id],
        "title": schema_id,
        **body,
    }


def _success(data: dict[str, Any]) -> dict[str, Any]:
    return _closed(
        ["success", "requestId", "data", "warnings", "error"],
        {
            "success": {"const": True},
            "requestId": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$"},
            "data": data,
            "warnings": {"type": "array", "maxItems": 20, "items": {"type": "object"}},
            "error": {"type": "null"},
        },
    )


def _event_schema() -> dict[str, Any]:
    references = _closed(
        [],
        {
            "ownerRef": {"type": "string", "pattern": "^usr_[A-Za-z0-9_-]{8,64}$"},
            "sourceRevisionId": {"type": "string", "pattern": "^srv_[a-z0-9][a-z0-9_-]{2,95}$"},
            "importTicketId": {"type": "string", "pattern": "^rti_[0-9a-f]{32}$"},
            "profileId": {"enum": ["bge_m3_local_1024_v1", "voyage_context_4_1024_v1"]},
            "artifactId": {"type": "string", "pattern": "^artifact_[A-Za-z0-9_-]{8,96}$"},
            "runId": {"type": "string", "pattern": "^(run|demo)_[A-Za-z0-9_-]{8,96}$"},
            "jobId": {"type": "string", "pattern": "^job_[A-Za-z0-9_-]{8,96}$"},
            "contentHash": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
            "resultRef": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_-]{7,127}$"},
            "replayOf": {"type": "string", "pattern": "^evt_[A-Za-z0-9_-]{8,96}$"},
        },
    )
    return _schema(
        "async-event-envelope.v1",
        _closed(
            ["eventId", "eventType", "schemaVersion", "occurredAt", "partitionKey", "payloadHash", "references"],
            {
                "eventId": {"type": "string", "pattern": "^evt_[A-Za-z0-9_-]{8,96}$"},
                "eventType": {"enum": list(BASE_TOPICS)},
                "schemaVersion": {"const": 1},
                "occurredAt": _timestamp(),
                "partitionKey": {"type": "string", "pattern": "^hmac-sha256:[0-9a-f]{64}$"},
                "payloadHash": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
                "references": references,
            },
        ),
    )


def _dlq_event_schema() -> dict[str, Any]:
    references = _closed(
        ["eventId", "eventType", "payloadHash", "failureCode", "sourceTopic", "attempt"],
        {
            "eventId": {"type": "string", "pattern": "^evt_[A-Za-z0-9_-]{8,96}$"},
            "eventType": {"enum": list(BASE_TOPICS)},
            "payloadHash": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
            "failureCode": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]{2,63}$"},
            "sourceTopic": {"enum": list(BASE_TOPICS)},
            "attempt": {"type": "integer", "minimum": 1, "maximum": 3},
        },
    )
    return _schema(
        "async-dlq-envelope.v1",
        _closed(
            ["eventId", "eventType", "schemaVersion", "occurredAt", "partitionKey", "payloadHash", "references"],
            {
                "eventId": {"type": "string", "pattern": "^evt_dlq_[0-9a-f]{32}$"},
                "eventType": {"enum": list(BASE_TOPICS)},
                "schemaVersion": {"const": 1},
                "occurredAt": _timestamp(),
                "partitionKey": {"type": "string", "pattern": "^hmac-sha256:[0-9a-f]{64}$"},
                "payloadHash": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
                "references": references,
            },
        ),
    )


def _job_item() -> dict[str, Any]:
    return _closed(
        ["jobId", "type", "status", "requestedAt", "startedAt", "completedAt", "sourceId", "artifactId", "resultRef", "error"],
        {
            "jobId": {"type": "string", "pattern": "^job_[A-Za-z0-9_-]{8,96}$"},
            "type": {"enum": ["RAG_INDEX", "ARTIFACT_INGEST", "MODEL_EVAL"]},
            "status": {"enum": ["REQUESTED", "RUNNING", "COMPLETED", "FAILED", "NEEDS_REVIEW"]},
            "requestedAt": _timestamp(),
            "startedAt": _timestamp(nullable=True),
            "completedAt": _timestamp(nullable=True),
            "sourceId": _nullable_string("^src_[A-Za-z0-9_-]{8,96}$"),
            "artifactId": _nullable_string("^artifact_[A-Za-z0-9_-]{8,96}$"),
            "resultRef": _nullable_string("^[A-Za-z][A-Za-z0-9_-]{7,127}$"),
            "error": {
                "oneOf": [
                    _closed(
                        ["code", "class"],
                        {
                            "code": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]{2,63}$"},
                            "class": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]{2,63}$"},
                        },
                    ),
                    {"type": "null"},
                ]
            },
        },
    )


def _job_status_schema() -> dict[str, Any]:
    return _schema("async-job-status.v1", _success(_job_item()))


def _job_list_schema() -> dict[str, Any]:
    data = _closed(
        ["items", "nextCursor"],
        {
            "items": {"type": "array", "items": _job_item(), "maxItems": 100},
            "nextCursor": {
                "oneOf": [
                    {
                        "type": "string",
                        "minLength": 16,
                        "maxLength": 256,
                        "pattern": "^[A-Za-z0-9_-]{16,256}$",
                    },
                    {"type": "null"},
                ]
            },
        },
    )
    return _schema("async-job-list.v1", _success(data))


def _stream_metric_schema() -> dict[str, Any]:
    component = _closed(
        ["status", "observedAt"],
        {"status": {"enum": ["OK", "EMPTY", "UNAVAILABLE"]}, "observedAt": _timestamp(nullable=True)},
    )
    data = _closed(
        ["lastUpdatedAt", "pipelineHealth", "signalStaleRatio", "decisionDistribution", "failedJobCount", "dlqEventCount", "components"],
        {
            "lastUpdatedAt": _timestamp(nullable=True),
            "pipelineHealth": {"enum": ["OK", "DEGRADED", "UNAVAILABLE"]},
            "signalStaleRatio": {"oneOf": [{"type": "number", "minimum": 0, "maximum": 1}, {"type": "null"}]},
            "decisionDistribution": _closed(
                ["ALLOW", "WARN", "HOLD", "BLOCK"],
                {key: {"type": "integer", "minimum": 0} for key in ("ALLOW", "WARN", "HOLD", "BLOCK")},
            ),
            "failedJobCount": {"type": "integer", "minimum": 0},
            "dlqEventCount": {"type": "integer", "minimum": 0},
            "components": _closed(
                ["decisionDistribution", "signalFreshness", "failedJobs", "dlqEvents"],
                {key: copy.deepcopy(component) for key in ("decisionDistribution", "signalFreshness", "failedJobs", "dlqEvents")},
            ),
        },
    )
    return _schema("stream-metric-status.v1", _success(data))


def _artifact_ingest_schema() -> dict[str, Any]:
    item = _closed(
        ["artifactId", "fileName", "producer", "runId", "fileHash", "schemaVersion", "status", "lastIngestedAt", "duplicate"],
        {
            "artifactId": {"type": "string", "pattern": "^artifact_[A-Za-z0-9_-]{8,96}$"},
            "fileName": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"},
            "producer": {"enum": ["decision-platform", "return-engine"]},
            "runId": {"type": "string", "pattern": "^(run|demo)_[A-Za-z0-9_-]{8,96}$"},
            "fileHash": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
            "schemaVersion": {"type": "string", "pattern": "^[1-9][0-9]*\\.[0-9]+\\.[0-9]+$"},
            "status": {"enum": ["DISCOVERED", "VALIDATED", "INGESTED", "FAILED", "SKIPPED"]},
            "lastIngestedAt": _timestamp(nullable=True),
            "duplicate": {"type": "boolean"},
        },
    )
    return _schema(
        "artifact-ingest-status.v1",
        _success(_closed(["items"], {"items": {"type": "array", "items": item, "maxItems": 100}})),
    )


def _view_envelope(schema_id: str, view: dict[str, Any]) -> dict[str, Any]:
    data = _closed(
        ["viewState", "asOf", "freshUntil", "evidenceMode", "performanceClaimAllowed", "view"],
        {
            "viewState": {"enum": ["READY", "EMPTY", "STALE"]},
            "asOf": _timestamp(nullable=True),
            "freshUntil": _timestamp(nullable=True),
            "evidenceMode": {"enum": ["STORED_RUNTIME", "REAL_ARTIFACT", "SYNTHETIC_DEMO"]},
            "performanceClaimAllowed": {"const": False},
            "view": {"oneOf": [view, {"type": "null"}]},
        },
    )
    return _schema(schema_id, _success(data))


def _metric_values() -> dict[str, Any]:
    nullable_number = {"oneOf": [{"type": "number"}, {"type": "null"}]}
    return _closed(
        ["cagr", "mdd", "sharpe", "sortino", "var95", "cvar95"],
        {key: copy.deepcopy(nullable_number) for key in ("cagr", "mdd", "sharpe", "sortino", "var95", "cvar95")},
    )


def _model_view_schema() -> dict[str, Any]:
    model = _closed(
        ["modelId", "status", "metrics"],
        {
            "modelId": {"enum": ["BASELINE", "LSTM", "LIGHTGBM"]},
            "status": {"enum": ["AVAILABLE", "ABSTAIN"]},
            "metrics": _metric_values(),
        },
    )
    point = _closed(["at", "value"], {"at": _timestamp(), "value": {"type": "number"}})
    view = _closed(
        ["runId", "models", "timeline", "sourceRunIds"],
        {
            "runId": {"type": "string", "pattern": "^(run|demo)_[A-Za-z0-9_-]{8,96}$"},
            "models": {"type": "array", "items": model, "minItems": 1, "maxItems": 3},
            "timeline": {"type": "array", "items": point, "maxItems": 500},
            "sourceRunIds": {"type": "array", "items": {"type": "string", "maxLength": 96}, "maxItems": 8, "uniqueItems": True},
        },
    )
    return _view_envelope("dashboard-model-evaluation.v1", view)


def _backtest_view_schema() -> dict[str, Any]:
    point = _closed(["at", "value"], {"at": _timestamp(), "value": {"type": "number"}})

    def strategy(name: str) -> dict[str, Any]:
        return _closed(
            ["strategy", "metrics", "curve"],
            {
                "strategy": {"const": name},
                "metrics": _metric_values(),
                "curve": {"type": "array", "items": point, "maxItems": 2000},
            },
        )
    heatmap = _closed(
        ["month", "return"],
        {"month": {"type": "string", "pattern": "^[0-9]{4}-(0[1-9]|1[0-2])$"}, "return": {"type": "number"}},
    )
    card = _closed(["metric", "value"], {"metric": {"type": "string", "maxLength": 48}, "value": {"oneOf": [{"type": "number"}, {"type": "null"}]}})
    view = _closed(
        ["runId", "fixtureClass", "strategies", "heatmap", "metricCards", "projectionHash"],
        {
            "runId": {"type": "string", "pattern": "^(run|demo)_[A-Za-z0-9_-]{8,96}$"},
            "fixtureClass": {"enum": ["REAL_ARTIFACT", "SYNTHETIC_FAKE_E2E"]},
            "strategies": {"type": "array", "prefixItems": [strategy("Baseline"), strategy("Guide"), strategy("Strict")], "items": False, "minItems": 3, "maxItems": 3},
            "heatmap": {"type": "array", "items": heatmap, "maxItems": 120},
            "metricCards": {"type": "array", "items": card, "maxItems": 11},
            "projectionHash": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
        },
    )
    return _view_envelope("dashboard-backtest.v1", view)


def _risk_view_schema() -> dict[str, Any]:
    text_item = {"type": "string", "minLength": 1, "maxLength": 256}
    risk_item = _closed(
        ["code", "severity", "summary"],
        {
            "code": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]{2,63}$"},
            "severity": {"enum": ["INFO", "WARN", "BLOCK"]},
            "summary": text_item,
        },
    )
    view = _closed(
        ["decisionId", "action", "reasons", "principles", "riskItems"],
        {
            "decisionId": {"type": "string", "pattern": "^dec_[A-Za-z0-9_-]{8,96}$"},
            "action": {"enum": ["ALLOW", "WARN", "HOLD", "BLOCK"]},
            "reasons": {"type": "array", "items": text_item, "maxItems": 20},
            "principles": {"type": "array", "items": text_item, "maxItems": 20},
            "riskItems": {"type": "array", "items": risk_item, "maxItems": 20},
        },
    )
    return _view_envelope("dashboard-risk-result.v1", view)


def _rag_view_schema() -> dict[str, Any]:
    source = _closed(
        ["sourceId", "title", "classification", "summary"],
        {
            "sourceId": {"type": "string", "pattern": "^src_[A-Za-z0-9_-]{8,96}$"},
            "title": {"type": "string", "minLength": 1, "maxLength": 160},
            "classification": {"enum": ["OFFICIAL", "SCHOLARLY", "INTERNAL_PAPER"]},
            "summary": {"type": "string", "minLength": 1, "maxLength": 512},
        },
    )
    view = _closed(
        ["answerId", "topSources", "expandableSources"],
        {
            "answerId": {"type": "string", "pattern": "^rag_[A-Za-z0-9_-]{12,96}$"},
            "topSources": {"type": "array", "items": source, "maxItems": 3},
            "expandableSources": {"type": "array", "items": source, "maxItems": 5},
        },
    )
    return _view_envelope("dashboard-rag-sources.v1", view)


def build_schemas() -> dict[str, dict[str, Any]]:
    schemas = {
        "async-event-envelope.v1": _event_schema(),
        "async-dlq-envelope.v1": _dlq_event_schema(),
        "async-job-status.v1": _job_status_schema(),
        "async-job-list.v1": _job_list_schema(),
        "stream-metric-status.v1": _stream_metric_schema(),
        "artifact-ingest-status.v1": _artifact_ingest_schema(),
        "dashboard-model-evaluation.v1": _model_view_schema(),
        "dashboard-backtest.v1": _backtest_view_schema(),
        "dashboard-risk-result.v1": _risk_view_schema(),
        "dashboard-rag-sources.v1": _rag_view_schema(),
    }
    if tuple(schemas) != SCHEMA_IDS:
        raise ContractValidationError("S7/S8 schema order drifted.")
    return schemas


def _fixtures() -> dict[str, dict[str, Any]]:
    ts = "2026-08-22T00:00:00Z"
    later = "2026-08-22T00:05:00Z"
    metric_values = {"cagr": None, "mdd": None, "sharpe": None, "sortino": None, "var95": None, "cvar95": None}
    empty_view = {"viewState": "EMPTY", "asOf": None, "freshUntil": None, "evidenceMode": "SYNTHETIC_DEMO", "performanceClaimAllowed": False, "view": None}
    job = {
        "jobId": "job_rag_index_00000001", "type": "RAG_INDEX", "status": "COMPLETED", "requestedAt": ts,
        "startedAt": ts, "completedAt": later, "sourceId": "src_fixture_00000001", "artifactId": None,
        "resultRef": "rag_index_result_00000001", "error": None,
    }
    component_ok = {"status": "OK", "observedAt": ts}
    fixtures: dict[str, dict[str, Any]] = {
        "async-event-envelope.v1": {
            "eventId": "evt_rag_index_00000001", "eventType": "rag.index-requested.v1", "schemaVersion": 1,
            "occurredAt": ts, "partitionKey": "hmac-sha256:" + "1" * 64, "payloadHash": "sha256:" + "2" * 64,
            "references": {"ownerRef": "usr_fixture_00000001", "sourceRevisionId": "srv_fixture_00000001", "importTicketId": "rti_" + "2" * 32, "profileId": "bge_m3_local_1024_v1", "jobId": "job_rag_index_00000001", "contentHash": "sha256:" + "3" * 64},
        },
        "async-dlq-envelope.v1": {
            "eventId": "evt_dlq_" + "a" * 32, "eventType": "rag.index-requested.v1", "schemaVersion": 1,
            "occurredAt": ts, "partitionKey": "hmac-sha256:" + "6" * 64, "payloadHash": "sha256:" + "7" * 64,
            "references": {
                "eventId": "evt_rag_index_00000001", "eventType": "rag.index-requested.v1",
                "payloadHash": "sha256:" + "2" * 64, "failureCode": "INVALID_EVENT_PAYLOAD",
                "sourceTopic": "rag.index-requested.v1", "attempt": 1,
            },
        },
        "async-job-status.v1": {"success": True, "data": job},
        "async-job-list.v1": {"success": True, "data": {"items": [job], "nextCursor": None}},
        "stream-metric-status.v1": {"success": True, "data": {"lastUpdatedAt": ts, "pipelineHealth": "OK", "signalStaleRatio": 0.0, "decisionDistribution": {"ALLOW": 1, "WARN": 0, "HOLD": 0, "BLOCK": 0}, "failedJobCount": 0, "dlqEventCount": 0, "components": {key: copy.deepcopy(component_ok) for key in ("decisionDistribution", "signalFreshness", "failedJobs", "dlqEvents")}}},
        "artifact-ingest-status.v1": {"success": True, "data": {"items": [{"artifactId": "artifact_fixture_00000001", "fileName": "backtest_result.json", "producer": "decision-platform", "runId": "demo_fixture_00000001", "fileHash": "sha256:" + "4" * 64, "schemaVersion": "1.0.0", "status": "INGESTED", "lastIngestedAt": ts, "duplicate": False}]}},
        "dashboard-model-evaluation.v1": {"success": True, "data": copy.deepcopy(empty_view)},
        "dashboard-backtest.v1": {"success": True, "data": {"viewState": "READY", "asOf": ts, "freshUntil": later, "evidenceMode": "SYNTHETIC_DEMO", "performanceClaimAllowed": False, "view": {"runId": "demo_fixture_00000001", "fixtureClass": "SYNTHETIC_FAKE_E2E", "strategies": [{"strategy": name, "metrics": copy.deepcopy(metric_values), "curve": []} for name in ("Baseline", "Guide", "Strict")], "heatmap": [], "metricCards": [], "projectionHash": "sha256:" + "5" * 64}}},
        "dashboard-risk-result.v1": {"success": True, "data": copy.deepcopy(empty_view)},
        "dashboard-rag-sources.v1": {"success": True, "data": copy.deepcopy(empty_view)},
    }
    for schema_id, fixture in fixtures.items():
        if schema_id not in {"async-event-envelope.v1", "async-dlq-envelope.v1"}:
            fixture.update({"requestId": "req_s7s8_fixture_0001", "warnings": [], "error": None})
    return fixtures


def _negative_fixtures(fixtures: Mapping[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    negatives: dict[str, dict[str, Any]] = {}
    for schema_id, fixture in fixtures.items():
        invalid = copy.deepcopy(fixture)
        invalid["unexpected"] = True
        negatives[schema_id] = invalid
    return negatives


def validate_semantics(schema_id: str, value: Mapping[str, Any]) -> None:
    if schema_id in {"async-event-envelope.v1", "async-dlq-envelope.v1"}:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > 65_536:
            raise ContractValidationError("Kafka envelope exceeds 64 KiB.")
        references = value.get("references")
        if not isinstance(references, dict) or not references:
            raise ContractValidationError("Async event must contain reference-only identity.")
        return
    data = value.get("data")
    if not isinstance(data, dict):
        raise ContractValidationError("Public S7/S8 response data must be an object.")
    if schema_id.startswith("dashboard-"):
        state = data.get("viewState")
        view = data.get("view")
        if state == "EMPTY" and view is not None:
            raise ContractValidationError("EMPTY dashboard state must have null view.")
        if state in {"READY", "STALE"} and view is None:
            raise ContractValidationError("READY/STALE dashboard state requires a view.")
        if data.get("evidenceMode") == "SYNTHETIC_DEMO" and isinstance(view, dict):
            run_id = view.get("runId")
            if run_id is not None and not str(run_id).startswith("demo_"):
                raise ContractValidationError("Synthetic dashboard run must use demo_ namespace.")


def _openapi(schemas: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    component_names = {schema_id: "".join(part.title() for part in schema_id.replace(".", "-").split("-")) for schema_id in SCHEMA_IDS[1:]}

    def operation(
        schema_id: str,
        parameter: tuple[str, str] | None = None,
        *,
        admin_only: bool = False,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "security": [{"bearerAuth": []}],
            "responses": {
                "200": {"description": "Bounded ADMIN operational response" if admin_only else "Bounded owner-scoped response", "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{component_names[schema_id]}"}}}},
                "401": {"description": "Authentication required"},
                "403": {"description": "Current ADMIN role required"} if admin_only else {"description": "Access denied"},
                "404": {"description": "Resource not found"},
            },
        }
        if parameter is not None:
            name, pattern = parameter
            result["parameters"] = [{"name": name, "in": "path", "required": True, "schema": {"type": "string", "pattern": pattern}}]
        return result

    paths = {
        "/api/v1/async-jobs/{jobId}": {"get": operation("async-job-status.v1", ("jobId", "^job_[A-Za-z0-9_-]{8,96}$"), admin_only=True)},
        "/api/v1/async-jobs": {"get": operation("async-job-list.v1", admin_only=True)},
        "/api/v1/stream-metrics": {"get": operation("stream-metric-status.v1", admin_only=True)},
        "/api/v1/artifacts/ingest-status": {"get": operation("artifact-ingest-status.v1", admin_only=True)},
        "/api/v1/dashboard/model-evaluations/{runId}": {"get": operation("dashboard-model-evaluation.v1", ("runId", "^(run|demo)_[A-Za-z0-9_-]{8,96}$"))},
        "/api/v1/dashboard/backtests/{runId}": {"get": operation("dashboard-backtest.v1", ("runId", "^(run|demo)_[A-Za-z0-9_-]{8,96}$"))},
        "/api/v1/dashboard/risk-results/{decisionId}": {"get": operation("dashboard-risk-result.v1", ("decisionId", "^dec_[A-Za-z0-9_-]{8,96}$"))},
        "/api/v1/dashboard/rag-sources/{answerId}": {"get": operation("dashboard-rag-sources.v1", ("answerId", "^rag_[A-Za-z0-9_-]{12,96}$"))},
    }
    paths["/api/v1/async-jobs"]["get"]["parameters"] = [
        {"name": "status", "in": "query", "required": False, "schema": {"type": "string", "enum": ["REQUESTED", "RUNNING", "COMPLETED", "FAILED", "NEEDS_REVIEW"]}},
        {"name": "type", "in": "query", "required": False, "schema": {"type": "string", "enum": ["RAG_INDEX", "ARTIFACT_INGEST", "MODEL_EVAL"]}},
        {"name": "cursor", "in": "query", "required": False, "schema": {"type": "string", "pattern": "^[A-Za-z0-9_-]{16,256}$"}},
        {"name": "size", "in": "query", "required": False, "schema": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50}},
    ]
    return {
        "openapi": "3.1.1",
        "jsonSchemaDialect": "https://spec.openapis.org/oas/3.1/dialect/base",
        "info": {"title": "S7/S8 Async and Dashboard Contract", "version": "1.0.0"},
        "paths": paths,
        "components": {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}},
            "schemas": {component_names[schema_id]: copy.deepcopy(schemas[schema_id]) for schema_id in SCHEMA_IDS[1:]},
        },
    }


def _catalog() -> dict[str, Any]:
    exact_topics = [topic for base in BASE_TOPICS for topic in (base, base.replace(".v1", ".retry.v1"), base.replace(".v1", ".dlq.v1"))]
    return {
        "contractId": "s7-s8-contract-lock.v1",
        "asyncAdapter": {"default": "db", "allowed": ["db", "kafka"], "silentFallback": False, "dualActive": False},
        "limits": {"dbPayloadBytes": 32_768, "kafkaEnvelopeBytes": 65_536, "jsonDepth": 8, "jsonKeys": 64, "arrayItems": 32, "stringBytes": 2_048, "errorBytes": 128, "claimPageSize": 100, "attempts": 3},
        "topics": exact_topics,
        "publicPaths": list(PUBLIC_PATHS),
        "crossMarketRuntimePaths": [],
        "performanceClaimAllowed": False,
    }


def _bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def build_outputs() -> dict[Path, bytes]:
    schemas = build_schemas()
    fixtures = _fixtures()
    negatives = _negative_fixtures(fixtures)
    outputs: dict[Path, bytes] = {}
    for schema_id, schema in schemas.items():
        outputs[ROOT / SCHEMA_PATHS[schema_id]] = _bytes(schema)
        outputs[ROOT / "contracts" / "examples" / f"{schema_id}.valid.json"] = _bytes(fixtures[schema_id])
        outputs[ROOT / "contracts" / "examples" / "invalid" / f"{schema_id}.unknown-field.invalid.json"] = _bytes(negatives[schema_id])
    outputs[ROOT / "contracts" / "catalogs" / "s7-s8-contract-lock.v1.json"] = _bytes(_catalog())
    outputs[ROOT / "contracts" / "openapi" / "s7-s8-async-dashboard.v1.openapi.json"] = _bytes(_openapi(schemas))
    return outputs


def validate_generated() -> None:
    schemas = build_schemas()
    fixtures = _fixtures()
    negatives = _negative_fixtures(fixtures)
    for schema_id, schema in schemas.items():
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        errors = list(validator.iter_errors(fixtures[schema_id]))
        if errors:
            raise ContractValidationError(f"{schema_id} positive fixture failed: {errors[0].message}")
        validate_semantics(schema_id, fixtures[schema_id])
        if not list(validator.iter_errors(negatives[schema_id])):
            raise ContractValidationError(f"{schema_id} negative fixture passed.")


def generate(*, check: bool) -> None:
    validate_generated()
    for path, payload in build_outputs().items():
        if check:
            if not path.is_file() or path.read_bytes() != payload:
                raise ContractValidationError(
                    f"generated artifact drift: {path.relative_to(ROOT).as_posix()}"
                )
            continue
        write_generated_path(ROOT, path, payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        generate(check=args.check)
    except (ContractValidationError, OSError) as error:
        print(f"S7_S8_CONTRACT_LOCK_FAILED: {error}", file=sys.stderr)
        return 1
    print("S7_S8_CONTRACT_LOCK_VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
