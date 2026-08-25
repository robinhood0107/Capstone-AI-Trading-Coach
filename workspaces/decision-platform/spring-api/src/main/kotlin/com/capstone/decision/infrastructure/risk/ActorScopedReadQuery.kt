package com.capstone.decision.infrastructure.risk

import com.capstone.decision.domain.risk.EvaluationBounds
import com.capstone.decision.infrastructure.security.ActorCapabilityBinding
import com.capstone.decision.infrastructure.security.ActorCapabilityRolePolicy
import com.capstone.decision.infrastructure.security.ActorRlsScope
import org.springframework.beans.factory.ObjectProvider
import org.springframework.stereotype.Component
import org.springframework.transaction.support.TransactionSynchronizationManager
import java.sql.PreparedStatement
import java.sql.ResultSet
import javax.sql.DataSource

/**
 * owner projection의 custom GUC를 한 read-only connection transaction에만 가둔다.
 * Decision persistence transaction 안에서 호출되면 즉시 실패해 source read/write TX 경계를 보존한다.
 */
@Component
class ActorScopedReadQuery(
    private val dataSourceProvider: ObjectProvider<DataSource>,
    private val actorRlsScope: ActorRlsScope,
) {
    fun <T> query(
        actorUserId: String,
        sql: String,
        requestedDecisionId: String? = null,
        requestedOrderId: String? = null,
        binder: (PreparedStatement) -> Unit = {},
        mapper: (ResultSet) -> T,
    ): List<T> {
        require(actorUserId.isNotBlank() && actorUserId.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        require(
            requestedDecisionId == null ||
                (
                    requestedDecisionId.isNotBlank() &&
                        requestedDecisionId.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS
                ),
        )
        require(
            requestedOrderId == null ||
                (
                    requestedOrderId.isNotBlank() &&
                        requestedOrderId.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS
                ),
        )
        check(!TransactionSynchronizationManager.isActualTransactionActive()) {
            "Stored source read cannot join the Decision persistence transaction."
        }
        val dataSource =
            dataSourceProvider.getIfAvailable()
                ?: error("Actor-scoped JDBC access is unavailable without a configured DataSource.")
        dataSource.connection.use { connection ->
            check(connection.autoCommit) {
                "Actor-scoped source connection must begin outside an existing transaction."
            }
            try {
                // Capability consumption is an authorization-ledger write; business SQL below remains SELECT-only.
                connection.isReadOnly = false
                connection.autoCommit = false
                val targetKind =
                    when {
                        requestedDecisionId != null -> "DECISION"
                        requestedOrderId != null -> "ORDER"
                        else -> "OWNER"
                    }
                val targetId = requestedDecisionId ?: requestedOrderId ?: actorUserId
                actorRlsScope.open(
                    connection,
                    actorUserId,
                    ActorCapabilityBinding.request(
                        "READ_STORED_SOURCE",
                        targetKind,
                        targetId,
                        ActorCapabilityRolePolicy.OWNER,
                        actorUserId,
                        requestedDecisionId,
                        requestedOrderId,
                    ),
                )
                connection
                    .prepareStatement("SELECT set_config('statement_timeout', '500ms', true)")
                    .use { statement ->
                        statement.executeQuery().use { result -> check(result.next()) }
                    }
                if (requestedDecisionId != null) {
                    connection
                        .prepareStatement("SELECT set_config('app.requested_decision_id', ?, true)")
                        .use { statement ->
                            statement.setString(1, requestedDecisionId)
                            statement.executeQuery().use { result -> check(result.next()) }
                        }
                }
                if (requestedOrderId != null) {
                    connection
                        .prepareStatement("SELECT set_config('app.requested_order_id', ?, true)")
                        .use { statement ->
                            statement.setString(1, requestedOrderId)
                            statement.executeQuery().use { result -> check(result.next()) }
                        }
                }
                val rows =
                    connection.prepareStatement(sql).use { statement ->
                        binder(statement)
                        statement.executeQuery().use { result ->
                            buildList {
                                while (result.next()) {
                                    add(mapper(result))
                                }
                            }
                        }
                    }
                connection.commit()
                return rows
            } catch (exception: Exception) {
                runCatching { connection.rollback() }
                throw exception
            } finally {
                runCatching { connection.isReadOnly = false }
                runCatching { connection.autoCommit = true }
            }
        }
    }
}
