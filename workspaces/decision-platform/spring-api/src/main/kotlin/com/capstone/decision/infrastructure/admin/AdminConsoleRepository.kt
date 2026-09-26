package com.capstone.decision.infrastructure.admin

import com.capstone.decision.infrastructure.security.AuthDatabase
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Repository
import java.sql.ResultSet
import java.time.OffsetDateTime

/** Admin console reads and writes go through V214 definer functions that re-check the ADMIN actor. */
@Repository
class AdminConsoleRepository(
    private val authDatabaseProvider: ObjectProvider<AuthDatabase>,
) {
    fun listUsers(
        actorUserId: String,
        search: String,
        limit: Int,
        offset: Int,
    ): AdminUserPage {
        var total = 0L
        val rows =
            jdbc().query(
                "select * from admin_list_users_v1(?,?,?,?)",
                { row, _ ->
                    total = row.getLong("total_count")
                    AdminUserRow(
                        userId = row.getString("user_id"),
                        username = row.getString("username"),
                        role = row.getString("role"),
                        status = row.getString("status"),
                        createdAt = row.offsetDateTime("created_at"),
                        email = row.getString("email"),
                        providers = (row.getArray("providers")?.array as? Array<*>)?.map { it.toString() } ?: emptyList(),
                        brokerState = row.getString("broker_state"),
                        automationState = row.getString("automation_state"),
                    )
                },
                actorUserId,
                search,
                limit,
                offset,
            )
        return AdminUserPage(rows, total)
    }

    fun setUserAccess(
        actorUserId: String,
        targetUserId: String,
        role: String,
        status: String,
    ): AdminUserAccess =
        jdbc()
            .query(
                "select * from admin_set_user_access_v1(?,?,?,?)",
                { row, _ ->
                    AdminUserAccess(
                        userId = row.getString("user_id"),
                        role = row.getString("role"),
                        status = row.getString("status"),
                    )
                },
                actorUserId,
                targetUserId,
                role,
                status,
            ).single()

    fun listAutomation(actorUserId: String): List<AdminAutomationRow> =
        jdbc().query(
            "select * from admin_list_automation_v1(?)",
            { row, _ ->
                AdminAutomationRow(
                    userId = row.getString("user_id"),
                    username = row.getString("username"),
                    controlState = row.getString("control_state"),
                    accountId = row.getString("account_id"),
                    controlUpdatedAt = row.offsetDateTime("control_updated_at"),
                    todayClaimState = row.getString("today_claim_state"),
                    todayRunId = row.getString("today_run_id"),
                )
            },
            actorUserId,
        )

    fun readLimits(actorUserId: String): AdminLimits =
        jdbc()
            .query(
                "select * from admin_read_limits_v1(?)",
                { row, _ ->
                    AdminLimits(
                        signupCap = row.getObject("signup_cap", Integer::class.java)?.toInt(),
                        automationActiveCap = row.getInt("automation_active_cap"),
                        updatedBy = row.getString("updated_by"),
                        updatedAt = row.offsetDateTime("updated_at"),
                        userCount = row.getLong("user_count"),
                        activeUserCount = row.getLong("active_user_count"),
                        armedCount = row.getLong("armed_count"),
                    )
                },
                actorUserId,
            ).single()

    fun updateLimits(
        actorUserId: String,
        signupCap: Int?,
        automationActiveCap: Int,
    ) {
        jdbc().query("select admin_update_limits_v1(?,?,?)", { _, _ -> }, actorUserId, signupCap, automationActiveCap)
    }

    private fun jdbc(): JdbcTemplate =
        JdbcTemplate(
            authDatabaseProvider.ifAvailable?.dataSource
                ?: throw IllegalStateException("Admin console database is unavailable."),
        )

    private fun ResultSet.offsetDateTime(column: String): OffsetDateTime? = getObject(column, OffsetDateTime::class.java)
}

data class AdminUserPage(
    val items: List<AdminUserRow>,
    val total: Long,
)

data class AdminUserRow(
    val userId: String,
    val username: String,
    val role: String,
    val status: String,
    val createdAt: OffsetDateTime?,
    val email: String?,
    val providers: List<String>,
    val brokerState: String?,
    val automationState: String?,
)

data class AdminUserAccess(
    val userId: String,
    val role: String,
    val status: String,
)

data class AdminAutomationRow(
    val userId: String,
    val username: String,
    val controlState: String,
    val accountId: String,
    val controlUpdatedAt: OffsetDateTime?,
    val todayClaimState: String?,
    val todayRunId: String?,
)

data class AdminLimits(
    val signupCap: Int?,
    val automationActiveCap: Int,
    val updatedBy: String?,
    val updatedAt: OffsetDateTime?,
    val userCount: Long,
    val activeUserCount: Long,
    val armedCount: Long,
)
