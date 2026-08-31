package com.capstone.decision.infrastructure.vertex

import com.capstone.decision.application.rag.RagV2GenerationBudget
import com.capstone.decision.application.rag.RagV2VertexActivationAuthorPort
import com.capstone.decision.application.rag.RagV2VertexPreparation
import org.slf4j.Logger
import org.slf4j.LoggerFactory
import org.springframework.beans.factory.ObjectProvider
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Component
import tools.jackson.databind.json.JsonMapper
import java.nio.file.Files
import java.nio.file.LinkOption
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.security.SecureRandom
import java.time.Clock
import java.time.Duration
import java.time.Instant
import java.time.temporal.ChronoUnit
import java.util.Base64
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

/**
 * 배포 정책으로 활성화 패킷을 저술한다.
 *
 * 왜 파일로 쓰나. 패킷을 읽고 검증하는 자리는 [PreS5VertexActivationReader] 하나뿐이고, 그
 * 검증(계약 ID, 코드 바인딩, 비용 상한 산술, 5분 만료, 파일 권한과 단일 링크)은 운영자가 저술하든
 * 배포 정책이 저술하든 똑같이 통과해야 한다. 우회로를 새로 내지 않고 같은 문을 지나가게 한다.
 *
 * 무엇이 바뀌고 무엇이 그대로인가. 바뀌는 것은 "호출마다 사람이 승인한다"가 "배포할 때 한 번
 * 승인한다"로 내려간 것뿐이다. 모델, 비용 상한, evidence 해시, 코드 바인딩, 물리 호출 상한,
 * 단일 사용 nonce는 그대로 강제된다. 사람이 곧 호출 한도이던 자리를 대신하려고 소유자별 하루
 * 상한을 정책에서 읽어 저술 전에 확인한다.
 *
 * 동시성. 패킷 파일은 하나이므로 저술과 생성이 겹치면 서로의 패킷을 읽을 수 있다. 저술부터
 * 생성까지를 [lock]으로 감싸는 책임은 호출자(`RagV2RuntimeService`)에게 있고, 여기서는 저술
 * 자체를 원자적 교체로 만든다.
 */
