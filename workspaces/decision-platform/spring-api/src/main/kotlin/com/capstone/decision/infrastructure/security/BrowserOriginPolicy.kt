package com.capstone.decision.infrastructure.security

import java.net.URI

/** Treat the two IPv4 loopback spellings as the same local FULL browser origin. */
object BrowserOriginPolicy {
    fun allows(
        configuredOrigin: String,
        requestOrigin: String?,
    ): Boolean {
        if (configuredOrigin.isBlank() || requestOrigin == null) return false
        if (requestOrigin == configuredOrigin) return true
        val configured = runCatching { URI.create(configuredOrigin) }.getOrNull() ?: return false
        if (configured.scheme != "http" ||
            configured.host !in setOf("localhost", "127.0.0.1") ||
            configured.port !in 1..65535 ||
            !configured.path.isNullOrEmpty() ||
            configured.rawQuery != null ||
            configured.rawFragment != null ||
            configured.userInfo != null
        ) {
            return false
        }
        return requestOrigin == "http://localhost:${configured.port}" ||
            requestOrigin == "http://127.0.0.1:${configured.port}"
    }
}
