package com.capstone.decision.application.automation

import com.capstone.decision.application.security.ActorRlsScopePort
import org.springframework.beans.factory.ObjectProvider
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.context.annotation.Primary
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Component
import org.springframework.stereotype.Service
import org.springframework.transaction.PlatformTransactionManager
import org.springframework.transaction.TransactionDefinition
import org.springframework.transaction.annotation.Transactional
import org.springframework.transaction.support.TransactionTemplate
import tools.jackson.databind.JsonNode
import tools.jackson.databind.ObjectMapper
import java.math.BigDecimal
import java.net.URI
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.text.Normalizer
import java.time.LocalDate
import java.util.HexFormat

data class AutomationEvidenceCandidate(
    val symbol: String,
    val expectedReturn: String,
    val modelConfidence: String,
    val priceKrw: Long,
    val lowerLimitKrw: Long,
    val upperLimitKrw: Long,
    val isEtfEtn: Boolean,
)

data class AutomationEvidenceSettings(
    val settingsSha256: String,
    val provider: String,
    val fallbackProvider: String?,
    val modelId: String?,
    val fallbackModelId: String?,
    val baseUrl: String?,
    val fallbackBaseUrl: String?,
    val answerLanguage: String,
    val dailyGenerateCallCap: Int,
    val aiJudgementEnabled: Boolean,
    val thinkingLevel: String,
)

data class RawAutomationEvidence(
    val citationId: String,
    val sourceId: String,
    val sourceType: String,
    val sourceEventDate: LocalDate?,
    val uri: String,
    val boundedQuote: String,
    val supportObserved: Boolean,
    val storedUriSha256: String? = null,
    val storedQuoteSha256: String? = null,
    val storedAgeWarning: Boolean? = null,
    /** Google redirect URI와 별개로 provider metadata가 관측한 원 출처 domain. */
    val sourceDomain: String? = null,
)

data class RawAutomationScreening(
    val symbol: String,
    val status: String,
    val verdict: String,
    val scoreBps: Int,
    val reason: String,
    val promptInjectionDetected: Boolean,
    val evidence: List<RawAutomationEvidence>,
)

data class RawAutomationScreeningBatch(
    val screenings: List<RawAutomationScreening>,
    val providerCallCount: Int,
    val groundingQueryCount: Int,
)

data class RawAutomationJudgeVerdict(
    val symbol: String,
    val scoreBps: Int,
    val veto: Boolean,
    val reason: String,
    val evidenceSpans: List<Pair<String, String>>,
)

data class RawAutomationJudgement(
    val candidates: List<RawAutomationJudgeVerdict>,
    val confidenceBps: Int,
    val summary: String,
    val providerCallCount: Int,
)

interface AutomationEvidenceProvider {
    fun screen(
        runId: String,
        candidates: List<AutomationEvidenceCandidate>,
        settings: AutomationEvidenceSettings,
    ): RawAutomationScreeningBatch

    fun judge(
        runId: String,
        candidates: List<AutomationEvidenceCandidate>,
        evidence: Map<String, List<RawAutomationEvidence>>,
        settings: AutomationEvidenceSettings,
    ): RawAutomationJudgement
}

@Component
@Primary
@ConditionalOnProperty(
    name = ["app.p1.automation.evidence-fixture-enabled"],
    havingValue = "true",
    matchIfMissing = false,
)
class FixtureAutomationEvidenceProvider : AutomationEvidenceProvider {
    override fun screen(
        runId: String,
        candidates: List<AutomationEvidenceCandidate>,
        settings: AutomationEvidenceSettings,
    ): RawAutomationScreeningBatch {
        require(settings.aiJudgementEnabled)
        val first = candidates.minBy { it.symbol }.symbol
        return RawAutomationScreeningBatch(
            screenings =
                candidates.map { candidate ->
                    RawAutomationScreening(
                        symbol = candidate.symbol,
                        status = "AVAILABLE",
                        verdict = "NO_VETO",
                        scoreBps = 5_000,
                        reason = if (candidate.symbol == first) "FIXTURE_VERIFIED_EVIDENCE" else "NO_EVIDENCE",
                        promptInjectionDetected = false,
                        evidence =
                            if (candidate.symbol == first) {
                                listOf(
                                    RawAutomationEvidence(
                                        citationId = "cit_fixture_${candidate.symbol}",
                                        sourceId = "src_official_dart",
                                        sourceType = "OFFICIAL_PRIMARY",
                                        sourceEventDate = null,
                                        uri = "https://dart.fss.or.kr/fixture/${candidate.symbol}",
                                        boundedQuote = "Fixture adverse disclosure for ${candidate.symbol}",
                                        supportObserved = true,
                                    ),
                                )
                            } else {
                                emptyList()
                            },
                    )
                },
            providerCallCount = 0,
            groundingQueryCount = 0,
        )
    }

