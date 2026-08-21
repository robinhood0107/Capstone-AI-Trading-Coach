from __future__ import annotations

import re
import unittest

import yaml

from contracts.generate_s7_s8_contracts import BASE_TOPICS, ROOT


class S7KafkaComposeTest(unittest.TestCase):
    def test_kraft_topics_and_authenticated_ui_are_explicit(self) -> None:
        compose = yaml.safe_load((ROOT / "infra/docker-compose.infra.yml").read_text())
        services = compose["services"]
        kafka = services["kafka"]
        self.assertEqual(["kafka"], kafka["profiles"])
        self.assertTrue(kafka["ports"][0].startswith("127.0.0.1:"))
        self.assertEqual("false", kafka["environment"]["KAFKA_AUTO_CREATE_TOPICS_ENABLE"])
        self.assertIn("INTERNAL://kafka:29092", kafka["environment"]["KAFKA_ADVERTISED_LISTENERS"])
        self.assertIn("EXTERNAL://127.0.0.1:", kafka["environment"]["KAFKA_ADVERTISED_LISTENERS"])

        initializer = services["kafka-topic-init"]
        self.assertEqual(["kafka"], initializer["profiles"])
        self.assertEqual("service_healthy", initializer["depends_on"]["kafka"]["condition"])

        ui = services["kafka-ui"]
        self.assertEqual(["kafka-ui"], ui["profiles"])
        self.assertTrue(ui["ports"][0].startswith("127.0.0.1:"))
        self.assertEqual("LOGIN_FORM", ui["environment"]["AUTH_TYPE"])
        self.assertIn(":?", ui["environment"]["SPRING_SECURITY_USER_NAME"])
        self.assertIn(":?", ui["environment"]["SPRING_SECURITY_USER_PASSWORD"])

    def test_topic_initializer_matches_contract_without_wildcards(self) -> None:
        script = (ROOT / "infra/kafka/create-topics.sh").read_text()
        block = script.split("base_topics=(", 1)[1].split(")", 1)[0]
        materialized = tuple(re.findall(r"^\s{2}([a-z][a-z0-9.-]+\.v1)$", block, re.MULTILINE))
        self.assertEqual(BASE_TOPICS, materialized)
        self.assertIn("--partitions 3 --replication-factor 1", script)
        self.assertIn("retention.ms=604800000", script)
        self.assertIn("retention.ms=2592000000", script)
        self.assertNotIn("--topic '*'", script)


if __name__ == "__main__":
    unittest.main()
