package com.capstone.decision.application.signal

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.time.Instant
import java.time.LocalDate

/**
 * 대상 세션이 지난 예측을 화면에서 지우지 않는다는 회귀 방어.
 *
 * 왜 필요한가. 예전에는 `row.sessionDate != latest`면 두 producer 모두
 * `STALE_EVIDENCE`로 ABSTAIN 했다. `latest`(V134의 `latest_completed_session`)는 이름과 달리
 * **아직 마감되지 않은 가장 이른 세션**이므로, 장 마감 순간부터 다음 아침 배치까지 모델 비교
 * 화면이 어느 종목을 골라도 전부 ABSTAIN이 됐다. 실측으로 재현된 상태가 정확히 그것이다 —
 * `latest=2026-09-08`, 배치 `target_session=2026-09-07`.
 *
 * 이 endpoint의 소비자는 대시보드 하나이고 주문 경로는
 * `p1_read_automation_runtime_state_v4`를 쓰므로, 값을 숨기는 대신 대상 세션이 지났다는
 * 사실을 warnings로 알린다. `reason` enum은 계약(`contracts/schemas`)에 고정돼 있어 새 사유
 * 코드를 만들 수 없다는 제약이 이 설계의 이유다.
 */
class SignalV3RuntimeServiceTest {
    @Test
    fun `대상 세션이 지나도 예측을 보여주고 지났다는 사실을 경고로 남긴다`() {
        // 배치는 09-07을 대상으로 만들어졌고, 지금 다음 미마감 세션은 09-08이다.
        val service = SignalV3RuntimeService(port(target = SESSION, latest = NEXT_SESSION))

        val response = service.read(SYMBOL)

        assertThat(response.components.ruleBaseline.status).isEqualTo("AVAILABLE")
        assertThat(response.components.lstm.status).isEqualTo("AVAILABLE")
        assertThat(response.composite.status).isEqualTo("AVAILABLE")
        // 값을 보여 주는 것과 그것이 지난 예측임을 말하는 것은 함께 가야 한다.
        assertThat(response.warnings).anyMatch { it.contains("target session $SESSION has already passed") }
    }

    @Test
    fun `대상 세션이 아직 오지 않았으면 지났다는 경고를 붙이지 않는다`() {
        val service = SignalV3RuntimeService(port(target = SESSION, latest = SESSION))

        val response = service.read(SYMBOL)

        assertThat(response.composite.status).isEqualTo("AVAILABLE")
        assertThat(response.warnings).noneMatch { it.contains("has already passed") }
    }

    @Test
    fun `식별할 수 없는 출력은 계속 ABSTAIN이다`() {
        // 신선도 판정을 뺀 것이지 출력 검증을 뺀 것이 아니다.
        val service =
            SignalV3RuntimeService(
                port(target = SESSION, latest = SESSION, signal = "SIDEWAYS"),
            )

        val response = service.read(SYMBOL)

        assertThat(response.components.ruleBaseline.status).isEqualTo("ABSTAIN")
        assertThat(response.components.ruleBaseline.reason).isEqualTo("UNIDENTIFIABLE_OUTPUT")
        assertThat(response.composite.reason).isEqualTo("REQUIRED_COMPONENT_UNAVAILABLE")
    }

    @Test
    fun `배치가 없으면 전부 ABSTAIN이다`() {
        val service = SignalV3RuntimeService { SignalV3ReadSnapshot(rows = emptyList(), latestCompletedSession = SESSION) }

        val response = service.read(SYMBOL)

        assertThat(response.components.ruleBaseline.reason).isEqualTo("MISSING_EVIDENCE")
        assertThat(response.components.lstm.reason).isEqualTo("MISSING_EVIDENCE")
        assertThat(response.composite.status).isEqualTo("ABSTAIN")
    }

    private fun port(
        target: LocalDate,
        latest: LocalDate,
        signal: String = "BUY",
    ) = SignalV3ProductionReadPort {
        SignalV3ReadSnapshot(
            rows =
                listOf(
                    row("RULE_BASELINE", target, signal),
                    row("LSTM", target, "HOLD"),
                ),
            latestCompletedSession = latest,
        )
    }

    private fun row(
        producer: String,
        target: LocalDate,
        signal: String,
    ) = StoredSignalV3Component(
        producer = producer,
        sourceWorkspace = "return-engine",
        sessionDate = target,
        asOf = Instant.parse("2026-09-06T23:10:00Z"),
        signal = signal,
        predictedReturn = 0.0123,
        modelVersion = "model-v1",
        modelReportId = "report-v1",
        sourceSession = SOURCE_SESSION,
    )

    private companion object {
        const val SYMBOL = "005930"
        val SESSION: LocalDate = LocalDate.parse("2026-09-07")
        val NEXT_SESSION: LocalDate = LocalDate.parse("2026-09-08")
        val SOURCE_SESSION: LocalDate = LocalDate.parse("2026-09-04")
    }
}
