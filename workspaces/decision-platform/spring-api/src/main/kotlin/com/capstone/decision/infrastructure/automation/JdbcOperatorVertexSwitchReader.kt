package com.capstone.decision.infrastructure.automation

import com.capstone.decision.application.automation.OperatorVertexSwitchReader
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Component

/** 관리자 콘솔 스위치를 앱 DB 역할로 읽는다. 값은 불리언 하나뿐이다(V216). */
@Component
class JdbcOperatorVertexSwitchReader(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
) : OperatorVertexSwitchReader {
    override fun enabled(): Boolean {
        val jdbc = jdbcProvider.getIfAvailable() ?: return false
        return jdbc.queryForObject(
            "SELECT public.p1_operator_vertex_fallback_enabled_v1()",
            emptyMap<String, Any>(),
            Boolean::class.java,
        ) == true
    }
}
