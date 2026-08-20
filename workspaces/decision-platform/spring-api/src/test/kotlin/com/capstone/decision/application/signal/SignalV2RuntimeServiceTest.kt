package com.capstone.decision.application.signal

import com.capstone.decision.api.common.ApiException
import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import java.time.Instant
import java.time.LocalDate

class SignalV2RuntimeServiceTest {
    @Test
    fun `no evidence returns honest all-abstain without root asOf`() {
        val service = SignalV2RuntimeService { SignalReadSnapshot(emptyList(), null) }
        val result = service.read("005930")

        assertThat(result.asOf).isNull()
        assertThat(result.modelReportId).isNull()
        assertThat(result.composite.status).isEqualTo("ABSTAIN")
        assertThat(result.components.ruleBaseline.reason).isEqualTo("MISSING_EVIDENCE")
        assertThat(result.components.lstm.reason).isEqualTo("MISSING_EVIDENCE")
        assertThat(result.components.lightgbm.reason).isEqualTo("MISSING_EVIDENCE")
        assertThat(result.components.hmmRegime.reason).isEqualTo("MISSING_EVIDENCE")
    }

    @Test
    fun `fresh LightGBM row remains research-only and cannot enter public runtime`() {
        val session = LocalDate.of(2026, 8, 14)
        val service =
            SignalV2RuntimeService {
                SignalReadSnapshot(
                    rows =
                        listOf(
                            StoredSignalComponent(
                                producer = "LIGHTGBM",
                                sourceWorkspace = "decision-platform",
                                sessionDate = session,
                                asOf = Instant.parse("2026-08-14T06:30:00Z"),
                                status = "AVAILABLE",
                                reason = null,
                                signal = "HOLD",
                                confidence = 0.0,
                                predictedReturn = null,
                                modelVersion = "lgbm-v1-fixture",
                                modelReportId = "mrp-fixture",
                            ),
                        ),
                    latestCompletedSession = session,
                )
            }
        val result = service.read("005930")

        assertThat(result.components.lightgbm.status).isEqualTo("ABSTAIN")
        assertThat(result.components.lightgbm.reason).isEqualTo("MISSING_EVIDENCE")
        assertThat(result.components.lightgbm.signal).isNull()
        assertThat(result.components.lightgbm.confidence).isNull()
        assertThat(result.asOf).isNull()
        assertThat(result.composite.status).isEqualTo("ABSTAIN")
    }

    @Test
    fun `stale LightGBM row also remains research-only without refreshing old asOf`() {
        val rowSession = LocalDate.of(2026, 8, 13)
        val service =
            SignalV2RuntimeService {
                SignalReadSnapshot(
                    listOf(
                        StoredSignalComponent(
                            "LIGHTGBM",
                            "decision-platform",
                            rowSession,
                            Instant.parse("2026-08-13T06:30:00Z"),
                            "AVAILABLE",
                            null,
                            "BUY",
                            0.8,
                            null,
                            "lgbm-v1-fixture",
                            "mrp-fixture",
                        ),
                    ),
                    LocalDate.of(2026, 8, 14),
                )
            }
        val result = service.read("005930")
        assertThat(result.components.lightgbm.reason).isEqualTo("MISSING_EVIDENCE")
        assertThat(result.components.lightgbm.asOf).isNull()
        assertThat(result.asOf).isNull()
    }

    @Test
    fun `storage failure and invalid symbol are typed API errors`() {
        val unavailable = SignalV2RuntimeService { throw SignalStorageUnavailableException() }
        assertThatThrownBy { unavailable.read("005930") }
            .isInstanceOf(ApiException::class.java)
            .extracting("errorCode")
            .isEqualTo(com.capstone.decision.api.common.ErrorCode.SIGNAL_UNAVAILABLE)
        assertThatThrownBy { unavailable.read("005930;DROP") }
            .isInstanceOf(ApiException::class.java)
            .extracting("errorCode")
            .isEqualTo(com.capstone.decision.api.common.ErrorCode.VALIDATION_ERROR)
    }

    @Test
    fun `nonfinite predicted return and oversized model identity abstain`() {
        val session = LocalDate.of(2026, 8, 14)
        val service =
            SignalV2RuntimeService {
                SignalReadSnapshot(
                    listOf(
                        StoredSignalComponent(
                            "LIGHTGBM",
                            "decision-platform",
                            session,
                            Instant.parse("2026-08-14T06:30:00Z"),
                            "AVAILABLE",
                            null,
                            "BUY",
                            0.8,
                            Double.NaN,
                            "x".repeat(129),
                            "mrp-fixture",
                        ),
                    ),
                    session,
                )
            }

        val result = service.read("005930")
        assertThat(result.components.lightgbm.status).isEqualTo("ABSTAIN")
        assertThat(result.components.lightgbm.reason).isEqualTo("MISSING_EVIDENCE")
    }
}
