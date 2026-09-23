package com.capstone.decision.infrastructure.security

import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder
import java.security.SecureRandom
import java.util.Base64

/**
 * Historical V7 needs two attested password rows while building a fresh schema.
 * Public products generate throwaway credentials only in migration memory. No
 * password is published, mounted in the serving app, or accepted by a login API.
 */
internal object PublicActorTrustMigrationFactory {
    fun create(): V7__s2_1_actor_trust {
        val random = SecureRandom()
        val separationKey = ByteArray(32).also(random::nextBytes)
        val encoder = BCryptPasswordEncoder(12)
        return try {
            val user = issue(requireNotNull(DemoAccounts.byUserId("usr_demo_user")), random, separationKey, encoder)
            val admin = issue(requireNotNull(DemoAccounts.byUserId("usr_demo_admin")), random, separationKey, encoder)
            DemoCredentialBundlePolicy.requireSeparated(user, admin)
            V7__s2_1_actor_trust(user, admin)
        } finally {
            separationKey.fill(0)
        }
    }

    private fun issue(
        identity: DemoAccountIdentity,
        random: SecureRandom,
        separationKey: ByteArray,
        encoder: BCryptPasswordEncoder,
    ): VerifiedDemoCredentialBundle {
        val bytes = ByteArray(32).also(random::nextBytes)
        val encoded = Base64.getUrlEncoder().withoutPadding().encode(bytes)
        val password = CharArray(encoded.size) { index -> encoded[index].toInt().toChar() }
        return try {
            val bundle = DemoCredentialBundlePolicy.prepare(identity, password, separationKey, encoder)
            DemoCredentialBundlePolicy.verify(bundle, identity, separationKey)
        } finally {
            password.fill('\u0000')
            encoded.fill(0)
            bytes.fill(0)
        }
    }
}
