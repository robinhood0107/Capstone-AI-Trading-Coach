from __future__ import annotations

import json
from dataclasses import dataclass

import psycopg

from app.cross_market.s67_materializer import CrossMarketRiskSnapshotV2


@dataclass(frozen=True, slots=True)
class PostgresCrossMarketRiskPublisher:
    """decision_market_writer의 V78 function만 호출하며 provider client를 소유하지 않는다."""

    database_dsn: str

    def publish(self, owner_user_id: str, owner_scope_hash: str, snapshot: CrossMarketRiskSnapshotV2) -> str:
        payload = snapshot.payload
        payload_text = _canonical(payload)
        explanation_text = _canonical(snapshot.explanation)
        with psycopg.connect(self.database_dsn) as connection:
            role = connection.execute("select current_user").fetchone()
            if role != ("decision_market_writer",):
                raise ValueError("cross-market publisher requires writer role")
            row = connection.execute(
                """
                select append_cross_market_risk_snapshot_v2(
                  %s::uuid, %s, %s, %s, %s::timestamptz, %s::timestamptz,
                  %s, %s, %s, %s, %s, %s::numeric, %s::numeric, %s, %s, %s,
                  %s::timestamptz, %s, %s, %s, %s, %s
                )
                """,
                (
                    payload["snapshotId"],
                    owner_user_id,
                    owner_scope_hash,
                    payload["symbol"],
                    payload["availableAt"],
                    payload["staleAt"],
                    payload["evidenceMode"],
                    payload["storageMode"],
                    payload["runtimeMode"],
                    payload["availability"],
                    payload["quality"],
                    payload["score"],
                    payload["thresholdPercentile"],
                    payload["thresholdArtifactHash"],
                    payload["configHash"],
                    payload["exposure"],
                    payload["exposureAvailableAt"],
                    payload["exposureCatalogHash"],
                    payload["semanticInputHash"],
                    payload["artifactHash"],
                    payload_text,
                    explanation_text,
                ),
            ).fetchone()
        if row is None or row[0] not in {"INSERTED", "NO_OP"}:
            raise RuntimeError("cross-market v2 publication failed")
        return str(row[0])


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
