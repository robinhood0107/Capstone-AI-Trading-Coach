package com.capstone.decision.application.security

import java.security.MessageDigest
import java.util.HexFormat

/**
 * Owner write 요청의 raw idempotency key를 즉시 목적 분리 hash로 축약한다.
 * 반환값만 persistence로 전달하며 원문 key는 로그·metric·DB에 전달하지 않는다.
 */
object OwnerWriteHashes {
    fun scope(
        ownerUserId: String,
        rawKey: String,
    ): String = digest(canonical("OWNER_WRITE_SCOPE_V1", ownerUserId, rawKey))

    fun request(
        operation: String,
        vararg values: String?,
    ): String = digest(canonical("OWNER_WRITE_REQUEST_V1", operation, *values))

    fun ownerScope(ownerUserId: String): String = digest(ownerUserId).removePrefix("sha256:")

    private fun canonical(vararg values: String?): String =
        values.joinToString(separator = "") { value ->
            if (value == null) {
                "-:\n"
            } else {
                "${value.toByteArray(Charsets.UTF_8).size}:$value\n"
            }
        }

    private fun digest(value: String): String =
        "sha256:" +
            HexFormat
                .of()
                .formatHex(MessageDigest.getInstance("SHA-256").digest(value.toByteArray(Charsets.UTF_8)))
}
