package com.capstone.decision.infrastructure.security

import org.springframework.security.crypto.password.PasswordEncoder
import java.io.ByteArrayOutputStream
import java.io.DataOutputStream
import java.nio.CharBuffer
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.util.Base64
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

// bundle은 BCrypt hash와 평문 동등성 증거를 identity/version에 결속해 bootstrap과 rotation의 해석 차이를 막는다.
object DemoCredentialBundlePolicy {
    fun prepare(
        identity: DemoAccountIdentity,
        password: CharArray,
        separationKey: ByteArray,
        passwordEncoder: PasswordEncoder,
    ): String {
        requireApprovedIdentity(identity)
        requireKey(separationKey)
        val passwordBytes = encodePassword(password)
        return try {
            require(passwordBytes.size in MIN_PASSWORD_BYTES..MAX_BCRYPT_PASSWORD_BYTES) {
                "Demo credential must encode to 12..72 UTF-8 bytes."
            }
            val passwordHash =
                DemoCredentialHashPolicy.requireValid(
                    requireNotNull(passwordEncoder.encode(CharBuffer.wrap(password))),
                )
            val reuseTag = hmac(separationKey, REUSE_TAG_DOMAIN, passwordBytes)
            val bundleMac = bundleMac(separationKey, BUNDLE_VERSION, identity, reuseTag, passwordHash)
            try {
                listOf(
                    BUNDLE_VERSION,
                    identity.userId,
                    encodeEvidence(reuseTag),
                    passwordHash,
                    encodeEvidence(bundleMac),
                ).joinToString(BUNDLE_SEPARATOR)
            } finally {
                reuseTag.fill(0)
                bundleMac.fill(0)
            }
        } finally {
            passwordBytes.fill(0)
        }
    }

    fun verify(
        serialized: String,
        expectedIdentity: DemoAccountIdentity,
        separationKey: ByteArray,
    ): VerifiedDemoCredentialBundle {
        requireApprovedIdentity(expectedIdentity)
        requireKey(separationKey)
        require(serialized.length in 1..MAX_SERIALIZED_LENGTH && serialized.none(Char::isWhitespace)) {
            "Demo credential bundle has an invalid shape."
        }
        val fields = serialized.split(BUNDLE_SEPARATOR)
        require(fields.size == BUNDLE_FIELD_COUNT) { "Demo credential bundle has an invalid field count." }
        val version = fields[0]
        val userId = fields[1]
        val reuseTag = decodeEvidence(fields[2])
        val passwordHash = DemoCredentialHashPolicy.requireValid(fields[3])
        val suppliedMac = decodeEvidence(fields[4])
        try {
            require(version == BUNDLE_VERSION) { "Demo credential bundle version is not supported." }
            require(userId == expectedIdentity.userId) { "Demo credential bundle identity is not approved." }
            return verifyStored(
                identity = expectedIdentity,
                passwordHash = passwordHash,
                reuseTag = reuseTag,
                bundleMac = suppliedMac,
                policyVersion = POLICY_VERSION,
                separationKey = separationKey,
            )
        } finally {
            reuseTag.fill(0)
            suppliedMac.fill(0)
        }
    }

    fun verifyStored(
        identity: DemoAccountIdentity,
        passwordHash: String,
        reuseTag: ByteArray,
        bundleMac: ByteArray,
        policyVersion: Int,
        separationKey: ByteArray,
    ): VerifiedDemoCredentialBundle {
        requireApprovedIdentity(identity)
        requireKey(separationKey)
        require(policyVersion == POLICY_VERSION) { "Demo credential policy version is not supported." }
        require(reuseTag.size == EVIDENCE_BYTES && bundleMac.size == EVIDENCE_BYTES) {
            "Demo credential evidence has an invalid length."
        }
        DemoCredentialHashPolicy.requireValid(passwordHash)
        val expectedMac = bundleMac(separationKey, BUNDLE_VERSION, identity, reuseTag, passwordHash)
        try {
            require(MessageDigest.isEqual(expectedMac, bundleMac)) {
                "Demo credential bundle attestation is invalid."
            }
        } finally {
            expectedMac.fill(0)
        }
        return VerifiedDemoCredentialBundle(
            identity = identity,
            passwordHash = passwordHash,
            reuseTag = reuseTag,
            bundleMac = bundleMac,
            policyVersion = policyVersion,
        )
    }

    fun requireSeparated(
        userBundle: VerifiedDemoCredentialBundle,
        adminBundle: VerifiedDemoCredentialBundle,
    ) {
        require(userBundle.identity == DemoAccounts.byUserId(USER_ID)) { "Demo USER bundle identity is invalid." }
        require(adminBundle.identity == DemoAccounts.byUserId(ADMIN_ID)) { "Demo ADMIN bundle identity is invalid." }
        require(!MessageDigest.isEqual(userBundle.reuseTagInternal(), adminBundle.reuseTagInternal())) {
            "Demo USER and ADMIN credentials must not reuse one plaintext."
        }
        require(userBundle.passwordHash != adminBundle.passwordHash) {
            "Demo USER and ADMIN credential hashes must be different."
        }
    }

