package com.capstone.decision.infrastructure.security

import org.flywaydb.core.api.callback.BaseCallback
import org.flywaydb.core.api.callback.Context
import org.flywaydb.core.api.callback.Event
import org.springframework.stereotype.Component

/** B86 pg_dump 기준선이 끈 RLS를 다음 버전 마이그레이션 전에 복구한다. */
@Component
class P1FlywayRlsResetCallback : BaseCallback() {
    override fun supports(
        event: Event,
        context: Context?,
    ): Boolean = event == Event.BEFORE_EACH_MIGRATE

    override fun handle(
        event: Event,
        context: Context,
    ) {
        context.connection.createStatement().use { statement ->
            statement.execute("SET row_security = on")
        }
    }
}
