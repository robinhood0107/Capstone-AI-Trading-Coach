package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class OwnerPerformanceReportMigrationContractTest {
    private val migration = Files.readString(Path.of("src/main/resources/db/migration/V158__owner_performance_report_generations.sql"))

    @Test
    fun `performance report keeps immutable generations and last success on failure`() {
        assertThat(migration).contains(
            "owner_performance_report_generations",
            "source_generation_sha256",
            "supersedes_report_id",
            "correction_of_report_id",
            "fixedForecast",
            "actualTrading",
            "record_owner_performance_report_failure_v1",
            "read_latest_owner_performance_report_authorized_v1",
        )
        assertThat(migration).doesNotContain("UPDATE public.owner_performance_report_generations")
    }
}
