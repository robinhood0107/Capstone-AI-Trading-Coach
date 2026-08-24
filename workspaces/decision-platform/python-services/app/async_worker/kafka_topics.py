from __future__ import annotations

import os
import re
import time
from typing import Protocol, cast

from confluent_kafka import KafkaException
from confluent_kafka.admin import (  # type: ignore[attr-defined]
    AclBinding,
    AclBindingFilter,
    AclOperation,
    AclPermissionType,
    AdminClient,
    ConfigResource,
    ResourcePatternType,
    ResourceType,
)
from confluent_kafka.cimpl import KafkaError, NewTopic

BASE_TOPICS = (
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


class _AdminFuture(Protocol):
    def result(self) -> object: ...


def exact_topic_configs() -> dict[str, dict[str, str]]:
    topics: dict[str, dict[str, str]] = {}
    for base_topic in BASE_TOPICS:
        stem = base_topic.removesuffix(".v1")
        topics[base_topic] = {"retention.ms": "604800000"}
        topics[f"{stem}.retry.v1"] = {"retention.ms": "604800000"}
        topics[f"{stem}.dlq.v1"] = {"retention.ms": "2592000000"}
    return topics


def exact_acl_bindings() -> tuple[AclBinding, ...]:
    allow = AclPermissionType.ALLOW
    literal = ResourcePatternType.LITERAL
    bindings: list[AclBinding] = []
    publisher = "User:p1_outbox_publisher"
    consumer = "User:p1_async_worker"
    for topic in sorted(BASE_TOPICS):
        for operation in (AclOperation.DESCRIBE, AclOperation.WRITE):
            bindings.append(
                AclBinding(ResourceType.TOPIC, topic, literal, publisher, "*", operation, allow)
            )
        dlq_topic = topic.removesuffix(".v1") + ".dlq.v1"
        for operation in (AclOperation.DESCRIBE, AclOperation.WRITE):
            bindings.append(
                AclBinding(ResourceType.TOPIC, dlq_topic, literal, publisher, "*", operation, allow)
            )
    bindings.append(
        AclBinding(
            ResourceType.BROKER,
            "kafka-cluster",
            literal,
            publisher,
            "*",
            AclOperation.IDEMPOTENT_WRITE,
            allow,
        )
    )
    for topic in (
        "artifact.ingest-requested.v1",
        "model.eval-requested.v1",
        "rag.index-requested.v1",
    ):
        for operation in (AclOperation.DESCRIBE, AclOperation.READ):
            bindings.append(
                AclBinding(ResourceType.TOPIC, topic, literal, consumer, "*", operation, allow)
            )
    bindings.append(
        AclBinding(
            ResourceType.GROUP,
            "decision-python-async-v1",
            literal,
            consumer,
            "*",
            AclOperation.READ,
            allow,
        )
    )
    return tuple(bindings)


def materialize_exact_acls(
    admin: AdminClient,
    *,
    deadline_seconds: float = 10.0,
) -> None:
    expected = exact_acl_bindings()
    futures = cast(
        dict[AclBinding, _AdminFuture],
        admin.create_acls(list(expected), request_timeout=10.0),
    )
    for future in futures.values():
        future.result()
    deadline = time.monotonic() + deadline_seconds
    while True:
        remaining = deadline - time.monotonic()
        inventory = admin.describe_acls(
            AclBindingFilter(
                ResourceType.ANY,
                cast(str, None),
                ResourcePatternType.ANY,
                cast(str, None),
                cast(str, None),
                AclOperation.ANY,
                AclPermissionType.ANY,
            ),
            request_timeout=min(10.0, max(1.0, remaining)),
        ).result()
        if set(inventory) == set(expected):
            return
        if remaining <= 0:
            raise RuntimeError("Kafka ACL inventory mismatch.")
        time.sleep(min(0.25, remaining))


def materialize_exact_topics(
    admin: AdminClient,
    *,
    deadline_seconds: float = 120.0,
) -> None:
    expected = exact_topic_configs()
    deadline = time.monotonic() + deadline_seconds
    pending = expected.copy()
    while pending:
        futures = admin.create_topics(
            [
                NewTopic(name, num_partitions=3, replication_factor=1, config=config)
                for name, config in sorted(pending.items())
            ],
            request_timeout=min(10.0, max(1.0, deadline - time.monotonic())),
        )
        retry: dict[str, dict[str, str]] = {}
        for name, future in futures.items():
            try:
                future.result()
            except KafkaException as exc:
                error = exc.args[0]
                if (
                    isinstance(error, KafkaError)
                    and error.code() == KafkaError.TOPIC_ALREADY_EXISTS
                ):
                    continue
                retry[name] = pending[name]
        if not retry:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("Kafka topic initialization deadline exceeded.")
        pending = retry
        time.sleep(1.0)

    metadata = admin.list_topics(timeout=min(10.0, max(1.0, deadline - time.monotonic())))
    actual = {name for name in metadata.topics if not name.startswith("__")}
    if actual != set(expected):
        raise RuntimeError("Kafka topic inventory mismatch.")
    for name in sorted(expected):
        topic = metadata.topics[name]
        partitions = getattr(topic, "partitions", None)
        if (
            getattr(topic, "error", None) is not None
            or not isinstance(partitions, dict)
            or len(partitions) != 3
            or any(
                len(getattr(partition, "replicas", ())) != 1 for partition in partitions.values()
            )
        ):
            raise RuntimeError("Kafka topic partition or replication mismatch.")

    resources = [ConfigResource(ConfigResource.Type.TOPIC, name) for name in sorted(expected)]
    config_futures = admin.describe_configs(
        resources,
        request_timeout=min(10.0, max(1.0, deadline - time.monotonic())),
    )
    actual_retention: dict[str, str] = {}
    for resource, future in config_futures.items():
        retention = future.result().get("retention.ms")
        value = getattr(retention, "value", None)
        if not isinstance(value, str):
            raise RuntimeError("Kafka topic retention is unavailable.")
        actual_retention[resource.name] = value
    if actual_retention != {name: config["retention.ms"] for name, config in expected.items()}:
        raise RuntimeError("Kafka topic retention mismatch.")


def main() -> None:
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP_SERVER", "")
    protocol = os.environ.get("KAFKA_SECURITY_PROTOCOL", "")
    username = os.environ.get("KAFKA_SASL_USERNAME", "")
    password = os.environ.get("KAFKA_SASL_PASSWORD", "")
    if re.fullmatch(r"127\.0\.0\.1:[1-9][0-9]{0,4}", bootstrap) is None:
        raise RuntimeError("KAFKA_BOOTSTRAP_SERVER must be numeric loopback.")
    if (
        protocol != "SASL_PLAINTEXT"
        or username != "p1_kafka_admin"
        or not 32 <= len(password.encode()) <= 128
    ):
        raise RuntimeError("Kafka topic initializer principal is invalid.")
    admin = AdminClient(
        {
            "bootstrap.servers": bootstrap,
            "security.protocol": protocol,
            "sasl.mechanism": "PLAIN",
            "sasl.username": username,
            "sasl.password": password,
            "client.id": "p1-offline-topic-initializer",
        }
    )
    materialize_exact_topics(admin)
    materialize_exact_acls(admin)


if __name__ == "__main__":
    main()
