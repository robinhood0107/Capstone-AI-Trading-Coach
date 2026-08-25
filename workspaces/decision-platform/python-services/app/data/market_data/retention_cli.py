"""Least-privilege ECOS retention; dry-run is the default and performs no provider I/O."""

from __future__ import annotations

import argparse
import json
import os
from datetime import date

import psycopg

from app.data.market_data.repository import MarketDataRepositoryError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="market-data ECOS retention")
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    database_dsn = os.environ.get("MARKET_DATA_RETENTION_DSN")
    if not database_dsn:
        parser.error("MARKET_DATA_RETENTION_DSN is required")
    with psycopg.connect(database_dsn, autocommit=False, connect_timeout=2) as connection:
        authority = connection.execute(
            "select pg_has_role(session_user, 'decision_market_retention_admin', 'MEMBER')"
        ).fetchone()
        if authority != (True,):
            raise MarketDataRepositoryError("market-data retention role membership is required")
        result = connection.execute(
            "select candidate_rows, deleted_rows from prune_market_data_macro(%s, %s)",
            (args.as_of, args.apply),
        ).fetchone()
        connection.commit()
    if result is None:
        raise MarketDataRepositoryError("market-data retention returned no result")
    print(
        json.dumps(
            {
                "candidateRows": result[0],
                "deletedRows": result[1],
                "mode": "APPLY" if args.apply else "DRY_RUN",
                "providerCalls": 0,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0
