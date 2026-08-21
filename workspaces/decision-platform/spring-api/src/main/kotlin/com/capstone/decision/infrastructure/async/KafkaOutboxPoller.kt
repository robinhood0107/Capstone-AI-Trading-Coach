package com.capstone.decision.infrastructure.async

import net.javacrumbs.shedlock.spring.annotation.SchedulerLock
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.scheduling.annotation.Scheduled
import org.springframework.stereotype.Component

@Component
@ConditionalOnProperty(name = ["app.async.adapter"], havingValue = "kafka")
@ConditionalOnProperty(name = ["app.async.polling-enabled"], havingValue = "true", matchIfMissing = true)
class KafkaOutboxPoller(
    private val publisher: KafkaOutboxPublisher,
) {
    @Scheduled(fixedDelayString = "PT5S", initialDelayString = "PT5S", scheduler = "asyncTaskScheduler")
    @SchedulerLock(name = "s7.kafka-outbox-publisher", lockAtMostFor = "PT30S", lockAtLeastFor = "PT1S")
    fun poll() = publisher.poll()
}
