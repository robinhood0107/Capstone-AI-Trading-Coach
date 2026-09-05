package com.capstone.decision.api.instrument

import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.security.AppPrincipal
import io.swagger.v3.oas.annotations.Hidden
import jakarta.servlet.http.HttpServletRequest
import org.springframework.beans.factory.ObjectProvider
import org.springframework.http.MediaType
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.security.core.annotation.AuthenticationPrincipal
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

data class InstrumentDisplayItem(
    val symbol: String,
    val nameKo: String,
    val logoText: String,
    val brandColor: String,
    val market: String,
)

data class InstrumentDisplayCatalog(
    val items: List<InstrumentDisplayItem>,
)

@RestController
@Hidden
@RequestMapping("/api/v1/instruments", produces = [MediaType.APPLICATION_JSON_VALUE])
class InstrumentDisplayController(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
) {
    @GetMapping("/display")
    fun display(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): ApiResponse<InstrumentDisplayCatalog> {
        require(request.queryString == null)
        require(principal.userId.isNotBlank())
        val jdbc = jdbcProvider.getIfAvailable() ?: error("Instrument display catalog is unavailable")
        val items =
            jdbc.query(
                """
                SELECT symbol,name_ko,logo_text,brand_color,market
                FROM instrument_display_metadata ORDER BY display_order
                """.trimIndent(),
                emptyMap<String, Any>(),
            ) { row, _ ->
                InstrumentDisplayItem(
                    symbol = row.getString("symbol"),
                    nameKo = row.getString("name_ko"),
                    logoText = row.getString("logo_text"),
                    brandColor = row.getString("brand_color"),
                    market = row.getString("market"),
                )
            }
        check(items.size == 31) { "Instrument display catalog is incomplete" }
        return ApiResponseFactory.success(RequestIds.currentOrCreate(request), InstrumentDisplayCatalog(items))
    }
}
