from __future__ import annotations

import json
from dataclasses import dataclass

import psycopg

from app.data._shared.canonical_json import canonical_json_bytes
from app.financial_engineering.manual_batch import BatchPublication


@dataclass(frozen=True)
class PostgresFinancialEngineeringPublisher:
    database_dsn: str

    def publish_all(self, publications: tuple[BatchPublication, ...]) -> tuple[str, ...]:
        outcomes: list[str] = []
        with psycopg.connect(self.database_dsn) as connection:
            role = connection.execute("select current_user").fetchone()
            if role != ("decision_market_writer",):
                raise ValueError("financial engineering publisher requires writer role")
            for publication in publications:
                outcomes.append(self._publish(connection, publication))
        return tuple(outcomes)

    def _publish(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        publication: BatchPublication,
    ) -> str:
        snapshot = publication.snapshot
        manifest = publication.manifest
        numeric_text = canonical_json_bytes(snapshot["numericPayload"]).decode()
        explanatory_text = canonical_json_bytes(
            {
                "authority": "EXPLANATION_ONLY",
                "reportIncluded": True,
            }
        ).decode()
        steps_text = canonical_json_bytes(manifest["steps"]).decode()
        row = connection.execute(
            """
            select outcome, snapshot_id
            from append_financial_engineering_result(
              %s::uuid, %s, %s, %s::date, %s::timestamptz, %s::timestamptz,
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                manifest["runId"],
                snapshot["schemaVersion"],
                snapshot["symbol"],
                snapshot["sessionDate"],
                snapshot["asOf"],
                snapshot["availableAt"],
                snapshot["sourceManifestHash"],
                snapshot["configHash"],
                snapshot["numericPayloadHash"],
                snapshot["artifactHash"],
                snapshot["availability"],
                snapshot["quality"],
                snapshot["staleness"],
                numeric_text,
                explanatory_text,
                manifest["reportArtifactHash"],
                manifest["reportBytes"],
                steps_text,
            ),
        ).fetchone()
        if row is None or row[0] not in {"INSERTED", "NO_OP"}:
            raise RuntimeError("financial engineering publication failed")
        return str(row[0])


def parse_publication_json(snapshot_json: str, manifest_json: str, report: str) -> BatchPublication:
    snapshot = json.loads(snapshot_json)
    manifest = json.loads(manifest_json)
    if not isinstance(snapshot, dict) or not isinstance(manifest, dict):
        raise ValueError("financial engineering publication JSON is invalid")
    return BatchPublication(snapshot, manifest, report)
