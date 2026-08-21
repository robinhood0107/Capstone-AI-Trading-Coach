package com.capstone.decision.infrastructure.async

import com.zaxxer.hikari.HikariConfig
import com.zaxxer.hikari.HikariDataSource
import net.javacrumbs.shedlock.core.LockProvider
import net.javacrumbs.shedlock.provider.jdbctemplate.JdbcTemplateLockProvider
import net.javacrumbs.shedlock.spring.annotation.EnableSchedulerLock
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.boot.context.properties.EnableConfigurationProperties
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.scheduling.concurrent.ThreadPoolTaskScheduler
import javax.sql.DataSource

@Configuration
@EnableSchedulerLock(defaultLockAtMostFor = "PT30S")
@EnableConfigurationProperties(AsyncProperties::class, AsyncWorkerProperties::class, KafkaAsyncProperties::class)
class AsyncInfrastructureConfiguration {
    @Bean
    fun asyncPropertiesValidation(properties: AsyncProperties): AsyncPropertiesValidation {
        properties.validate()
        return AsyncPropertiesValidation
    }

    @Bean
    fun asyncWorkerPropertiesValidation(
        properties: AsyncProperties,
        workerProperties: AsyncWorkerProperties,
    ): AsyncWorkerPropertiesValidation {
        workerProperties.validate(properties.adapter)
        return AsyncWorkerPropertiesValidation
    }

    @Bean
    fun kafkaAsyncPropertiesValidation(
        properties: AsyncProperties,
        kafkaProperties: KafkaAsyncProperties,
    ): KafkaAsyncPropertiesValidation {
        kafkaProperties.validate(properties.adapter)
        return KafkaAsyncPropertiesValidation
    }

    @Bean
    fun lockProvider(dataSource: DataSource): LockProvider =
        JdbcTemplateLockProvider(
            JdbcTemplateLockProvider.Configuration
                .builder()
                .withJdbcTemplate(JdbcTemplate(dataSource))
                .usingDbTime()
                .build(),
        )

    @Bean("asyncTaskScheduler")
    fun asyncTaskScheduler(): ThreadPoolTaskScheduler =
        ThreadPoolTaskScheduler().apply {
            poolSize = 1
            setThreadNamePrefix("s7-async-poller-")
            setWaitForTasksToCompleteOnShutdown(true)
            setAwaitTerminationSeconds(10)
        }

    @Bean("streamMetricTaskScheduler")
    fun streamMetricTaskScheduler(): ThreadPoolTaskScheduler =
        ThreadPoolTaskScheduler().apply {
            poolSize = 1
            setThreadNamePrefix("s7-stream-metric-")
            setWaitForTasksToCompleteOnShutdown(false)
            setRemoveOnCancelPolicy(true)
        }

    @Bean(destroyMethod = "close")
    @ConditionalOnProperty(name = ["app.async.adapter"], havingValue = "db", matchIfMissing = true)
    fun asyncWorkerDatabase(properties: AsyncWorkerProperties): AsyncWorkerDatabase =
        AsyncWorkerDatabase(
            HikariDataSource(
                HikariConfig().apply {
                    jdbcUrl = properties.jdbcUrl
                    username = properties.username
                    password = properties.password
                    maximumPoolSize = 2
                    minimumIdle = 0
                    connectionTimeout = 500
                    validationTimeout = 500
                    initializationFailTimeout = -1
                    poolName = "s7-async-worker-pool"
                },
            ),
        )
}

class AsyncWorkerDatabase(
    val dataSource: HikariDataSource,
) : AutoCloseable {
    override fun close() = dataSource.close()
}

object AsyncPropertiesValidation

object AsyncWorkerPropertiesValidation

object KafkaAsyncPropertiesValidation
