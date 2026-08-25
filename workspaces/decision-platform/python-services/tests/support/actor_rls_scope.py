from __future__ import annotations

import hashlib
import uuid
from typing import Any

import psycopg


def open_actor_rls_scope(
    *,
    identity_dsn: str,
    connection: psycopg.Connection[Any],
    actor_user_id: str,
    actor_role: str,
    operation: str,
    target_kind: str,
    target_id: str,
) -> None:
    """Register and consume one exact test capability on the application transaction."""
    nonce = uuid.uuid4().hex
    signature = (uuid.uuid4().hex * 3)[:86]
    token = f"cap2_{uuid.uuid4().hex}{uuid.uuid4().hex}.{signature}"
    payload_hash = "sha256:" + hashlib.sha256(target_id.encode()).hexdigest()
    with psycopg.connect(identity_dsn, autocommit=True) as identity:
        registered = identity.execute(
            """
            SELECT register_actor_request_capability_v2(
              %s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,
              statement_timestamp(),statement_timestamp() + interval '15 seconds',%s
            )
            """,
            (
                token,
                actor_user_id,
                actor_role,
                operation,
                target_kind,
                target_id,
                payload_hash,
                "req_" + uuid.uuid4().hex,
                "txn_" + uuid.uuid4().hex,
                nonce,
                "ed25519:" + signature,
            ),
        ).fetchone()
    assert registered == (True,)
    opened = connection.execute(
        "SELECT open_actor_rls_scope_v1(%s,%s,%s,%s,%s,%s)",
        (token, actor_user_id, operation, target_kind, target_id, payload_hash),
    ).fetchone()
    assert opened == (True,)
