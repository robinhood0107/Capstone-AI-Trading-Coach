package com.capstone.decision.api.brokerage

import com.capstone.decision.application.brokerage.BrokerageFieldViolation
import com.capstone.decision.application.brokerage.BrokerageValidationException
import jakarta.servlet.http.HttpServletRequest
import org.springframework.stereotype.Component
import java.time.DateTimeException
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.temporal.ChronoUnit

data class FillDateQuery(
    val fromInclusive: Instant,
    val toExclusive: Instant,
    val cursor: String?,
)

/**
 * fill 조회 날짜는 KST 일 경계로 고정하고 inclusive from/to 기간을 최대 31일로 제한한다.
 * cursor 외 offset/size/provider query는 unknown field로 거부한다.
 */
@Component
class BrokerageFillRequestParser {
    fun parse(request: HttpServletRequest): FillDateQuery {
        val violations =
            request.parameterMap.keys
                .filterNot(FIELDS::contains)
                .map { name -> BrokerageFieldViolation("/query/${escapePointer(name)}", "UNKNOWN_FIELD") }
                .toMutableList()
        val from = parseDate(single(request, "from", violations), "from", violations)
        val to = parseDate(single(request, "to", violations), "to", violations)
        val cursor = optionalCursor(request, violations)
        if (from != null && to != null) {
            val days = ChronoUnit.DAYS.between(from, to) + 1
            if (days !in 1..MAX_RANGE_DAYS) {
                violations.add(BrokerageFieldViolation("/query/to", "RANGE_EXCEEDS_31_DAYS"))
            }
        }
        throwIfInvalid(violations)
        return FillDateQuery(
            fromInclusive = requireNotNull(from).atStartOfDay(KST).toInstant(),
            toExclusive = requireNotNull(to).plusDays(1).atStartOfDay(KST).toInstant(),
            cursor = cursor,
        )
    }

    private fun single(
        request: HttpServletRequest,
        name: String,
        violations: MutableList<BrokerageFieldViolation>,
    ): String? {
        val values = request.parameterMap[name]
        if (values == null || values.size != 1 || values.single().isBlank()) {
            violations.add(BrokerageFieldViolation("/query/$name", "REQUIRED"))
            return null
        }
        return values.single()
    }

    private fun parseDate(
        value: String?,
        name: String,
        violations: MutableList<BrokerageFieldViolation>,
    ): LocalDate? {
        if (value == null) {
            return null
        }
        return try {
            LocalDate.parse(value).takeIf { it.toString() == value }
                ?: throw DateTimeException("Non-canonical date")
        } catch (_: DateTimeException) {
            violations.add(BrokerageFieldViolation("/query/$name", "INVALID_DATE"))
            null
        }
    }

    private fun optionalCursor(
        request: HttpServletRequest,
        violations: MutableList<BrokerageFieldViolation>,
    ): String? {
        val values = request.parameterMap["cursor"] ?: return null
        if (values.size != 1 || values.single().isBlank() || values.single().length > MAX_CURSOR_CHARS) {
            violations.add(BrokerageFieldViolation("/query/cursor", "INVALID_FORMAT"))
            return null
        }
        return values.single()
    }

    private fun throwIfInvalid(violations: List<BrokerageFieldViolation>) {
        if (violations.isNotEmpty()) {
            throw BrokerageValidationException(violations)
        }
    }

    private fun escapePointer(value: String): String = value.replace("~", "~0").replace("/", "~1")

    private companion object {
        const val MAX_RANGE_DAYS = 31L
        const val MAX_CURSOR_CHARS = 1024
        val FIELDS = setOf("from", "to", "cursor")
        val KST: ZoneId = ZoneId.of("Asia/Seoul")
    }
}