    fun decodeSeparationKey(encoded: String): ByteArray {
        require(BASE64URL_EVIDENCE.matches(encoded)) {
            "Demo credential separation key must be canonical unpadded Base64url."
        }
        val decoded =
            runCatching { Base64.getUrlDecoder().decode(encoded) }
                .getOrElse { throw IllegalArgumentException("Demo credential separation key is invalid.") }
        require(decoded.size == EVIDENCE_BYTES && encodeEvidence(decoded) == encoded) {
            decoded.fill(0)
            "Demo credential separation key must decode to exactly 32 bytes."
        }
        return decoded
    }

    private fun encodePassword(password: CharArray): ByteArray {
        val encoded = StandardCharsets.UTF_8.encode(CharBuffer.wrap(password))
        return try {
            ByteArray(encoded.remaining()).also(encoded::get)
        } finally {
            if (encoded.hasArray()) encoded.array().fill(0)
        }
    }

    private fun requireApprovedIdentity(identity: DemoAccountIdentity) {
        require(DemoAccounts.byUserId(identity.userId) == identity) { "Demo credential identity is not allowlisted." }
    }

    private fun requireKey(key: ByteArray) {
        require(key.size == EVIDENCE_BYTES) { "Demo credential separation key must contain exactly 32 bytes." }
    }

    private fun decodeEvidence(encoded: String): ByteArray {
        require(BASE64URL_EVIDENCE.matches(encoded)) { "Demo credential evidence is not canonical Base64url." }
        val decoded =
            runCatching { Base64.getUrlDecoder().decode(encoded) }
                .getOrElse { throw IllegalArgumentException("Demo credential evidence is invalid.") }
        require(decoded.size == EVIDENCE_BYTES && encodeEvidence(decoded) == encoded) {
            decoded.fill(0)
            "Demo credential evidence must contain exactly 32 bytes."
        }
        return decoded
    }

    private fun encodeEvidence(value: ByteArray): String = Base64.getUrlEncoder().withoutPadding().encodeToString(value)

    private fun bundleMac(
        key: ByteArray,
        version: String,
        identity: DemoAccountIdentity,
        reuseTag: ByteArray,
        passwordHash: String,
    ): ByteArray =
        hmac(
            key,
            BUNDLE_MAC_DOMAIN,
            version.toByteArray(StandardCharsets.UTF_8),
            identity.userId.toByteArray(StandardCharsets.UTF_8),
            identity.username.toByteArray(StandardCharsets.UTF_8),
            identity.role.name.toByteArray(StandardCharsets.UTF_8),
            reuseTag,
            passwordHash.toByteArray(StandardCharsets.UTF_8),
        )

    private fun hmac(
        key: ByteArray,
        domain: ByteArray,
        vararg fields: ByteArray,
    ): ByteArray {
        val payload = frame(domain, *fields)
        return try {
            Mac.getInstance(HMAC_ALGORITHM).run {
                init(SecretKeySpec(key, HMAC_ALGORITHM))
                doFinal(payload)
            }
        } finally {
            payload.fill(0)
        }
    }

    private fun frame(
        domain: ByteArray,
        vararg fields: ByteArray,
    ): ByteArray =
        ByteArrayOutputStream().use { output ->
            DataOutputStream(output).use { data ->
                (arrayOf(domain) + fields).forEach { field ->
                    data.writeInt(field.size)
                    data.write(field)
                }
            }
            output.toByteArray()
        }

    private const val BUNDLE_VERSION = "s21-v1"
    private const val POLICY_VERSION = 1
    private const val BUNDLE_FIELD_COUNT = 5
    private const val BUNDLE_SEPARATOR = ":"
    private const val EVIDENCE_BYTES = 32
    private const val MIN_PASSWORD_BYTES = 12
    private const val MAX_BCRYPT_PASSWORD_BYTES = 72
    private const val MAX_SERIALIZED_LENGTH = 256
    private const val USER_ID = "usr_demo_user"
    private const val ADMIN_ID = "usr_demo_admin"
    private const val HMAC_ALGORITHM = "HmacSHA256"
    private val BASE64URL_EVIDENCE = Regex("^[A-Za-z0-9_-]{43}$")
    private val REUSE_TAG_DOMAIN = "capstone:s21:demo-credential-reuse:v1".toByteArray(StandardCharsets.UTF_8)
    private val BUNDLE_MAC_DOMAIN = "capstone:s21:demo-credential-bundle:v1".toByteArray(StandardCharsets.UTF_8)
}

// 검증 완료 객체만 migration과 rotation에 전달해 hash와 증거가 따로 해석되는 경로를 만들지 않는다.
class VerifiedDemoCredentialBundle internal constructor(
    val identity: DemoAccountIdentity,
    val passwordHash: String,
    reuseTag: ByteArray,
    bundleMac: ByteArray,
    val policyVersion: Int,
) {
    private val verifiedReuseTag = reuseTag.copyOf()
    private val verifiedBundleMac = bundleMac.copyOf()

    val reuseTag: ByteArray
        get() = verifiedReuseTag.copyOf()

    val bundleMac: ByteArray
        get() = verifiedBundleMac.copyOf()

    internal fun reuseTagInternal(): ByteArray = verifiedReuseTag

    internal fun bundleMacInternal(): ByteArray = verifiedBundleMac

    internal fun clearEvidence() {
        verifiedReuseTag.fill(0)
        verifiedBundleMac.fill(0)
    }
}
