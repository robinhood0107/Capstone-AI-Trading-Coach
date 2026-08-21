package com.capstone.decision.infrastructure.async

import com.capstone.decision.application.async.AcceptedAsyncJob
import com.capstone.decision.application.async.AsyncJobRequest
import com.capstone.decision.application.async.AsyncPipelinePort
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component

@Component
@ConditionalOnProperty(name = ["app.async.adapter"], havingValue = "kafka")
class KafkaAsyncPipelineAdapter(
    private val writer: JdbcAsyncRequestWriter,
) : AsyncPipelinePort {
    override fun request(command: AsyncJobRequest): AcceptedAsyncJob = writer.request(command)
}
