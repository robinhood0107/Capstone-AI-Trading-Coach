package com.capstone.decision.infrastructure.mcp

import com.capstone.decision.application.rag.RagAnswerMode
import com.capstone.decision.application.rag.RagAskCommand
import com.capstone.decision.application.rag.RagV2RuntimeService
import com.capstone.decision.application.rag.RagV2VertexEvidence
import com.capstone.decision.application.rag.RagV2VertexResponseValidator
import com.capstone.decision.infrastructure.vertex.S49StrongLlmProperties
import org.slf4j.LoggerFactory
import org.springframework.ai.mcp.annotation.McpTool
import org.springframework.ai.mcp.annotation.McpToolParam
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.security.core.context.SecurityContextHolder
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken
import org.springframework.stereotype.Component
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.Instant
import java.util.UUID

data class McpRagSearchResponse(
    val researchContext: String,
    val expiresAt: Instant,
    val evidence: List<McpEvidenceItem>,
)

data class McpEvidenceItem(
    val citationId: String,
    val text: String,
    val contentSha256: String,
)

data class McpWebSearchResponse(
    val researchContext: String,
    val results: List<RegisteredResearchSource>,
)

data class McpWebReadResponse(
    val researchContext: String,
    val evidence: McpEvidenceItem,
    val canonicalUrl: String,
    val title: String,
    val discoveredLinks: List<RegisteredResearchSource>,
)

data class McpAnswerValidationResponse(
    val status: String,
    val warnings: List<String>,
    val validationReceipt: String,
    val expiresAt: Instant,
)

data class McpAnswerSaveResponse(
    val saved: Boolean,
    val answerId: String,
)

