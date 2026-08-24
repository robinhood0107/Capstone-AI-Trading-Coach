from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from confluent_kafka.admin import ConfigResource

from app.async_worker.kafka_topics import (
    exact_acl_bindings,
    exact_topic_configs,
    main,
    materialize_exact_acls,
    materialize_exact_topics,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[5]


def test_topic_catalog_matches_generated_contract() -> None:
    contract = json.loads(
        (REPOSITORY_ROOT / "contracts" / "catalogs" / "s7-s8-contract-lock.v1.json").read_text(
            encoding="utf-8"
        )
    )
    configs = exact_topic_configs()
    assert set(configs) == set(contract["topics"])
    assert len(configs) == 36
    assert sum(name.endswith(".dlq.v1") for name in configs) == 12


def test_acl_catalog_separates_publisher_consumer_and_group() -> None:
    bindings = exact_acl_bindings()
    publisher = [binding for binding in bindings if binding.principal == "User:p1_outbox_publisher"]
    consumer = [binding for binding in bindings if binding.principal == "User:p1_async_worker"]
    assert len(publisher) == 49
    assert len(consumer) == 7
    assert all(binding.permission_type.name == "ALLOW" for binding in bindings)
    assert all(binding.principal != "User:ANONYMOUS" for binding in bindings)
    assert {binding.operation.name for binding in publisher} == {
        "DESCRIBE",
        "WRITE",
        "IDEMPOTENT_WRITE",
    }
    assert {binding.operation.name for binding in consumer} == {"DESCRIBE", "READ"}
    assert sum(binding.restype.name == "GROUP" for binding in consumer) == 1


def test_acl_materializer_waits_for_every_binding() -> None:
    success = Mock()
    success.result.return_value = None
    admin = Mock()
    bindings = exact_acl_bindings()
    admin.create_acls.return_value = {binding: success for binding in bindings}
    admin.describe_acls.return_value.result.return_value = list(bindings)
    materialize_exact_acls(admin)
    assert admin.create_acls.call_count == 1
    assert success.result.call_count == len(bindings)
    admin.describe_acls.assert_called_once()


def test_materializer_accepts_existing_topics_without_retry() -> None:
    expected = exact_topic_configs()
    already_exists = Mock()
    already_exists.result.side_effect = _topic_exists()
    admin = Mock()
    admin.create_topics.return_value = {name: already_exists for name in expected}
    _configure_topic_state(admin, expected)

    materialize_exact_topics(admin, deadline_seconds=1.0)

    created = admin.create_topics.call_args.args[0]
    assert len(created) == 36
    assert {item.topic for item in created} == set(expected)


def test_materializer_rejects_unregistered_topic() -> None:
    expected = exact_topic_configs()
    success = Mock()
    success.result.return_value = None
    admin = Mock()
    admin.create_topics.return_value = {name: success for name in expected}
    admin.list_topics.return_value = SimpleNamespace(
        topics={name: _topic_metadata() for name in expected} | {"foreign.topic.v1": object()}
    )

    with pytest.raises(RuntimeError, match="inventory mismatch"):
        materialize_exact_topics(admin, deadline_seconds=1.0)


def test_materializer_rejects_partition_or_replica_drift() -> None:
    expected = exact_topic_configs()
    success = Mock()
    success.result.return_value = None
    admin = Mock()
    admin.create_topics.return_value = {name: success for name in expected}
    _configure_topic_state(admin, expected)
    first = next(iter(expected))
    admin.list_topics.return_value.topics[first] = _topic_metadata(partition_count=2)

    with pytest.raises(RuntimeError, match="partition or replication mismatch"):
        materialize_exact_topics(admin, deadline_seconds=1.0)


def test_materializer_rejects_retention_drift() -> None:
    expected = exact_topic_configs()
    success = Mock()
    success.result.return_value = None
    admin = Mock()
    admin.create_topics.return_value = {name: success for name in expected}
    first = next(iter(expected))
    _configure_topic_state(admin, expected, retention_overrides={first: "1"})

    with pytest.raises(RuntimeError, match="retention mismatch"):
        materialize_exact_topics(admin, deadline_seconds=1.0)


def test_main_rejects_non_loopback_before_admin_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVER", "kafka:9092")
    monkeypatch.setenv("KAFKA_SECURITY_PROTOCOL", "SASL_PLAINTEXT")
    monkeypatch.setenv("KAFKA_SASL_USERNAME", "p1_kafka_admin")
    monkeypatch.setenv("KAFKA_SASL_PASSWORD", "a" * 32)
    with pytest.raises(RuntimeError, match="numeric loopback"):
        main()


def _topic_exists() -> Exception:
    from confluent_kafka import KafkaException
    from confluent_kafka.cimpl import KafkaError

    return KafkaException(KafkaError(KafkaError.TOPIC_ALREADY_EXISTS))


def _topic_metadata(*, partition_count: int = 3, replica_count: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        error=None,
        partitions={
            index: SimpleNamespace(replicas=list(range(replica_count)))
            for index in range(partition_count)
        },
    )


def _configure_topic_state(
    admin: Mock,
    expected: dict[str, dict[str, str]],
    *,
    retention_overrides: dict[str, str] | None = None,
) -> None:
    admin.list_topics.return_value = SimpleNamespace(
        topics={name: _topic_metadata() for name in expected} | {"__consumer_offsets": object()},
    )
    overrides = retention_overrides or {}
    config_futures = {}
    for name, config in expected.items():
        future = Mock()
        future.result.return_value = {
            "retention.ms": SimpleNamespace(value=overrides.get(name, config["retention.ms"])),
        }
        config_futures[ConfigResource(ConfigResource.Type.TOPIC, name)] = future
    admin.describe_configs.return_value = config_futures
