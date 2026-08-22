package com.capstone.decision.infrastructure.async

import jakarta.annotation.PostConstruct
import org.apache.kafka.clients.admin.AdminClient
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.core.io.ClassPathResource
import org.springframework.kafka.core.KafkaAdmin
import org.springframework.stereotype.Component
import tools.jackson.databind.ObjectMapper
import java.util.concurrent.TimeUnit

@Component
class AsyncTopicCatalog(
    objectMapper: ObjectMapper,
) {
    val topics: Set<String> =
        ClassPathResource("contracts/s7-s8-contract-lock.v1.json").inputStream.use { input ->
            objectMapper
                .readTree(input)
                .path("topics")
                .values()
                .asSequence()
                .map { it.stringValue() }
                .toSet()
        }

    val baseTopics: Set<String> = topics.filterNot { ".retry.v1" in it || ".dlq.v1" in it }.toSet()

    init {
        require(topics.size == 36 && baseTopics.size == 12)
        require(topics.all(TOPIC::matches))
    }

    fun requireBaseTopic(topic: String) {
        require(topic in baseTopics) { "Unregistered async topic." }
    }

    fun requireTopic(topic: String) {
        require(topic in topics) { "Unregistered async topic." }
    }

    private companion object {
        val TOPIC = Regex("^[a-z][a-z0-9.-]{2,127}\\.v1$")
    }
}

@Component
@ConditionalOnProperty(name = ["app.async.adapter"], havingValue = "kafka")
class KafkaReadinessGate(
    private val kafkaAdmin: KafkaAdmin,
    private val catalog: AsyncTopicCatalog,
) {
    @PostConstruct
    fun verify() {
        AdminClient.create(kafkaAdmin.configurationProperties).use { admin ->
            val available = admin.listTopics().names().get(10, TimeUnit.SECONDS)
            val missing = catalog.topics - available
            require(missing.isEmpty()) { "Kafka async topics are not ready: ${missing.sorted().joinToString(",")}" }
            admin.describeCluster().clusterId().get(10, TimeUnit.SECONDS)
        }
    }
}