    override fun judge(
        runId: String,
        candidates: List<AutomationEvidenceCandidate>,
        evidence: Map<String, List<RawAutomationEvidence>>,
        settings: AutomationEvidenceSettings,
    ): RawAutomationJudgement =
        RawAutomationJudgement(
            candidates =
                candidates.map { candidate ->
                    val spans = evidence[candidate.symbol].orEmpty()
                    RawAutomationJudgeVerdict(
                        symbol = candidate.symbol,
                        scoreBps = if (spans.isEmpty()) 5_000 else 2_000,
                        veto = spans.isNotEmpty(),
                        reason = if (spans.isEmpty()) "FIXTURE_NEUTRAL" else "FIXTURE_VERIFIED_VETO",
                        evidenceSpans = spans.map { it.citationId to it.boundedQuote },
                    )
                },
            confidenceBps = 7_500,
            summary = "fixture-only judgement",
            providerCallCount = 0,
        ).also { require(settings.aiJudgementEnabled) }
}

class AutomationEvidenceUnavailableException : RuntimeException("AUTOMATION_EVIDENCE_PROVIDER_UNAVAILABLE")

internal fun automationEvidenceSourceRegistry(objectMapper: ObjectMapper): Map<String, Pair<String, String>> {
    val resource =
        AutomationEvidenceService::class.java.classLoader
            .getResourceAsStream("contracts/p1-vertex-news-sources.v1.json")
            ?: throw IllegalStateException("automation news source registry missing")
    resource.use { stream ->
        val root = objectMapper.readTree(stream)
        return root.path("sources").associate { item ->
            item.path("domain").stringValue().lowercase() to
                (item.path("sourceId").stringValue() to item.path("sourceType").stringValue())
        }
    }
}