/** Provider-neutral MCP surface. owner identity는 tool argument가 아니라 검증된 `/mcp` JWT subject에서만 읽는다. */
@Component
@ConditionalOnProperty(name = ["app.rag-v2.web.enabled"], havingValue = "true")
class CapstoneMcpTools(
    private val ragService: RagV2RuntimeService,
    private val contexts: McpResearchContextRegistry,
    private val researchTools: ResearchToolFacade,
    private val properties: RagWebToolProperties,
    private val validationReceipts: McpAnswerValidationReceiptRegistry,
    private val strongLlmProperties: S49StrongLlmProperties,
    private val admission: McpResearchAdmissionLimiter,
    private val webEvidenceMetadata: S49WebEvidenceMetadataPort,
) {
    private val validator = RagV2VertexResponseValidator()

    init {
        contexts.bindCloseListener(researchTools::closeSession)
    }

    @McpTool(
        name = "capstone_rag_search",
        description = "Search the consented Capstone public and owner RAG corpus and create an owner-bound research context.",
        generateOutputSchema = true,
    )
    fun ragSearch(
        @McpToolParam(description = "Question to retrieve evidence for", required = true) question: String,
        @McpToolParam(description = "CONCISE or DETAILED", required = true) mode: String,
        @McpToolParam(description = "RAG topic allowlist", required = true) topics: List<String>,
    ): McpRagSearchResponse {
        val caller = caller("mcp:rag.public")
        val answerMode = RagAnswerMode.valueOf(mode)
        require(question.isNotBlank() && question.toByteArray(StandardCharsets.UTF_8).size <= 4_096)
        require(
            topics.isNotEmpty() &&
                topics.size <= ALLOWED_TOPICS.size &&
                topics.distinct().size == topics.size &&
                topics.all(ALLOWED_TOPICS::contains),
        )
        val includeOwner = hasScope("mcp:rag.owner")
        if (includeOwner) requireUpdatedConsent(caller.ownerUserId)
        val requestId = "req_mcp_${UUID.randomUUID().toString().replace("-", "")}"
        val result =
            ragService.searchEvidence(
                caller.ownerUserId,
                requestId,
                RagAskCommand(question, answerMode, emptyList(), topics),
                includeOwner,
            )
        // retrieval 중 REVOKE/policy rotation이 발생해도 owner evidence를 MCP 응답으로 내보내지 않는다.
        if (includeOwner) requireUpdatedConsent(caller.ownerUserId)
        val (context, receipt) =
            contexts.create(
                caller.ownerUserId,
                caller.oauthClientId,
                question,
                mode,
                topics,
                requestId,
                result.scope,
                result.citations,
                result.evidence,
            )
        try {
            researchTools.openSession(context.id)
            researchTools.registerUserRoots(context.id, question)
            return McpRagSearchResponse(receipt, context.expiresAt, contexts.evidenceSnapshot(context).map(::evidenceItem))
        } catch (error: Exception) {
            contexts.close(context.id)
            throw error
        }
    }

    @McpTool(
        name = "capstone_web_search",
        description = "Search the internal SearXNG service within an existing owner-bound research context.",
        generateOutputSchema = true,
    )
    fun webSearch(
        @McpToolParam(description = "HMAC-bound research context", required = true) researchContext: String,
        @McpToolParam(description = "Public web search query", required = true) query: String,
        @McpToolParam(description = "CONCISE or DETAILED", required = true) mode: String,
    ): McpWebSearchResponse {
        val caller = caller("mcp:web.read")
        requirePublicWebQuery(query)
        val context = requireCurrentContext(researchContext, caller)
        val budget = properties.budget(mode)
        contexts.reserveSearch(context, mode, budget.maxSearches)
        admission.acquireSearch(caller)
        researchTools.openSession(context.id)
        val results = researchTools.search(context.id, query)
        contexts.addSearchableUrls(context, results.map { it.url })
        return McpWebSearchResponse(contexts.refreshedReceipt(context), results)
    }

    @McpTool(
        name = "capstone_web_read",
        description = "Read one HTTPS URL returned by capstone_web_search and add bounded normalized text as evidence.",
        generateOutputSchema = true,
    )
    fun webRead(
        @McpToolParam(description = "HMAC-bound research context", required = true) researchContext: String,
        @McpToolParam(description = "Preferred opaque resultId from search or discovered links", required = false) resultId: String?,
        @McpToolParam(description = "Compatibility URL; it must resolve to a registered source node", required = false) url: String?,
        @McpToolParam(description = "CONCISE or DETAILED", required = true) mode: String,
    ): McpWebReadResponse {
        val caller = caller("mcp:web.read")
        val context = requireCurrentContext(researchContext, caller)
        val budget = properties.budget(mode)
        val source = researchTools.resolve(context.id, resultId, url)
        contexts.reserveRead(context, mode, budget.maxReads, source.url)
        val registered =
            try {
                admission.withRead(caller) { researchTools.read(context.id, resultId, url) }
            } catch (error: S49WebReadRejectedException) {
                logger.warn("s4_9.web_read.rejected leaf={}", error.message)
                throw error
            } catch (_: Exception) {
                val leaf = "S4_9_WEB_READ_UNEXPECTED_REJECTED"
                logger.warn("s4_9.web_read.rejected leaf={}", leaf)
                throw S49WebReadRejectedException(leaf)
            }
        val document = registered.document
        val hash = sha256(document.text)
        webEvidenceMetadata.record(
            caller.ownerUserId,
            caller.oauthClientId,
            context.id,
            document.canonicalUrl,
            document.title,
            hash,
        )
        val stored = contexts.appendWebEvidence(context, document.text, hash)
        return McpWebReadResponse(
            contexts.refreshedReceipt(context),
            evidenceItem(stored),
            document.canonicalUrl,
            document.title,
            registered.discoveredLinks,
        )
    }

    @McpTool(
        name = "capstone_answer_validate",
        description = "Validate a structured draft against exact citations, quotes, numbers, owner scope, and advice rules.",
        generateOutputSchema = true,
    )
    fun answerValidate(
        @McpToolParam(description = "HMAC-bound research context", required = true) researchContext: String,
        @McpToolParam(description = "Structured Strong LLM answer JSON", required = true) draft: String,
    ): McpAnswerValidationResponse {
        val caller = caller("mcp:answer.validate")
        val context = requireCurrentContext(researchContext, caller)
        val evidence = contexts.evidenceSnapshot(context)
        val validated = validator.validate(draft, evidence)
        val receipt = validationReceipts.issue(caller, context, evidence, draft, validated.validationStatus.name)
        return McpAnswerValidationResponse(validated.validationStatus.name, validated.warnings, receipt.value, receipt.expiresAt)
    }

    @McpTool(
        name = "capstone_answer_save",
        description = "Explicitly save a previously validated draft using its one-use validation receipt.",
        generateOutputSchema = true,
    )
    fun answerSave(
        @McpToolParam(description = "One-use validation receipt", required = true) validationReceipt: String,
        @McpToolParam(description = "The exact structured draft validated earlier", required = true) draft: String,
    ): McpAnswerSaveResponse {
        val caller = caller("mcp:history.write")
        val contextId = validationReceipts.contextId(caller, validationReceipt, draft)
        requireCurrentContext(contexts.requireById(contextId, caller.ownerUserId, caller.oauthClientId), caller)
        val answerId = validationReceipts.consume(caller, validationReceipt, draft)
        contexts.close(contextId)
        return McpAnswerSaveResponse(true, answerId)
    }

    private fun caller(requiredScope: String): McpCaller {
        val authentication =
            SecurityContextHolder.getContext().authentication as? JwtAuthenticationToken
                ?: throw IllegalStateException("MCP OAuth authentication required")
        require(authentication.authorities.any { it.authority == "SCOPE_$requiredScope" })
        val clientId = authentication.token.getClaimAsString("client_id")
        require(clientId != null && (MCP_CLIENT_ID.matches(clientId) || CIMD_CLIENT_ID.matches(clientId)))
        require(OWNER_ID.matches(authentication.name))
        return McpCaller(authentication.name, clientId)
    }

    private fun hasScope(requiredScope: String): Boolean {
        val authentication =
            SecurityContextHolder.getContext().authentication as? JwtAuthenticationToken
                ?: throw IllegalStateException("MCP OAuth authentication required")
        return authentication.authorities.any { it.authority == "SCOPE_$requiredScope" }
    }

    private fun requireCurrentContext(
        receipt: String,
        caller: McpCaller,
    ): McpResearchContext = requireCurrentContext(contexts.require(receipt, caller.ownerUserId, caller.oauthClientId), caller)

    private fun requireCurrentContext(
        context: McpResearchContext,
        caller: McpCaller,
    ): McpResearchContext {
        // 공개 근거만 가진 context에는 개인문서 외부 처리 동의를 요구하지 않는다.
        if (context.retrievalCitations.any { it.citationKind == "LOCAL_DOCUMENT" }) {
            require(hasScope("mcp:rag.owner"))
            requireUpdatedConsent(caller.ownerUserId)
        }
        ragService.requireResearchEvidenceCurrent(
            caller.ownerUserId,
            context.requestId,
            context.retrievalScope,
            context.topics,
            context.retrievalCitations,
            context.retrievalEvidence,
        )
        return context
    }

    private fun requireUpdatedConsent(ownerUserId: String) {
        val consent = ragService.effectiveConsent(ownerUserId)
        require(
            consent.effective &&
                consent.policyDigest == strongLlmProperties.ownerConsentPolicySha256 &&
                consent.processorSetDigest == strongLlmProperties.ownerConsentProcessorSetSha256,
        )
    }

    private fun evidenceItem(value: RagV2VertexEvidence) = McpEvidenceItem(value.citationId, value.canonicalText, value.canonicalTextSha256)

    private fun sha256(value: String): String =
        MessageDigest.getInstance("SHA-256").digest(value.toByteArray(StandardCharsets.UTF_8)).joinToString("") { "%02x".format(it) }

    private companion object {
        val logger = LoggerFactory.getLogger(CapstoneMcpTools::class.java)
        val OWNER_ID = Regex("^usr_[a-z0-9][a-z0-9_-]{2,95}$")
        val MCP_CLIENT_ID = Regex("^mcp_[a-z0-9][a-z0-9._-]{2,95}$")
        val CIMD_CLIENT_ID = Regex("^https://[A-Za-z0-9.-]+/[A-Za-z0-9._~!$&'()*+,;=:@%/-]{1,190}$")
        val ALLOWED_TOPICS =
            setOf(
                "API",
                "DATA",
                "FINANCIAL_ENGINEERING",
                "METHODOLOGY",
                "PRODUCT_RISK",
                "RISK",
            )
    }
}

data class McpCaller(
    val ownerUserId: String,
    val oauthClientId: String,
)