@Component
@ConditionalOnProperty(name = ["app.rag-v2.vertex.enabled"], havingValue = "true")
@ConditionalOnProperty(name = ["app.rag-v2.vertex.auto-activation-enabled"], havingValue = "true")
internal class PreS5VertexAutoActivationAuthor(
    private val properties: RagV2VertexProperties,
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val clock: Clock = Clock.systemUTC(),
) : RagV2VertexActivationAuthorPort {
    private val mapper = JsonMapper.builder().build()
    private val random = SecureRandom()
    private val lock = ReentrantLock()

    override fun author(
        ownerUserId: String,
        preparation: RagV2VertexPreparation,
    ): Boolean =
        lock.withLock {
            val policy = readPolicy()
            val reservedToday = countReservedToday(ownerUserId)
            if (reservedToday >= policy.dailyGenerateCallCap) {
                // 상한 자체와 도달 사실만 남긴다. 질문·소유자·근거는 남기지 않는다.
                LOGGER.warn(
                    "pre_s5_vertex_auto_activation_daily_cap_reached cap={} reserved={}",
                    policy.dailyGenerateCallCap,
                    reservedToday,
                )
                return@withLock false
            }
            writePacket(buildPacket(policy, preparation))
            true
        }

    override fun budget(ownerUserId: String): RagV2GenerationBudget {
        val cap = readPolicy().dailyGenerateCallCap
        val used = countReservedToday(ownerUserId)
        return RagV2GenerationBudget(
            dailyCap = cap,
            usedToday = used,
            remaining = maxOf(0, cap - used),
        )
    }

    private fun countReservedToday(ownerUserId: String): Int =
        requireNotNull(jdbcProvider.getObject())
            .queryForObject(
                "select public.count_rag_v2_immutable_vertex_usage_today(:ownerUserId)",
                MapSqlParameterSource("ownerUserId", ownerUserId),
                Int::class.java,
            ) ?: 0

    private fun buildPacket(
        policy: AutoActivationPolicy,
        preparation: RagV2VertexPreparation,
    ): Map<String, Any> {
        // 초 단위로 자른다. 예약 원장이 timestamptz로 왕복시키면 나노초가 잘려 나가고,
        // 그러면 패킷이 말한 만료와 원장이 돌려준 만료가 달라져 예약이 그 자리에서 닫힌다.
        val issuedAt = Instant.now(clock).truncatedTo(ChronoUnit.SECONDS)
        // 읽는 쪽이 5분 이하만 받는다. 준비된 scope의 남은 수명보다 길게 잡지 않는다.
        val expiresAt =
            minOf(issuedAt.plus(PACKET_LIFETIME), preparation.expiresAt)
                .truncatedTo(ChronoUnit.SECONDS)
                .let { if (it.isAfter(issuedAt)) it else issuedAt.plusSeconds(1) }
        val modelId = properties.modelId
        return linkedMapOf(
            "contractId" to "pre-s5-vertex-activation/v3",
            "provider" to "VERTEX_AI",
            "authenticationMode" to "SERVICE_ACCOUNT_OAUTH",
            "origin" to "https://aiplatform.googleapis.com",
            "endpoint" to
                "POST /v1/projects/${policy.projectId}/locations/global/publishers/google/models/" +
                "$modelId:generateContent",
            "authOrigin" to "https://oauth2.googleapis.com",
            "authEndpoint" to "POST /token",
            "projectId" to policy.projectId,
            "requestId" to preparation.requestId,
            "scopeClaimId" to preparation.scopeClaimId,
            "questionFingerprintHmac" to preparation.questionFingerprintHmac,
            "answerMode" to preparation.answerMode.name,
            "consentEventId" to preparation.consentEventId,
            "modelId" to modelId,
            "headCommit" to properties.headCommit,
            "treeDigest" to properties.treeDigest,
            "ciDigest" to properties.ciDigest,
            "securityDigest" to properties.securityDigest,
            "serviceAccountSecurityEvidenceSha256" to policy.serviceAccountSecurityEvidenceSha256,
            "dataGovernanceStateEvidenceSha256" to policy.dataGovernanceStateEvidenceSha256,
            "abuseMonitoringStateEvidenceSha256" to policy.abuseMonitoringStateEvidenceSha256,
            "modelAvailabilityEvidenceSha256" to policy.modelAvailabilityEvidenceSha256,
            "policySha256" to preparation.policyDigest,
            "processorSetSha256" to preparation.processorSetDigest,
            "issuedAt" to issuedAt.toString(),
            "expiresAt" to expiresAt.toString(),
            "logicalCallCap" to 1,
            "physicalCallCap" to 2,
            "tokenPhysicalCallCap" to 1,
            "generateContentPhysicalCallCap" to 1,
            "inputTokenCap" to policy.inputTokenCap,
            "outputTokenCap" to policy.outputTokenCap,
            "inputByteCap" to policy.inputByteCap,
            "costCapMicrousd" to policy.costCapMicrousd,
            "inputMicrousdPerToken" to policy.inputMicrousdPerToken,
            "outputMicrousdPerToken" to policy.outputMicrousdPerToken,
            "retryCount" to 0,
            "rawArtifactCount" to 0,
            "operator" to policy.operator,
            "nonce" to nonce(),
        )
    }

    private fun nonce(): String {
        val raw = ByteArray(24)
        random.nextBytes(raw)
        return "ps5auto" + Base64.getUrlEncoder().withoutPadding().encodeToString(raw)
    }

    private fun writePacket(packet: Map<String, Any>) {
        val control = Path.of(properties.localRoot).resolve(CONTROL_DIRECTORY).normalize()
        require(control.parent == Path.of(properties.localRoot))
        Files.createDirectories(control)
        Files.setPosixFilePermissions(control, DIRECTORY_PERMISSIONS)
        val target = control.resolve(PACKET_FILE)
        val staging = control.resolve("$PACKET_FILE.staging")
        val bytes = mapper.writeValueAsBytes(packet)
        try {
            Files.deleteIfExists(staging)
            Files.write(staging, bytes)
            Files.setPosixFilePermissions(staging, FILE_PERMISSIONS)
            // 읽는 쪽이 단일 링크와 0600을 요구한다. 부분 기록된 파일이 보이지 않도록 교체로 넣는다.
            Files.move(staging, target, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
            require(Files.isRegularFile(target, LinkOption.NOFOLLOW_LINKS))
        } finally {
            bytes.fill(0)
            Files.deleteIfExists(staging)
        }
    }

    private fun readPolicy(): AutoActivationPolicy {
        val path = Path.of(properties.autoActivationPolicyFile)
        require(Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS))
        require(Files.getPosixFilePermissions(path, LinkOption.NOFOLLOW_LINKS) == FILE_PERMISSIONS) {
            "Vertex auto-activation policy file must be owner-only 0600."
        }
        val root = mapper.readTree(Files.readAllBytes(path))
        require(root != null && root.isObject)
        require(root.get("contractId")?.stringValue() == POLICY_CONTRACT_ID)
        val policy =
            AutoActivationPolicy(
                projectId = text(root, "projectId"),
                operator = text(root, "operator"),
                dailyGenerateCallCap = integer(root, "dailyGenerateCallCap"),
                inputTokenCap = integer(root, "inputTokenCap"),
                outputTokenCap = integer(root, "outputTokenCap"),
                inputByteCap = integer(root, "inputByteCap"),
                costCapMicrousd = integer(root, "costCapMicrousd").toLong(),
                inputMicrousdPerToken = integer(root, "inputMicrousdPerToken").toLong(),
                outputMicrousdPerToken = integer(root, "outputMicrousdPerToken").toLong(),
                serviceAccountSecurityEvidenceSha256 = text(root, "serviceAccountSecurityEvidenceSha256"),
                dataGovernanceStateEvidenceSha256 = text(root, "dataGovernanceStateEvidenceSha256"),
                abuseMonitoringStateEvidenceSha256 = text(root, "abuseMonitoringStateEvidenceSha256"),
                modelAvailabilityEvidenceSha256 = text(root, "modelAvailabilityEvidenceSha256"),
            )
        // 읽는 쪽과 같은 산술을 저술 전에 먼저 확인한다. 여기서 막으면 패킷을 만들지 않는다.
        require(policy.dailyGenerateCallCap in 1..10_000)
        require(policy.inputByteCap + INPUT_TOKEN_SAFETY_MARGIN <= policy.inputTokenCap)
        require(
            policy.inputTokenCap.toLong() * policy.inputMicrousdPerToken +
                policy.outputTokenCap.toLong() * policy.outputMicrousdPerToken <= policy.costCapMicrousd,
        )
        require(SHA256.matches(policy.serviceAccountSecurityEvidenceSha256))
        require(SHA256.matches(policy.dataGovernanceStateEvidenceSha256))
        require(SHA256.matches(policy.abuseMonitoringStateEvidenceSha256))
        require(SHA256.matches(policy.modelAvailabilityEvidenceSha256))
        return policy
    }

    private fun text(
        root: tools.jackson.databind.JsonNode,
        field: String,
    ): String = requireNotNull(root.get(field)?.stringValue()) { "auto-activation policy field is missing" }

    private fun integer(
        root: tools.jackson.databind.JsonNode,
        field: String,
    ): Int {
        val node = requireNotNull(root.get(field)) { "auto-activation policy field is missing" }
        require(node.isIntegralNumber)
        return node.intValue()
    }

    internal data class AutoActivationPolicy(
        val projectId: String,
        val operator: String,
        val dailyGenerateCallCap: Int,
        val inputTokenCap: Int,
        val outputTokenCap: Int,
        val inputByteCap: Int,
        val costCapMicrousd: Long,
        val inputMicrousdPerToken: Long,
        val outputMicrousdPerToken: Long,
        val serviceAccountSecurityEvidenceSha256: String,
        val dataGovernanceStateEvidenceSha256: String,
        val abuseMonitoringStateEvidenceSha256: String,
        val modelAvailabilityEvidenceSha256: String,
    )

    private companion object {
        val LOGGER: Logger = LoggerFactory.getLogger(PreS5VertexAutoActivationAuthor::class.java)
        const val CONTROL_DIRECTORY = "control"
        const val PACKET_FILE = "pre-s5-vertex-activation.json"
        const val POLICY_CONTRACT_ID = "pre-s5-vertex-auto-activation-policy/v1"
        const val INPUT_TOKEN_SAFETY_MARGIN = 512
        val PACKET_LIFETIME: Duration = Duration.ofMinutes(4)
        val SHA256 = Regex("^[0-9a-f]{64}$")
        val DIRECTORY_PERMISSIONS =
            setOf(
                java.nio.file.attribute.PosixFilePermission.OWNER_READ,
                java.nio.file.attribute.PosixFilePermission.OWNER_WRITE,
                java.nio.file.attribute.PosixFilePermission.OWNER_EXECUTE,
            )
        val FILE_PERMISSIONS =
            setOf(
                java.nio.file.attribute.PosixFilePermission.OWNER_READ,
                java.nio.file.attribute.PosixFilePermission.OWNER_WRITE,
            )
    }
}