@Service
class AutomationEvidenceService(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val actorRlsScope: ActorRlsScopePort,
    private val provider: ObjectProvider<AutomationEvidenceProvider>,
    private val objectMapper: ObjectMapper,
    private val transactionManagerProvider: ObjectProvider<PlatformTransactionManager>,
) {
    private val sourceRegistry by lazy { loadSourceRegistry() }

    @Transactional
    fun screen(
        ownerUserId: String,
        payload: JsonNode,
    ): Map<String, Any> {
        val request = parseCandidates(payload)
        val jdbc = jdbc()
        openScope(jdbc, ownerUserId, "AUTOMATION_EVIDENCE_SCREEN", request.runId)
        val runContext = readRunContext(jdbc, ownerUserId, request.runId, "NEWS_SCREENING")
        val candidateSetSha256 = request.candidateSetSha256
        val inputSha256 =
            sha256(
                objectMapper.writeValueAsBytes(
                    mapOf(
                        "aiSettingsSha256" to runContext.settings.settingsSha256,
                        "candidateSetSha256" to candidateSetSha256,
                    ),
                ),
            )
        val prior = readScreenings(jdbc, request.runId)
        if (prior.isNotEmpty()) {
            if (prior.any { it.inputSha256 != inputSha256 } || prior.map { it.symbol }.toSet() != request.symbols) {
                throw AutomationEvidenceUnavailableException()
            }
            return screeningResponse(jdbc, request.runId, prior)
        }
        val transport = provider.getIfAvailable() ?: throw AutomationEvidenceUnavailableException()
        val reservation = reserveProviderOperation(ownerUserId, request.runId, "SCREEN", inputSha256, 1)
        if (!reservation.created) throw AutomationEvidenceUnavailableException()
        val raw: RawAutomationScreeningBatch
        val sanitized: List<SanitizedScreening>
        try {
            raw = transport.screen(request.runId, request.candidates, runContext.settings)
            if (
                raw.providerCallCount !in 0..1 ||
                raw.groundingQueryCount !in 0..32 ||
                (raw.groundingQueryCount > 0 && raw.providerCallCount != 1)
            ) {
                throw AutomationEvidenceUnavailableException()
            }
            if (
                raw.screenings.map { it.symbol }.toSet() != request.symbols ||
                raw.screenings.size != request.symbols.size
            ) {
                throw AutomationEvidenceUnavailableException()
            }
            sanitized = raw.screenings.map { sanitizeScreening(it, request.sessionDate) }
            persistScreening(
                ownerUserId,
                request,
                inputSha256,
                candidateSetSha256,
                raw,
                sanitized,
            )
        } catch (error: AutomationEvidenceUnavailableException) {
            failProviderOperation(ownerUserId, request.runId, "SCREEN", inputSha256)
            throw error
        } catch (_: RuntimeException) {
            failProviderOperation(ownerUserId, request.runId, "SCREEN", inputSha256)
            throw AutomationEvidenceUnavailableException()
        }
        return screeningResponse(jdbc, request.runId, readScreenings(jdbc, request.runId))
    }

    @Transactional
    fun judge(
        ownerUserId: String,
        payload: JsonNode,
    ): Map<String, Any> {
        val request = parseCandidates(payload)
        val jdbc = jdbc()
        openScope(jdbc, ownerUserId, "AUTOMATION_EVIDENCE_JUDGE", request.runId)
        val runContext = readRunContext(jdbc, ownerUserId, request.runId, "AI_JUDGING")
        val stored = readEvidence(jdbc, request.runId)
        val storedCandidateSetSha256 =
            jdbc.queryForObject(
                "SELECT candidate_set_sha256 FROM automation_v3_usage WHERE run_id=:runId",
                mapOf("runId" to request.runId),
                String::class.java,
            )
        if (storedCandidateSetSha256 != request.candidateSetSha256) {
            throw AutomationEvidenceUnavailableException()
        }
        val requestEvidence = stored.filterKeys { it in request.symbols }
        if (requestEvidence.values.all { it.isEmpty() }) throw AutomationEvidenceUnavailableException()
        val transport = provider.getIfAvailable() ?: throw AutomationEvidenceUnavailableException()
        val inputSha256 =
            sha256(
                objectMapper.writeValueAsBytes(
                    mapOf(
                        "candidates" to request.candidates.sortedBy { it.symbol },
                        "aiSettingsSha256" to runContext.settings.settingsSha256,
                        "evidence" to
                            requestEvidence
                                .toSortedMap()
                                .mapValues { (_, items) ->
                                    items.sortedBy { it.citationId }.map {
                                        mapOf(
                                            "citationId" to it.citationId,
                                            "quote" to it.boundedQuote,
                                            "quoteSha256" to it.storedQuoteSha256,
                                        )
                                    }
                                },
                    ),
                ),
            )
        val reservation = reserveProviderOperation(ownerUserId, request.runId, "JUDGE", inputSha256, 2)
        if (!reservation.created) {
            if (reservation.status == "COMPLETED" && reservation.resultJson != null) {
                return decodeJudgeReplay(reservation.resultJson)
            }
            throw AutomationEvidenceUnavailableException()
        }
        try {
            val raw =
                transport.judge(
                    request.runId,
                    request.candidates,
                    requestEvidence,
                    runContext.settings,
                )
            if (raw.providerCallCount !in 0..2 || raw.confidenceBps !in 0..10_000 || raw.summary.isBlank()) {
                throw AutomationEvidenceUnavailableException()
            }
            if (
                raw.candidates.size != request.symbols.size ||
                raw.candidates.map { it.symbol }.toSet() != request.symbols
            ) {
                throw AutomationEvidenceUnavailableException()
            }
            val response =
                mapOf(
                    "candidates" to
                        raw.candidates.map { verdict ->
                            validateJudgeVerdict(verdict, requestEvidence[verdict.symbol].orEmpty())
                        },
                    "confidenceBps" to raw.confidenceBps,
                    "providerCallCount" to raw.providerCallCount,
                    "summary" to raw.summary.take(1_000),
                )
            val resultBytes = objectMapper.writeValueAsBytes(response)
            if (resultBytes.size !in 2..16_384) throw AutomationEvidenceUnavailableException()
            completeProviderOperation(
                ownerUserId,
                request.runId,
                "JUDGE",
                inputSha256,
                raw.providerCallCount,
                0,
                String(resultBytes, StandardCharsets.UTF_8),
                sha256(resultBytes),
            )
            return response
        } catch (error: AutomationEvidenceUnavailableException) {
            failProviderOperation(ownerUserId, request.runId, "JUDGE", inputSha256)
            throw error
        } catch (_: RuntimeException) {
            failProviderOperation(ownerUserId, request.runId, "JUDGE", inputSha256)
            throw AutomationEvidenceUnavailableException()
        }
    }

    private fun persistScreening(
        ownerUserId: String,
        request: CandidateRequest,
        inputSha256: String,
        candidateSetSha256: String,
        raw: RawAutomationScreeningBatch,
        sanitized: List<SanitizedScreening>,
    ) {
        requiresNew().executeWithoutResult {
            val jdbc = jdbc()
            openScope(jdbc, ownerUserId, "AUTOMATION_EVIDENCE_SCREEN", request.runId)
            requireRun(jdbc, ownerUserId, request.runId, "NEWS_SCREENING")
            val evidenceSetSha256 =
                sanitized
                    .flatMap { it.evidence }
                    .sortedWith(compareBy({ it.symbol }, { it.citationId }))
                    .takeIf { it.isNotEmpty() }
                    ?.let { sha256(objectMapper.writeValueAsBytes(it)) }
            sanitized.forEach { item ->
                val quote = request.candidates.single { it.symbol == item.symbol }
                jdbc.update(
                    """
                    INSERT INTO automation_candidate_screenings(
                      run_id,user_id,symbol,status,verdict,score_bps,reason,prompt_injection_detected,
                      input_sha256,output_sha256,provider_call_count,quote_price_krw,lower_limit_krw,
                      upper_limit_krw,is_etf_etn,recorded_at
                    ) VALUES (
                      :runId,:ownerUserId,:symbol,:status,:verdict,:scoreBps,:reason,:injection,
                      :inputSha256,:outputSha256,:providerCalls,:price,:lower,:upper,:isEtf,statement_timestamp()
                    )
                    """.trimIndent(),
                    mapOf(
                        "runId" to request.runId,
                        "ownerUserId" to ownerUserId,
                        "symbol" to item.symbol,
                        "status" to item.status,
                        "verdict" to item.verdict,
                        "scoreBps" to item.scoreBps,
                        "reason" to item.reason.take(512),
                        "injection" to item.promptInjectionDetected,
                        "inputSha256" to inputSha256,
                        "outputSha256" to sha256(objectMapper.writeValueAsBytes(item)),
                        "providerCalls" to raw.providerCallCount,
                        "price" to quote.priceKrw,
                        "lower" to quote.lowerLimitKrw,
                        "upper" to quote.upperLimitKrw,
                        "isEtf" to quote.isEtfEtn,
                    ),
                )
                item.evidence.forEach { evidence ->
                    jdbc.update(
                        """
                        INSERT INTO automation_candidate_evidence(
                          run_id,symbol,citation_id,source_id,source_type,source_event_date,age_warning,
                          uri_sha256,bounded_quote,quote_sha256,verified,recorded_at
                        ) VALUES (
                          :runId,:symbol,:citationId,:sourceId,:sourceType,:sourceEventDate,:ageWarning,
                          :uriSha256,:boundedQuote,:quoteSha256,true,statement_timestamp()
                        )
                        """.trimIndent(),
                        mapOf(
                            "runId" to request.runId,
                            "symbol" to item.symbol,
                            "citationId" to evidence.citationId,
                            "sourceId" to evidence.sourceId,
                            "sourceType" to evidence.sourceType,
                            "sourceEventDate" to evidence.sourceEventDate,
                            "ageWarning" to evidence.ageWarning,
                            "uriSha256" to evidence.uriSha256,
                            "boundedQuote" to evidence.boundedQuote,
                            "quoteSha256" to evidence.quoteSha256,
                        ),
                    )
                }
            }
            jdbc.update(
                """
                INSERT INTO automation_v3_usage(
                  run_id,user_id,provider_call_count,screening_provider_call_count,
                  grounding_query_count,candidate_set_sha256,evidence_set_sha256,updated_at
                ) VALUES (
                  :runId,:ownerUserId,:providerCalls,:providerCalls,:groundingQueries,
                  :candidateSetSha256,:evidenceSetSha256,statement_timestamp()
                ) ON CONFLICT (run_id) DO UPDATE SET
                  provider_call_count=GREATEST(automation_v3_usage.provider_call_count,excluded.provider_call_count),
                  screening_provider_call_count=excluded.screening_provider_call_count,
                  grounding_query_count=excluded.grounding_query_count,
                  candidate_set_sha256=excluded.candidate_set_sha256,
                  evidence_set_sha256=excluded.evidence_set_sha256,updated_at=excluded.updated_at
                """.trimIndent(),
                mapOf(
                    "runId" to request.runId,
                    "ownerUserId" to ownerUserId,
                    "providerCalls" to raw.providerCallCount,
                    "groundingQueries" to raw.groundingQueryCount,
                    "candidateSetSha256" to candidateSetSha256,
                    "evidenceSetSha256" to evidenceSetSha256,
                ),
            )
            completeProviderOperation(
                jdbc,
                ownerUserId,
                request.runId,
                "SCREEN",
                inputSha256,
                raw.providerCallCount,
                raw.groundingQueryCount,
                null,
                sha256(objectMapper.writeValueAsBytes(sanitized.sortedBy { it.symbol })),
            )
        }
    }

    private fun reserveProviderOperation(
        ownerUserId: String,
        runId: String,
        phase: String,
        inputSha256: String,
        physicalCallCap: Int,
    ): ProviderReservation =
        requiresNew().execute {
            val jdbc = jdbc()
            openScope(jdbc, ownerUserId, evidenceOperation(phase), runId)
            val json =
                jdbc.queryForObject(
                    "SELECT p1_reserve_automation_ai_provider_v1(:owner,:runId,:phase,:inputSha,:cap)",
                    mapOf(
                        "owner" to ownerUserId,
                        "runId" to runId,
                        "phase" to phase,
                        "inputSha" to inputSha256,
                        "cap" to physicalCallCap,
                    ),
                    String::class.java,
                ) ?: throw AutomationEvidenceUnavailableException()
            val node = objectMapper.readTree(json)
            ProviderReservation(
                created = node.path("created").booleanValue(),
                status = node.path("status").stringValue(),
                resultJson = node.path("resultJson").takeUnless { it.isNull }?.stringValue(),
            )
        }

    private fun completeProviderOperation(
        ownerUserId: String,
        runId: String,
        phase: String,
        inputSha256: String,
        providerCallCount: Int,
        groundingQueryCount: Int,
        resultJson: String?,
        outputSha256: String,
    ) {
        requiresNew().executeWithoutResult {
            val jdbc = jdbc()
            openScope(jdbc, ownerUserId, evidenceOperation(phase), runId)
            completeProviderOperation(
                jdbc,
                ownerUserId,
                runId,
                phase,
                inputSha256,
                providerCallCount,
                groundingQueryCount,
                resultJson,
                outputSha256,
            )
        }
    }

    private fun completeProviderOperation(
        jdbc: NamedParameterJdbcTemplate,
        ownerUserId: String,
        runId: String,
        phase: String,
        inputSha256: String,
        providerCallCount: Int,
        groundingQueryCount: Int,
        resultJson: String?,
        outputSha256: String,
    ) {
        jdbc.query(
            """
            SELECT p1_complete_automation_ai_provider_v1(
              :owner,:runId,:phase,:inputSha,:providerCalls,:groundingQueries,:resultJson,:outputSha
            )
            """.trimIndent(),
            mapOf(
                "owner" to ownerUserId,
                "runId" to runId,
                "phase" to phase,
                "inputSha" to inputSha256,
                "providerCalls" to providerCallCount,
                "groundingQueries" to groundingQueryCount,
                "resultJson" to resultJson,
                "outputSha" to outputSha256,
            ),
        ) { _, _ -> }
    }

    private fun failProviderOperation(
        ownerUserId: String,
        runId: String,
        phase: String,
        inputSha256: String,
    ) {
        requiresNew().executeWithoutResult {
            val jdbc = jdbc()
            openScope(jdbc, ownerUserId, evidenceOperation(phase), runId)
            jdbc.query(
                "SELECT p1_fail_automation_ai_provider_v1(:owner,:runId,:phase,:inputSha)",
                mapOf(
                    "owner" to ownerUserId,
                    "runId" to runId,
                    "phase" to phase,
                    "inputSha" to inputSha256,
                ),
            ) { _, _ -> }
        }
    }

    private fun decodeJudgeReplay(resultJson: String): Map<String, Any> {
        val root = objectMapper.readTree(resultJson)
        val rawCandidates = root.path("candidates")
        require(rawCandidates.isArray && rawCandidates.size() in 1..31)
        val candidates =
            (0 until rawCandidates.size()).map { index ->
                val item = rawCandidates[index]
                require(item != null && item.isObject)
                val spans = item.path("evidenceSpans")
                require(spans.isArray && spans.size() <= 5)
                mapOf(
                    "evidenceSpans" to
                        (0 until spans.size()).map { spanIndex ->
                            val span = spans[spanIndex]
                            require(span != null && span.isObject)
                            mapOf(
                                "citationId" to span.path("citationId").stringValue(),
                                "quote" to span.path("quote").stringValue(),
                            )
                        },
                    "reason" to item.path("reason").stringValue(),
                    "scoreBps" to item.path("scoreBps").intValue(),
                    "symbol" to item.path("symbol").stringValue(),
                    "veto" to item.path("veto").booleanValue(),
                )
            }
        return mapOf(
            "candidates" to candidates,
            "confidenceBps" to root.path("confidenceBps").intValue(),
            "providerCallCount" to root.path("providerCallCount").intValue(),
            "summary" to root.path("summary").stringValue(),
        )
    }

    private fun evidenceOperation(phase: String) = if (phase == "SCREEN") "AUTOMATION_EVIDENCE_SCREEN" else "AUTOMATION_EVIDENCE_JUDGE"

    private fun requiresNew(): TransactionTemplate =
        TransactionTemplate(
            transactionManagerProvider.getIfAvailable()
                ?: throw AutomationEvidenceUnavailableException(),
        ).apply {
            propagationBehavior = TransactionDefinition.PROPAGATION_REQUIRES_NEW
        }

    internal fun sanitizeScreening(
        raw: RawAutomationScreening,
        sessionDate: LocalDate,
    ): SanitizedScreening {
        val injection =
            raw.promptInjectionDetected ||
                containsPromptInjection(raw.reason) ||
                raw.evidence.any { containsPromptInjection(it.boundedQuote) }
        if (injection) {
            return SanitizedScreening(raw.symbol, "ABSTAIN", "NO_VETO", 5_000, "PROMPT_INJECTION", true, emptyList())
        }
        val evidence = raw.evidence.mapNotNull { validateEvidence(raw.symbol, it, sessionDate) }.take(5)
        val supported = evidence.isNotEmpty()
        return SanitizedScreening(
            symbol = raw.symbol,
            status = if (raw.status == "ABSTAIN") "ABSTAIN" else "AVAILABLE",
            verdict = if (raw.verdict == "VETO_BUY" && supported) "VETO_BUY" else "NO_VETO",
            scoreBps = if (supported && raw.scoreBps in 0..10_000) raw.scoreBps else 5_000,
            reason = raw.reason.ifBlank { "NO_EVIDENCE" },
            promptInjectionDetected = false,
            evidence = evidence,
        )
    }

    private fun validateEvidence(
        symbol: String,
        raw: RawAutomationEvidence,
        sessionDate: LocalDate,
    ): SanitizedEvidence? {
        if (!raw.supportObserved || raw.sourceEventDate?.isAfter(sessionDate) == true) return null
        val uri = runCatching { URI(raw.uri) }.getOrNull() ?: return null
        if (uri.scheme != "https" || uri.userInfo != null || uri.fragment != null) return null
        val uriHost = uri.host?.lowercase() ?: return null
        val observedDomain = raw.sourceDomain?.lowercase() ?: uriHost
        if (
            raw.sourceDomain != null &&
            uriHost != "vertexaisearch.cloud.google.com" &&
            uriHost != observedDomain &&
            !uriHost.endsWith(".$observedDomain")
        ) {
            return null
        }
        val registration = sourceRegistry[observedDomain] ?: return null
        if (registration.first != raw.sourceId || registration.second != raw.sourceType) return null
        if (!CITATION.matches(raw.citationId) || raw.boundedQuote.length !in 1..240) return null
        val quoteSha = sha256(raw.boundedQuote.toByteArray(StandardCharsets.UTF_8))
        return SanitizedEvidence(
            symbol,
            raw.citationId,
            raw.sourceId,
            raw.sourceType,
            raw.sourceEventDate,
            raw.sourceEventDate?.isBefore(sessionDate) == true,
            sha256(raw.uri.toByteArray(StandardCharsets.UTF_8)),
            raw.boundedQuote,
            quoteSha,
        )
    }

    internal fun validateJudgeVerdict(
        raw: RawAutomationJudgeVerdict,
        evidence: List<RawAutomationEvidence>,
    ): Map<String, Any> {
        val supported = evidence.associate { it.citationId to it.boundedQuote }
        val distinctSpans = raw.evidenceSpans.distinct().take(5)
        val verifiedSpans = distinctSpans.filter { supported[it.first] == it.second }
        val spansValid =
            raw.evidenceSpans.isNotEmpty() &&
                raw.evidenceSpans.size <= 5 &&
                verifiedSpans.size == raw.evidenceSpans.size
        return mapOf(
            "evidenceSpans" to
                (if (spansValid) verifiedSpans else emptyList()).map {
                    mapOf("citationId" to it.first, "quote" to it.second)
                },
            "reason" to raw.reason.ifBlank { "UNSUPPORTED_OUTPUT" }.take(512),
            "scoreBps" to if (spansValid && raw.scoreBps in 0..10_000) raw.scoreBps else 5_000,
            "symbol" to raw.symbol,
            "veto" to (raw.veto && spansValid),
        )
    }

    private fun parseCandidates(payload: JsonNode): CandidateRequest {
        require(payload.isObject)
        require(payload.properties().map { it.key }.toSet() == REQUEST_FIELDS)
        val runId = payload.path("runId").stringValue()
        require(RUN_ID.matches(runId))
        val candidateSetSha256 = payload.path("candidateSetSha256").stringValue()
        require(HASH.matches(candidateSetSha256))
        val sessionDate = LocalDate.parse(payload.path("sessionDate").stringValue())
        val items = payload.path("candidates")
        require(items.isArray && items.size() in 1..31)
        val candidates =
            (0 until items.size())
                .map { index ->
                    val item = items[index]
                    require(item != null)
                    require(item.isObject)
                    require(item.properties().map { it.key }.toSet() == CANDIDATE_FIELDS)
                    val candidate =
                        AutomationEvidenceCandidate(
                            symbol = item.path("symbol").stringValue().also { require(SYMBOL.matches(it)) },
                            expectedReturn = decimalField(item, "expectedReturn", BigDecimal("-1"), BigDecimal.ONE),
                            modelConfidence = decimalField(item, "modelConfidence", BigDecimal.ZERO, BigDecimal.ONE),
                            priceKrw = positiveLongField(item, "priceKrw"),
                            lowerLimitKrw = positiveLongField(item, "lowerLimitKrw"),
                            upperLimitKrw = positiveLongField(item, "upperLimitKrw"),
                            isEtfEtn = item.path("isEtfEtn").also { require(it.isBoolean) }.booleanValue(),
                        )
                    require(candidate.lowerLimitKrw <= candidate.priceKrw)
                    require(candidate.priceKrw <= candidate.upperLimitKrw)
                    candidate
                }.toList()
        require(candidates.map { it.symbol }.toSet().size == candidates.size)
        return CandidateRequest(runId, sessionDate, candidateSetSha256, candidates)
    }

    private fun containsPromptInjection(value: String): Boolean {
        val normalized =
            Normalizer
                .normalize(value, Normalizer.Form.NFKC)
                .lowercase()
                .filterNot { Character.getType(it) == Character.FORMAT.toInt() }
        return normalized.any { Character.isISOControl(it) } || INJECTION.containsMatchIn(normalized)
    }

    private fun decimalField(
        item: JsonNode,
        name: String,
        minimum: BigDecimal,
        maximum: BigDecimal,
    ): String {
        val value = item.path(name).stringValue()
        require(value.length in 1..32)
        val decimal = value.toBigDecimalOrNull() ?: throw IllegalArgumentException("invalid decimal")
        require(decimal >= minimum && decimal <= maximum)
        return value
    }

    private fun positiveLongField(
        item: JsonNode,
        name: String,
    ): Long {
        val value = item.path(name)
        require(value.isIntegralNumber && value.canConvertToLong())
        return value.longValue().also { require(it > 0) }
    }

    private fun readRunContext(
        jdbc: NamedParameterJdbcTemplate,
        ownerUserId: String,
        runId: String,
        expectedState: String,
    ): RunEvidenceContext {
        val row =
            jdbc
                .query(
                    """
                    SELECT ai_settings_sha256,ai_settings_snapshot_json::text settings_json
                    FROM automation_runs
                    WHERE run_id=:runId AND user_id=:ownerUserId AND state=:state
                    """.trimIndent(),
                    mapOf("runId" to runId, "ownerUserId" to ownerUserId, "state" to expectedState),
                ) { result, _ ->
                    result.getString("ai_settings_sha256") to result.getString("settings_json")
                }.singleOrNull() ?: throw AutomationEvidenceUnavailableException()
        val settingsSha256 = row.first
        val root = row.second?.let { objectMapper.readTree(it) }
        if (settingsSha256 == null || !HASH.matches(settingsSha256) || root == null || !root.isObject) {
            throw AutomationEvidenceUnavailableException()
        }
        val settings =
            AutomationEvidenceSettings(
                settingsSha256 = settingsSha256,
                provider = root.path("provider").stringValue(),
                fallbackProvider = root.optionalString("fallbackProvider"),
                modelId = root.optionalString("modelId"),
                fallbackModelId = root.optionalString("fallbackModelId"),
                baseUrl = root.optionalString("baseUrl"),
                fallbackBaseUrl = root.optionalString("fallbackBaseUrl"),
                answerLanguage = root.path("answerLanguage").stringValue(),
                dailyGenerateCallCap = root.path("dailyGenerateCallCap").intValue(),
                aiJudgementEnabled = root.path("aiJudgementEnabled").booleanValue(),
                thinkingLevel = root.path("thinkingLevel").stringValue(),
            )
        if (
            !settings.aiJudgementEnabled ||
            settings.provider.isBlank() ||
            settings.answerLanguage !in setOf("ko", "en") ||
            settings.dailyGenerateCallCap !in 3..500 ||
            settings.thinkingLevel !in setOf("minimal", "low", "medium")
        ) {
            throw AutomationEvidenceUnavailableException()
        }
        return RunEvidenceContext(settings)
    }

    private fun JsonNode.optionalString(name: String): String? {
        val value = get(name) ?: return null
        return if (value.isNull) null else value.stringValue()
    }

    private fun requireRun(
        jdbc: NamedParameterJdbcTemplate,
        ownerUserId: String,
        runId: String,
        expectedState: String,
    ) {
        val valid =
            jdbc.queryForObject(
                "SELECT count(*)=1 FROM automation_runs WHERE run_id=:runId AND user_id=:ownerUserId AND state=:state",
                mapOf("runId" to runId, "ownerUserId" to ownerUserId, "state" to expectedState),
                Boolean::class.java,
            ) ?: false
        if (!valid) throw AutomationEvidenceUnavailableException()
    }

    private fun readScreenings(
        jdbc: NamedParameterJdbcTemplate,
        runId: String,
    ): List<StoredScreening> =
        jdbc.query(
            """
            SELECT symbol,status,verdict,score_bps,reason,input_sha256,provider_call_count,
                   quote_price_krw,lower_limit_krw,upper_limit_krw,is_etf_etn
            FROM automation_candidate_screenings WHERE run_id=:runId ORDER BY symbol
            """.trimIndent(),
            mapOf("runId" to runId),
        ) { row, _ ->
            StoredScreening(
                row.getString("symbol"),
                row.getString("status"),
                row.getString("verdict"),
                row.getInt("score_bps"),
                row.getString("reason"),
                row.getString("input_sha256"),
                row.getInt("provider_call_count"),
                row.getLong("quote_price_krw"),
                row.getLong("lower_limit_krw"),
                row.getLong("upper_limit_krw"),
                row.getBoolean("is_etf_etn"),
            )
        }

    private fun readEvidence(
        jdbc: NamedParameterJdbcTemplate,
        runId: String,
    ): Map<String, List<RawAutomationEvidence>> =
        jdbc
            .query(
                """
                SELECT symbol,citation_id,source_id,source_type,source_event_date,age_warning,
                       uri_sha256,bounded_quote,quote_sha256
                FROM automation_candidate_evidence WHERE run_id=:runId ORDER BY symbol,citation_id
                """.trimIndent(),
                mapOf("runId" to runId),
            ) { row, _ ->
                row.getString("symbol") to
                    RawAutomationEvidence(
                        row.getString("citation_id"),
                        row.getString("source_id"),
                        row.getString("source_type"),
                        row.getObject("source_event_date", LocalDate::class.java),
                        "https://stored.invalid",
                        row.getString("bounded_quote"),
                        true,
                        row.getString("uri_sha256"),
                        row.getString("quote_sha256"),
                        row.getBoolean("age_warning"),
                    )
            }.groupBy({ it.first }, { it.second })

    private fun screeningResponse(
        jdbc: NamedParameterJdbcTemplate,
        runId: String,
        rows: List<StoredScreening>,
    ): Map<String, Any> {
        val evidence = readEvidence(jdbc, runId)
        val usage =
            jdbc.queryForMap(
                "SELECT screening_provider_call_count,grounding_query_count FROM automation_v3_usage WHERE run_id=:runId",
                mapOf("runId" to runId),
            )
        return mapOf(
            "failed" to false,
            "groundingQueryCount" to (usage["grounding_query_count"] as Number).toInt(),
            "providerCallCount" to (usage["screening_provider_call_count"] as Number).toInt(),
            "screenings" to
                rows.map { item ->
                    mapOf(
                        "evidence" to
                            evidence[item.symbol].orEmpty().map { value ->
                                mapOf(
                                    "ageWarning" to (value.storedAgeWarning ?: false),
                                    "boundedQuote" to value.boundedQuote,
                                    "citationId" to value.citationId,
                                    "quoteSha256" to (value.storedQuoteSha256 ?: sha256(value.boundedQuote.toByteArray())),
                                    "sourceEventDate" to value.sourceEventDate?.toString(),
                                    "sourceId" to value.sourceId,
                                    "sourceType" to value.sourceType,
                                    "symbol" to item.symbol,
                                    "uriSha256" to (value.storedUriSha256 ?: sha256(value.uri.toByteArray())),
                                    "verified" to true,
                                )
                            },
                        "reason" to item.reason,
                        "scoreBps" to item.scoreBps,
                        "status" to item.status,
                        "symbol" to item.symbol,
                        "verdict" to item.verdict,
                    )
                },
        )
    }

    private fun loadSourceRegistry(): Map<String, Pair<String, String>> = automationEvidenceSourceRegistry(objectMapper)

    private fun openScope(
        jdbc: NamedParameterJdbcTemplate,
        ownerUserId: String,
        operation: String,
        runId: String,
    ) = actorRlsScope.open(jdbc, ownerUserId, operation, "AUTOMATION_RUN", runId)

    private fun jdbc(): NamedParameterJdbcTemplate = jdbcProvider.getIfAvailable() ?: throw AutomationEvidenceUnavailableException()

    private fun sha256(value: ByteArray): String = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value))

    private data class CandidateRequest(
        val runId: String,
        val sessionDate: LocalDate,
        val candidateSetSha256: String,
        val candidates: List<AutomationEvidenceCandidate>,
    ) {
        val symbols = candidates.map { it.symbol }.toSet()
    }

    private data class ProviderReservation(
        val created: Boolean,
        val status: String,
        val resultJson: String?,
    )

    private data class RunEvidenceContext(
        val settings: AutomationEvidenceSettings,
    )

    internal data class SanitizedEvidence(
        val symbol: String,
        val citationId: String,
        val sourceId: String,
        val sourceType: String,
        val sourceEventDate: LocalDate?,
        val ageWarning: Boolean,
        val uriSha256: String,
        val boundedQuote: String,
        val quoteSha256: String,
    )

    internal data class SanitizedScreening(
        val symbol: String,
        val status: String,
        val verdict: String,
        val scoreBps: Int,
        val reason: String,
        val promptInjectionDetected: Boolean,
        val evidence: List<SanitizedEvidence>,
    )

    private data class StoredScreening(
        val symbol: String,
        val status: String,
        val verdict: String,
        val scoreBps: Int,
        val reason: String,
        val inputSha256: String,
        val providerCallCount: Int,
        val priceKrw: Long,
        val lowerLimitKrw: Long,
        val upperLimitKrw: Long,
        val isEtfEtn: Boolean,
    )

    private companion object {
        val RUN_ID = Regex("^auto_run_[0-9a-f]{32}$")
        val SYMBOL = Regex("^[0-9]{6}$")
        val CITATION = Regex("^cit_[A-Za-z0-9._:-]{1,96}$")
        val HASH = Regex("^[0-9a-f]{64}$")
        val INJECTION =
            Regex(
                "(?:ignore|disregard|override|bypass).{0,48}(?:previous|system|developer|instructions?|prompt)|" +
                    "(?:system|developer)\\s*(?:message|prompt)|(?:이전|기존).{0,24}지시.{0,24}무시|" +
                    "(?:도구|함수|mcp|플러그인).{0,20}(?:호출|실행)|시스템\\s*프롬프트",
                RegexOption.IGNORE_CASE,
            )
        val REQUEST_FIELDS = setOf("candidateSetSha256", "candidates", "runId", "sessionDate")
        val CANDIDATE_FIELDS =
            setOf(
                "expectedReturn",
                "isEtfEtn",
                "lowerLimitKrw",
                "modelConfidence",
                "priceKrw",
                "symbol",
                "upperLimitKrw",
            )
    }
}
