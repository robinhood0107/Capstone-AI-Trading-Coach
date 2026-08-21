from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.data.market_data.reader import ParquetMarketDataOperationalReader
from app.financial_engineering.manual_batch import ManualFinancialEngineeringBatch, write_publications
from app.financial_engineering.repository import PostgresFinancialEngineeringPublisher


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the provider-free S6.5 manual sequential batch.")
    parser.add_argument("--archive-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()
    database_dsn = os.environ.get("FINANCIAL_ENGINEERING_DATABASE_DSN", "").strip()
    if not database_dsn:
        print(json.dumps({"status": "NOT_AVAILABLE", "errorCode": "DATABASE_DSN_MISSING", "providerCalls": 0}))
        return 1
    result = ManualFinancialEngineeringBatch(
        ParquetMarketDataOperationalReader(args.archive_root),
        PostgresFinancialEngineeringPublisher(database_dsn),
    ).run()
    if result.status == "COMPLETE":
        write_publications(args.output_root, result.publications)
    print(
        json.dumps(
            {
                "status": result.status,
                "publicationCount": len(result.publications),
                "errorCode": result.error_code,
                "providerCalls": result.provider_calls,
            },
            sort_keys=True,
        )
    )
    return 0 if result.status == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
