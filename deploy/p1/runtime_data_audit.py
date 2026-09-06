#!/usr/bin/env python3
"""로컬 DB의 실제 외래키를 검사한다. 교정은 고아 행을 비공개 원형 archive로 이관한다."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def run_sql(database: str, sql: str) -> str:
    result = subprocess.run(
        [
            "/usr/bin/docker",
            "exec",
            "-i",
            "capstone-p1-postgres-1",
            "psql",
            "-U",
            "postgres",
            "-d",
            database,
            "-X",
            "-At",
            "-v",
            "ON_ERROR_STOP=1",
        ],
        input=sql,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("DB audit SQL failed; inspect the private operator report")
    return result.stdout


def identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def audit(database: str, apply: bool) -> dict[str, object]:
    metadata = json.loads(
        run_sql(
            database,
            """
SELECT json_agg(json_build_object('name',c.conname,'table',c.conrelid::regclass::text,
 'parent',c.confrelid::regclass::text,
 'keys',(SELECT json_agg(a.attname ORDER BY k.i) FROM unnest(c.conkey) WITH ORDINALITY k(n,i)
   JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.n),
 'refs',(SELECT json_agg(a.attname ORDER BY k.i) FROM unnest(c.confkey) WITH ORDINALITY k(n,i)
   JOIN pg_attribute a ON a.attrelid=c.confrelid AND a.attnum=k.n)))
FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace WHERE c.contype='f' AND n.nspname='public';
""",
        )
    )
    broken: list[dict[str, object]] = []
    repairs: list[str] = []
    allowed = {
        "order_events",
        "automation_ai_judgements",
        "decision_artifacts",
        "decision_traces",
    }
    for row in metadata:
        predicate = " AND ".join(
            "c." + identifier(k) + " IS NOT NULL" for k in row["keys"]
        )
        join = " AND ".join(
            "c." + identifier(k) + "=p." + identifier(v)
            for k, v in zip(row["keys"], row["refs"], strict=True)
        )
        condition = (
            predicate
            + " AND NOT EXISTS(SELECT 1 FROM "
            + row["parent"]
            + " p WHERE "
            + join
            + ")"
        )
        count = int(
            run_sql(
                database,
                "SELECT count(*) FROM " + row["table"] + " c WHERE " + condition + ";",
            )
        )
        if count == 0:
            continue
        broken.append({"constraint": row["name"], "rows": count})
        if apply:
            table = row["table"]
            if table not in allowed:
                raise RuntimeError("Unclassified orphan table; no repair was applied")
            # 행을 먼저 보존한 뒤 이관한다. FK trigger는 끄지 않으며 같은 transaction에서 재검사한다.
            repairs.append(f"""
LOCK TABLE public.{table} IN ACCESS EXCLUSIVE MODE;
INSERT INTO repair_archive.orphan_rows(source_table,reason,payload)
 SELECT '{table}','{row["name"]}',to_jsonb(c) FROM public.{table} c WHERE {condition};
ALTER TABLE public.{table} DISABLE TRIGGER USER;
DELETE FROM public.{table} c WHERE {condition};
ALTER TABLE public.{table} ENABLE TRIGGER USER;
""")
    if repairs:
        run_sql(
            database,
            """BEGIN;
SELECT pg_advisory_xact_lock(73490907);
CREATE SCHEMA IF NOT EXISTS repair_archive;
REVOKE ALL ON SCHEMA repair_archive FROM PUBLIC;
CREATE TABLE IF NOT EXISTS repair_archive.orphan_rows(
 source_table text NOT NULL,reason text NOT NULL,payload jsonb NOT NULL,archived_at timestamptz NOT NULL DEFAULT now());
REVOKE ALL ON repair_archive.orphan_rows FROM PUBLIC;
"""
            + "\n".join(repairs)
            + "\nCOMMIT;",
        )
    return {
        "foreignKeysChecked": len(metadata),
        "orphanReferences": broken,
        "archived": bool(repairs),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default="capstone_p1",
        choices=["capstone_p1", "capstone_repair_1719"],
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.database, args.apply)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    args.report.chmod(0o600)
    print(json.dumps(result, ensure_ascii=False))
