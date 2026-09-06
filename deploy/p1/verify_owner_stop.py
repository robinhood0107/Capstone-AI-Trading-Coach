#!/usr/bin/env python3
"""격리 DB에서 owner/admin capability 경계를 검증한다. 실행 DB에는 호출할 수 없다."""

import argparse
import hashlib
import json
import subprocess
import uuid

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--database", required=True)
args = parser.parse_args()
if not args.database.startswith("capstone_repair_"):
    raise SystemExit("DISPOSABLE_DATABASE_REQUIRED")
cmd = [
    "/usr/bin/docker",
    "exec",
    "-i",
    "capstone-p1-postgres-1",
    "psql",
    "-U",
    "postgres",
    "-d",
    args.database,
    "-X",
    "-At",
    "-v",
    "ON_ERROR_STOP=1",
]


def sql(text, success=True):
    r = subprocess.run(cmd, input=text, text=True, capture_output=True)
    if (r.returncode == 0) != success:
        raise AssertionError("SQL outcome mismatch: " + r.stderr[:350])
    return r.stdout.strip()


suffix = uuid.uuid4().hex[:10]
a, b, admin = ["usr_stop_" + name + suffix for name in ["a", "b", "admin"]]
for user, role in [(a, "USER"), (b, "USER"), (admin, "ADMIN")]:
    sql(
        f"INSERT INTO users(user_id,username,password_hash,role,status,security_version) VALUES('{user}','{user}','test-only-no-login','{role}','ACTIVE',1);"
    )
sql(
    "INSERT INTO risk_kill_switch(kill_switch_id,active,reason_class,generation,changed_by_role,changed_at) VALUES('GLOBAL',false,'INITIAL_STATE',1,'SYSTEM',now()) ON CONFLICT DO NOTHING;"
)


def capability(user, role, active, scope, request, global_scope=False, version=1):
    token = "cap2_" + uuid.uuid4().hex * 2 + "." + "a" * 86
    operation = "TRANSITION_KILL_SWITCH" if global_scope else "CHANGE_OWNER_KILL_SWITCH"
    kind = "KILL_SWITCH" if global_scope else "OWNER_KILL_SWITCH"
    target = "GLOBAL" if global_scope else user
    nonce = uuid.uuid4().hex
    payload = (
        f"public.actor_capability_payload_hash('{user}','{version}','{str(active).lower()}',"
        + ("'1'" if global_scope else "'" + scope + "'")
        + f",'{request}')"
    )
    sql(f"""INSERT INTO actor_request_capability(token_hash,actor_user_id,actor_role,actor_security_version,issued_at,expires_at,operation,target_kind,target_id,payload_hash,request_id,transaction_id,nonce,signature)
    VALUES('sha256:{hashlib.sha256(token.encode()).hexdigest()}','{user}','{role}',{version},now(),now()+interval '25 seconds','{operation}','{kind}','{target}',{payload},'req_{nonce}','txn_{nonce}','{nonce}','ed25519:{"a" * 86}');""")
    return token


def change(
    user, active, role="USER", key=None, acting_as=None, version=1, expected=True
):
    request = "req_" + uuid.uuid4().hex
    scope = "sha256:" + hashlib.sha256((key or uuid.uuid4().hex).encode()).hexdigest()
    token = capability(user, role, active, scope, request, version=version)
    result = sql(
        f"SET SESSION AUTHORIZATION decision_app; SELECT owner_kill_switch_authorized('{token}','{acting_as or user}',{version},{str(active).lower()},'{scope}','{request}')::text;",
        expected,
    )
    return json.loads(result.splitlines()[-1]) if expected else None


assert change(a, True)["active"]
assert sql(f"SELECT active FROM owner_kill_switch WHERE user_id='{b}';") == "f"
assert not change(a, False)["active"]
change(a, True, acting_as=b, expected=False)
for active in [True, False]:
    request = "req_" + uuid.uuid4().hex
    token = capability(a, "USER", active, "", request, global_scope=True)
    sql(
        f"SET SESSION AUTHORIZATION decision_app; SELECT * FROM transition_kill_switch_authorized('{token}','{a}',1,{str(active).lower()},1,'{request}');",
        False,
    )
first = change(a, True, key="replay")
assert change(a, True, key="replay") == first
change(a, False, key="replay", expected=False)
generation = sql(f"SELECT generation FROM owner_kill_switch WHERE user_id='{a}';")
change(a, True)
assert (
    sql(f"SELECT generation FROM owner_kill_switch WHERE user_id='{a}';") == generation
)
sql(f"UPDATE users SET security_version=2 WHERE user_id='{a}';")
change(a, False, expected=False)
sql(
    f"SET SESSION AUTHORIZATION decision_app; UPDATE owner_kill_switch SET active=false WHERE user_id='{b}';",
    False,
)
sql(
    "SET SESSION AUTHORIZATION decision_app; UPDATE risk_kill_switch SET active=false;",
    False,
)
print(
    "OWNER_STOP_DB=PASS owner_isolation symmetry replay conflict generation stale_actor global_denial direct_dml_denial"
)
