package com.capstone.decision.infrastructure.security

import org.flywaydb.core.api.migration.BaseJavaMigration
import org.flywaydb.core.api.migration.Context
import java.security.MessageDigest
import java.sql.Connection

// 검증된 bundle만 bind parameter로 전달해 hash와 평문 분리 증거를 하나의 S2.1 trust root로 설치한다.
@Suppress("ktlint:standard:class-naming")
class V7__s2_1_actor_trust(
    private val userBundle: VerifiedDemoCredentialBundle,
    private val adminBundle: VerifiedDemoCredentialBundle,
) : BaseJavaMigration() {
    override fun getChecksum(): Int = MIGRATION_CHECKSUM

    override fun migrate(context: Context) {
        DemoCredentialBundlePolicy.requireSeparated(userBundle, adminBundle)

        val connection = context.connection
        PostgreSqlCredentialLoggingPolicy.requireSafe(connection)
        connection.createStatement().use { statement ->
            statement.execute(
                """
                alter table users
                    add column security_version bigint not null default 1,
                    add column credential_reuse_tag bytea,
                    add column credential_bundle_mac bytea,
                    add column credential_policy_version smallint,
                    add constraint users_security_version_positive_check check (security_version > 0),
                    add constraint users_credential_evidence_shape_check check (
                        (
                            credential_reuse_tag is null and
                            credential_bundle_mac is null and
                            credential_policy_version is null
                        ) or (
                            credential_reuse_tag is not null and
                            octet_length(credential_reuse_tag) = 32 and
                            credential_bundle_mac is not null and
                            octet_length(credential_bundle_mac) = 32 and
                            credential_policy_version = 1
                        )
                    )
                """.trimIndent(),
            )
        }

        val candidates = lockTrustRootCandidates(connection)
        ensureIdentity(connection, candidates, userBundle)
        ensureIdentity(connection, candidates, adminBundle)
        requireDemoEvidence(connection)
        applyRuntimePrivileges(connection)
    }

    private fun lockTrustRootCandidates(connection: Connection): List<ExistingUser> =
        connection
            .prepareStatement(
                """
                select user_id, username, password_hash, role, status, security_version,
                       credential_reuse_tag, credential_bundle_mac, credential_policy_version
                from users
                where user_id in (?, ?) or username in (?, ?)
                for update
                """.trimIndent(),
            ).use { statement ->
                statement.setString(1, DemoAccounts.identities[0].userId)
                statement.setString(2, DemoAccounts.identities[1].userId)
                statement.setString(3, DemoAccounts.identities[0].username)
                statement.setString(4, DemoAccounts.identities[1].username)
                statement.executeQuery().use { result ->
                    buildList {
                        while (result.next()) {
                            add(
                                ExistingUser(
                                    userId = result.getString("user_id"),
                                    username = result.getString("username"),
                                    passwordHash = result.getString("password_hash"),
                                    role = result.getString("role"),
                                    status = result.getString("status"),
                                    securityVersion = result.getLong("security_version"),
                                    reuseTag = result.getBytes("credential_reuse_tag"),
                                    bundleMac = result.getBytes("credential_bundle_mac"),
                                    policyVersion =
                                        result
                                            .getObject("credential_policy_version")
                                            ?.let { (it as Number).toInt() },
                                ),
                            )
                        }
                    }
                }
            }

    private fun ensureIdentity(
        connection: Connection,
        candidates: List<ExistingUser>,
        bundle: VerifiedDemoCredentialBundle,
    ) {
        val identity = bundle.identity
        val matching = candidates.filter { it.userId == identity.userId || it.username == identity.username }
        if (matching.isNotEmpty()) {
            val existing = matching.singleOrNull()
            check(existing != null && existing.matchesBase(bundle)) {
                "Demo identity conflicts with the approved S2.1 trust root."
            }
            when {
                existing.hasNoEvidence() -> bindEvidence(connection, bundle)
                existing.matchesEvidence(bundle) -> Unit
                else -> error("Demo credential evidence conflicts with the approved S2.1 trust root.")
            }
            return
        }

        val reuseTag = bundle.reuseTag
        val bundleMac = bundle.bundleMac
        try {
            connection
                .prepareStatement(
                    """
                    insert into users (
                        user_id, username, role, password_hash, status, security_version,
                        credential_reuse_tag, credential_bundle_mac, credential_policy_version
                    )
                    values (?, ?, ?, ?, 'ACTIVE', 1, ?, ?, ?)
                    """.trimIndent(),
                ).use { statement ->
                    statement.setString(1, identity.userId)
                    statement.setString(2, identity.username)
                    statement.setString(3, identity.role.name)
                    statement.setString(4, bundle.passwordHash)
                    statement.setBytes(5, reuseTag)
                    statement.setBytes(6, bundleMac)
                    statement.setInt(7, bundle.policyVersion)
                    check(statement.executeUpdate() == 1) { "Demo identity seed did not affect exactly one row." }
                }
        } finally {
            reuseTag.fill(0)
            bundleMac.fill(0)
        }
    }

    private fun bindEvidence(
        connection: Connection,
        bundle: VerifiedDemoCredentialBundle,
    ) {
        val reuseTag = bundle.reuseTag
        val bundleMac = bundle.bundleMac
        try {
            connection
                .prepareStatement(
                    """
                    update users
                    set credential_reuse_tag = ?, credential_bundle_mac = ?, credential_policy_version = ?
                    where user_id = ? and username = ? and role = ? and password_hash = ?
                      and status = 'ACTIVE' and security_version = 1
                      and credential_reuse_tag is null and credential_bundle_mac is null
                      and credential_policy_version is null
                    """.trimIndent(),
                ).use { statement ->
                    statement.setBytes(1, reuseTag)
                    statement.setBytes(2, bundleMac)
                    statement.setInt(3, bundle.policyVersion)
                    statement.setString(4, bundle.identity.userId)
                    statement.setString(5, bundle.identity.username)
                    statement.setString(6, bundle.identity.role.name)
                    statement.setString(7, bundle.passwordHash)
                    check(statement.executeUpdate() == 1) {
                        "Demo credential evidence did not bind to exactly one approved row."
                    }
                }
        } finally {
            reuseTag.fill(0)
            bundleMac.fill(0)
        }
    }

    private fun requireDemoEvidence(connection: Connection) {
        connection.createStatement().use { statement ->
            statement.execute(
                """
                alter table users
                    add constraint users_demo_credential_evidence_required_check check (
                        user_id not in ('usr_demo_user', 'usr_demo_admin') or (
                            credential_reuse_tag is not null and
                            credential_bundle_mac is not null and
                            credential_policy_version = 1
                        )
                    )
                """.trimIndent(),
            )
        }
    }

    private fun applyRuntimePrivileges(connection: Connection) {
        val runtimeRoleExists =
            connection.prepareStatement("select 1 from pg_roles where rolname = 'decision_app'").use { statement ->
                statement.executeQuery().use { it.next() }
            }
        if (!runtimeRoleExists) return

        connection.createStatement().use { statement ->
            // runtime은 인증 재검증용 SELECT만 가지며 credential/status/version 변경은 operator role에 남긴다.
            statement.execute("revoke insert, update, delete, truncate on table users from decision_app")
            statement.execute("grant select on table users to decision_app")
        }
    }

    private data class ExistingUser(
        val userId: String,
        val username: String,
        val passwordHash: String,
        val role: String,
        val status: String,
        val securityVersion: Long,
        val reuseTag: ByteArray?,
        val bundleMac: ByteArray?,
        val policyVersion: Int?,
    ) {
        fun matchesBase(bundle: VerifiedDemoCredentialBundle): Boolean =
            userId == bundle.identity.userId &&
                username == bundle.identity.username &&
                passwordHash == bundle.passwordHash &&
                role == bundle.identity.role.name &&
                status == ACTIVE_STATUS &&
                securityVersion == 1L

        fun hasNoEvidence(): Boolean = reuseTag == null && bundleMac == null && policyVersion == null

        fun matchesEvidence(bundle: VerifiedDemoCredentialBundle): Boolean {
            val expectedTag = bundle.reuseTag
            val expectedMac = bundle.bundleMac
            return try {
                reuseTag != null &&
                    bundleMac != null &&
                    policyVersion == bundle.policyVersion &&
                    MessageDigest.isEqual(reuseTag, expectedTag) &&
                    MessageDigest.isEqual(bundleMac, expectedMac)
            } finally {
                expectedTag.fill(0)
                expectedMac.fill(0)
            }
        }
    }

    companion object {
        // Java migration은 기본 checksum이 없으므로 source 변경 시 함께 갱신하는 고정 검증값을 둔다.
        private const val MIGRATION_CHECKSUM = 0x52100009
        private const val ACTIVE_STATUS = "ACTIVE"
    }
}
