package com.capstone.decision

import org.testcontainers.postgresql.PostgreSQLContainer
import org.testcontainers.utility.DockerImageName
import java.time.Duration

internal fun stablePostgresContainer(image: DockerImageName): PostgreSQLContainer =
    PostgreSQLContainer(image)
        .withStartupAttempts(3)
        .withStartupTimeout(Duration.ofMinutes(2))
