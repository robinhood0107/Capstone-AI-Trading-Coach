#!/usr/bin/env bash
set -euo pipefail

: "${KAFKA_BOOTSTRAP_SERVER:?KAFKA_BOOTSTRAP_SERVER is required}"

base_topics=(
  artifact.ingest-requested.v1
  artifact.ingested.v1
  signal.received.v1
  feature.updated.v1
  lightgbm.signal-generated.v1
  risk.context-updated.v1
  risk.decision-created.v1
  order.event-created.v1
  rag.index-requested.v1
  rag.index-completed.v1
  model.eval-requested.v1
  model.eval-completed.v1
)

for base_topic in "${base_topics[@]}"; do
  stem="${base_topic%.v1}"
  for topic in "$base_topic" "${stem}.retry.v1"; do
    /opt/kafka/bin/kafka-topics.sh \
      --bootstrap-server "$KAFKA_BOOTSTRAP_SERVER" \
      --create --if-not-exists --topic "$topic" \
      --partitions 3 --replication-factor 1 \
      --config retention.ms=604800000
  done
  /opt/kafka/bin/kafka-topics.sh \
    --bootstrap-server "$KAFKA_BOOTSTRAP_SERVER" \
    --create --if-not-exists --topic "${stem}.dlq.v1" \
    --partitions 3 --replication-factor 1 \
    --config retention.ms=2592000000
done

actual="$({ /opt/kafka/bin/kafka-topics.sh --bootstrap-server "$KAFKA_BOOTSTRAP_SERVER" --list; } | grep -v '^__' | sort)"
expected="$({
  for base_topic in "${base_topics[@]}"; do
    stem="${base_topic%.v1}"
    printf '%s\n%s\n%s\n' "$base_topic" "${stem}.retry.v1" "${stem}.dlq.v1"
  done
} | sort)"
if [[ "$actual" != "$expected" ]]; then
  echo "Kafka topic inventory mismatch." >&2
  exit 1
fi
