package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Path
import kotlin.io.path.readText

class S49LangGraphGroundingMigrationContractTest {
    private val sql =
        Path.of("src/main/resources/db/migration/V70__s4_9_langchain_grounding_provenance.sql").readText()

    @Test
    fun `V70은 기존 migration을 수정하지 않고 Google budget과 provenance를 forward 추가한다`() {
        assertThat(sql).contains(
            "s4_9_google_grounding_monthly_budget",
            "s4_9_google_grounding_reservations",
            "s4_9_grounding_source_nodes",
            "s4_9_grounding_support_segments",
            "s4_9_grounding_support_edges",
            "s4_9_search_attempts",
            "record_s4_9_strong_llm_usage_v2",
            "record_s4_9_grounding_provenance",
            "record_s4_9_read_provenance",
            "record_s4_9_search_attempt",
            "persist_s4_9_strong_llm_history_v2",
            "canonicalize_s4_9_strong_llm_citations_v2",
            "reserve_s4_9_google_grounding_budget",
            "settle_s4_9_google_grounding_budget",
        )
        assertThat(sql).contains("ENABLE ROW LEVEL SECURITY", "FORCE ROW LEVEL SECURITY", "SECURITY DEFINER")
        assertThat(sql).contains("raw_body_stored boolean NOT NULL DEFAULT false CHECK (NOT raw_body_stored)")
        assertThat(sql).doesNotContain("DROP TABLE", "DROP FUNCTION", "TRUNCATE", "raw_query text", "model_response")
    }

    @Test
    fun `V70 decision app은 table 직접 권한 없이 함수만 실행한다`() {
        assertThat(sql).contains("REVOKE ALL PRIVILEGES ON TABLE", "FROM decision_app")
        assertThat(sql).contains("GRANT EXECUTE ON FUNCTION public.reserve_s4_9_google_grounding_budget")
        assertThat(sql).contains("GRANT EXECUTE ON FUNCTION public.record_s4_9_grounding_provenance")
        assertThat(sql).contains("GRANT EXECUTE ON FUNCTION public.persist_s4_9_strong_llm_history_v2")
        assertThat(sql).contains("GRANT EXECUTE ON FUNCTION public.record_s4_9_strong_llm_usage_v2")
    }
}
