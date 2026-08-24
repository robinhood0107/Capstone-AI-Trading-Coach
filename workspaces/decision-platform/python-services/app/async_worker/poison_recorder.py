from __future__ import annotations

from dataclasses import asdict, dataclass
from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
from typing import Protocol
import urllib.error
import urllib.request

import psycopg
from psycopg.conninfo import conninfo_to_dict


_PATH = "/internal/kafka/poison-receipts"
_LOOPBACK_URL = re.compile(r"^http://127\.0\.0\.1:[1-9][0-9]{0,4}/internal/kafka/poison-receipts$")
_SECRET = re.compile(r"^[A-Za-z0-9._~:-]{32,128}$")
_EVENT_ID = re.compile(r"^evt_[A-Za-z0-9_-]{8,96}$")
_EVENT_TYPE = re.compile(r"^[a-z][a-z0-9.-]{2,127}$")
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_FAILURE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_JOB_ID = re.compile(r"^job_[A-Za-z0-9_-]{8,96}$")
_CLAIM_TOKEN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_PARTITION_KEY = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")
_MAX_BODY_BYTES = 4_096


class PoisonReceiptError(RuntimeError):
    """The durable poison receipt was not recorded."""


@dataclass(frozen=True, slots=True)
class PoisonReceipt:
    event_id: str
    event_type: str
    payload_hash: str
    source_topic: str
    source_partition: int
    source_offset: int
    attempt: int
    failure_code: str
    partition_key: str
    job_id: str | None = None
    claim_token: str | None = None

    def validate(self) -> None:
        if (
            _EVENT_ID.fullmatch(self.event_id) is None
            or _EVENT_TYPE.fullmatch(self.event_type) is None
            or self.source_topic != self.event_type
            or _HASH.fullmatch(self.payload_hash) is None
            or self.source_partition not in range(3)
            or self.source_offset < 0
            or self.attempt not in {1, 2, 3}
            or _FAILURE.fullmatch(self.failure_code) is None
            or _PARTITION_KEY.fullmatch(self.partition_key) is None
            or ((self.job_id is None) != (self.claim_token is None))
            or (self.job_id is not None and _JOB_ID.fullmatch(self.job_id) is None)
            or (self.claim_token is not None and _CLAIM_TOKEN.fullmatch(self.claim_token) is None)
        ):
            raise PoisonReceiptError


class PoisonRecorderPort(Protocol):
    def record(self, receipt: PoisonReceipt) -> bool: ...


class PostgresPoisonRecorder:
    def __init__(self, database_dsn: str) -> None:
        if conninfo_to_dict(database_dsn).get("user") != "decision_poison_recorder":
            raise ValueError("poison recorder requires the decision_poison_recorder DSN")
        self._database_dsn = database_dsn

    def record(self, receipt: PoisonReceipt) -> bool:
        receipt.validate()
        with psycopg.connect(
            self._database_dsn,
            autocommit=True,
            connect_timeout=2,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_user,session_user")
                if cursor.fetchone() != ("decision_poison_recorder", "decision_poison_recorder"):
                    raise PoisonReceiptError
                cursor.execute(
                    """
                    SELECT p1_record_kafka_poison_receipt(
                      %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                    )
                    """,
                    (
                        receipt.event_id,
                        receipt.event_type,
                        receipt.payload_hash,
                        receipt.source_topic,
                        receipt.source_partition,
                        receipt.source_offset,
                        receipt.attempt,
                        receipt.failure_code,
                        receipt.partition_key,
                        receipt.job_id,
                        receipt.claim_token,
                    ),
                )
                row = cursor.fetchone()
                return row is not None and row[0] is True


class HttpPoisonRecorderClient:
    def __init__(self, url: str, shared_secret: str, *, timeout: float = 2.0) -> None:
        if (
            _LOOPBACK_URL.fullmatch(url) is None
            or _SECRET.fullmatch(shared_secret) is None
            or not 0.1 <= timeout <= 5.0
        ):
            raise ValueError("poison recorder client configuration is invalid")
        self._url = url
        self._shared_secret = shared_secret
        self._timeout = timeout

    def record(self, receipt: PoisonReceipt) -> bool:
        receipt.validate()
        body = json.dumps(asdict(receipt), separators=(",", ":")).encode()
        if len(body) > _MAX_BODY_BYTES:
            raise PoisonReceiptError
        request = urllib.request.Request(
            self._url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._shared_secret}",
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                response_body = response.read(64)
                return bool(response.status == 200 and response_body == b'{"recorded":true}')
        except (OSError, urllib.error.URLError, TimeoutError) as error:
            raise PoisonReceiptError from error


class PoisonRecorderHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self, bind: tuple[str, int], recorder: PoisonRecorderPort, shared_secret: str
    ) -> None:
        if (
            bind[0] != "127.0.0.1"
            or not 0 <= bind[1] <= 65_535
            or _SECRET.fullmatch(shared_secret) is None
        ):
            raise ValueError("poison recorder server configuration is invalid")
        self.recorder = recorder
        self.shared_secret = shared_secret
        super().__init__(bind, _PoisonRecorderHandler)


class _PoisonRecorderHandler(BaseHTTPRequestHandler):
    server: PoisonRecorderHttpServer

    def do_POST(self) -> None:
        try:
            if self.path != _PATH:
                self._respond(404, b'{"recorded":false}')
                return
            authorization = self.headers.get_all("Authorization") or []
            content_lengths = self.headers.get_all("Content-Length") or []
            if (
                len(authorization) != 1
                or len(content_lengths) != 1
                or not compare_digest(authorization[0], f"Bearer {self.server.shared_secret}")
                or not content_lengths[0].isdigit()
            ):
                self._respond(401, b'{"recorded":false}')
                return
            length = int(content_lengths[0])
            if not 1 <= length <= _MAX_BODY_BYTES:
                self._respond(413, b'{"recorded":false}')
                return
            raw = self.rfile.read(length)
            if len(raw) != length:
                self._respond(400, b'{"recorded":false}')
                return
            values = json.loads(raw)
            if not isinstance(values, dict) or set(values) != {
                "event_id",
                "event_type",
                "payload_hash",
                "source_topic",
                "source_partition",
                "source_offset",
                "attempt",
                "failure_code",
                "partition_key",
                "job_id",
                "claim_token",
            }:
                raise PoisonReceiptError
            receipt = PoisonReceipt(**values)
            if not self.server.recorder.record(receipt):
                raise PoisonReceiptError
            self._respond(200, b'{"recorded":true}')
        except (json.JSONDecodeError, PoisonReceiptError, TypeError, ValueError, psycopg.Error):
            self._respond(503, b'{"recorded":false}')

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)


def _bind_from_env() -> tuple[str, int]:
    raw = os.environ.get("POISON_RECORDER_BIND_ADDRESS", "127.0.0.1:18082")
    host, separator, port = raw.rpartition(":")
    if separator != ":" or host != "127.0.0.1" or not port.isdigit():
        raise ValueError("poison recorder must bind to numeric loopback")
    if not 1 <= int(port) <= 65_535:
        raise ValueError("poison recorder port is invalid")
    return host, int(port)


def serve() -> None:
    recorder = PostgresPoisonRecorder(os.environ.get("POISON_RECORDER_DATABASE_DSN", "").strip())
    secret = os.environ.get("POISON_RECORDER_SHARED_SECRET", "").strip()
    server = PoisonRecorderHttpServer(_bind_from_env(), recorder, secret)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()


if __name__ == "__main__":
    serve()
