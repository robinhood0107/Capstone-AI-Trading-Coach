"""Validated certification/source/Team B hashes를 V90 activation gate에 결속한다."""

from __future__ import annotations

import os
import re
from datetime import date

import psycopg
from psycopg.conninfo import conninfo_to_dict

from app.p1_owner.automation_runtime import AutomationRuntimeError, XkrxBoundaryPlanner

_SHA = re.compile(r"^[0-9a-f]{64}$")
_USER = re.compile(r"^usr_[A-Za-z0-9_-]{8,96}$")


def main() -> int:
    dsn = os.environ.get("P1_AUTOMATION_GATE_AUTHOR_DSN", "").strip()
    try:
        parsed = conninfo_to_dict(dsn)
    except psycopg.Error:
        print("AUTOMATION_ACTIVATION_GATE=FAIL:DSN_INVALID")
        print("PROVIDER_CALLS=0")
        return 1
    if parsed.get("user") != "decision_replay_authorizer" or parsed.get("host") not in {
        "postgres",
        "127.0.0.1",
        "localhost",
    }:
        print("AUTOMATION_ACTIVATION_GATE=FAIL:ROLE_INVALID")
        print("PROVIDER_CALLS=0")
        return 1
    owner = os.environ.get("P1_AUTOMATION_OWNER_USER_ID", "").strip()
    certification_sha = os.environ.get("P1_CERTIFICATION_RECEIPT_SHA256", "").strip()
    release_sha = os.environ.get("P1_RELEASE_BINDING_SHA256", "").strip()
    source_sha = os.environ.get("P1_SOURCE_BINDING_SHA256", "").strip()
    if _USER.fullmatch(owner) is None or any(
        _SHA.fullmatch(value) is None for value in (certification_sha, release_sha, source_sha)
    ):
        print("AUTOMATION_ACTIVATION_GATE=FAIL:BINDING_INVALID")
        print("PROVIDER_CALLS=0")
        return 1
    try:
        certification_session = date.fromisoformat(
            os.environ.get("P1_CERTIFICATION_SESSION_DATE", "")
        )
        eligible = XkrxBoundaryPlanner().next_session(certification_session)
        with psycopg.connect(dsn, autocommit=False, connect_timeout=2) as connection:
            with connection.cursor() as cursor:
                cursor.execute("select current_user,session_user")
                if cursor.fetchone() != (
                    "decision_replay_authorizer",
                    "decision_replay_authorizer",
                ):
                    raise AutomationRuntimeError("AUTOMATION_GATE_AUTHOR_ROLE_MISMATCH")
                cursor.execute("select p1_current_team_b_integrity_receipt_v1()")
                row = cursor.fetchone()
                if row is None or not isinstance(row[0], str) or _SHA.fullmatch(row[0]) is None:
                    print("AUTOMATION_ACTIVATION_GATE=NOT_AUTHORED_TEAM_B_MISSING")
                    print("PROVIDER_CALLS=0")
                    return 1
                cursor.execute(
                    "select p1_author_automation_activation_gate_v2(%s,%s,%s,%s,%s,%s,%s)",
                    (
                        owner,
                        certification_sha,
                        certification_session,
                        eligible,
                        release_sha,
                        source_sha,
                        row[0],
                    ),
                )
                authored = cursor.fetchone()
                if authored is None:
                    raise AutomationRuntimeError("AUTOMATION_GATE_AUTHOR_FAILED")
                version = int(authored[0])
    except (AutomationRuntimeError, ValueError, psycopg.Error):
        print("AUTOMATION_ACTIVATION_GATE=FAIL:CLOSED")
        print("PROVIDER_CALLS=0")
        return 1
    print("AUTOMATION_ACTIVATION_GATE=PASS")
    print(f"AUTOMATION_ACTIVATION_GATE_VERSION={version}")
    print(f"STRATEGY_ELIGIBLE_FROM_SESSION={eligible.isoformat()}")
    print("PROVIDER_CALLS=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
