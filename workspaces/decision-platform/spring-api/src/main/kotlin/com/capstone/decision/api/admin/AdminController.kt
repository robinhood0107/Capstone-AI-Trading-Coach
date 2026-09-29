package com.capstone.decision.api.admin

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.automation.AutomationAiProviderPolicy
import com.capstone.decision.application.security.AppPrincipal
import com.capstone.decision.infrastructure.admin.AdminAiUsageRow
import com.capstone.decision.infrastructure.admin.AdminAutomationRow
import com.capstone.decision.infrastructure.admin.AdminConsoleRepository
import com.capstone.decision.infrastructure.admin.AdminLimits
import com.capstone.decision.infrastructure.admin.AdminUserAccess
import com.capstone.decision.infrastructure.admin.AdminUserPage
import com.capstone.decision.infrastructure.vertex.OperatorVertexStatus
import com.capstone.decision.infrastructure.vertex.OperatorVertexStatusProbe
import io.swagger.v3.oas.annotations.Operation
import jakarta.servlet.http.HttpServletRequest
import jakarta.validation.Valid
import jakarta.validation.constraints.Max
import jakarta.validation.constraints.Min
import jakarta.validation.constraints.NotNull
import jakarta.validation.constraints.Pattern
import org.springframework.beans.factory.ObjectProvider
import org.springframework.dao.DataAccessException
import org.springframework.security.access.prepost.PreAuthorize
import org.springframework.security.core.annotation.AuthenticationPrincipal
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController
import java.sql.SQLException
import java.time.OffsetDateTime

/** Operator console: accounts, running automations, and service capacity. ADMIN only. */
@RestController
@RequestMapping("/api/v1/admin")
@PreAuthorize("hasRole('ADMIN')")
class AdminController(
    private val repository: AdminConsoleRepository,
    private val aiProviderPolicy: AutomationAiProviderPolicy,
    private val operatorStatus: ObjectProvider<OperatorVertexStatusProbe>,
) {
    @Operation(operationId = "adminListUsers")
    @GetMapping("/users")
    fun users(
        @AuthenticationPrincipal principal: AppPrincipal,
        @RequestParam(defaultValue = "") search: String,
        @RequestParam(defaultValue = "0") page: Int,
        @RequestParam(defaultValue = "50") size: Int,
        request: HttpServletRequest,
    ): ApiResponse<AdminUserPage> {
        if (page !in 0..10_000 || size !in 1..200 || search.length > 254) throw ApiException(ErrorCode.VALIDATION_ERROR)
        return ok(request) { repository.listUsers(principal.userId, search.trim(), size, page * size) }
    }

    @Operation(operationId = "adminSetUserAccess")
    @PutMapping("/users/{userId}/access")
    fun setAccess(
        @AuthenticationPrincipal principal: AppPrincipal,
        @PathVariable @Pattern(regexp = "^usr_[A-Za-z0-9_-]{4,96}$") userId: String,
        @Valid @RequestBody body: AdminUserAccessRequest,
        request: HttpServletRequest,
    ): ApiResponse<AdminUserAccess> {
        if (userId == principal.userId && (body.role != "ADMIN" || body.status != "ACTIVE")) {
            // 자기 자신을 강등·정지하면 콘솔 접근을 잃는다. 다른 관리자가 처리한다.
            throw ApiException(ErrorCode.CONFLICT, "Change another administrator's access instead of your own.")
        }
        return ok(request) { repository.setUserAccess(principal.userId, userId, body.role, body.status) }
    }

    @Operation(operationId = "adminListAutomation")
    @GetMapping("/automation")
    fun automation(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): ApiResponse<List<AdminAutomationRow>> = ok(request) { repository.listAutomation(principal.userId) }

    @Operation(operationId = "adminReadLimits")
    @GetMapping("/limits")
    fun limits(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): ApiResponse<AdminLimits> = ok(request) { repository.readLimits(principal.userId) }

    @Operation(operationId = "adminUpdateLimits")
    @PutMapping("/limits")
    fun updateLimits(
        @AuthenticationPrincipal principal: AppPrincipal,
        @Valid @RequestBody body: AdminLimitsRequest,
        request: HttpServletRequest,
    ): ApiResponse<AdminLimits> =
        ok(request) {
            repository.updateLimits(principal.userId, body.signupCap, body.automationActiveCap)
            repository.readLimits(principal.userId)
        }

    /**
     * 공용 Vertex 상태와 사용자별 AI 검토 사용량. 공용 서비스 계정·토큰·사용자 키는 싣지 않는다 - 사용자가
     * 자기 키를 등록했는지만 보인다.
     */
    @Operation(operationId = "adminReadAiReview")
    @GetMapping("/ai")
    fun ai(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): ApiResponse<AdminAiReview> = ok(request) { readAi(principal.userId) }

    @Operation(operationId = "adminSetOperatorVertexFallback")
    @PutMapping("/ai/operator-fallback")
    fun setOperatorFallback(
        @AuthenticationPrincipal principal: AppPrincipal,
        @Valid @RequestBody body: AdminOperatorFallbackRequest,
        request: HttpServletRequest,
    ): ApiResponse<AdminAiReview> =
        ok(request) {
            repository.setOperatorVertexSwitch(principal.userId, requireNotNull(body.enabled))
            readAi(principal.userId)
        }

    private fun readAi(actorUserId: String): AdminAiReview {
        val switch = repository.readOperatorVertexSwitch(actorUserId)
        return AdminAiReview(
            operator = operatorStatus.ifAvailable?.status() ?: OperatorVertexStatus(false, null, null, false),
            deploymentAllowsShared = aiProviderPolicy.deploymentCeiling(),
            sharedEnabled = switch.enabled,
            sharedEffective = aiProviderPolicy.deploymentCeiling() && switch.enabled,
            switchUpdatedBy = switch.updatedBy,
            switchUpdatedAt = switch.updatedAt,
            users = repository.listAiUsage(actorUserId),
        )
    }

    private fun <T> ok(
        request: HttpServletRequest,
        block: () -> T,
    ): ApiResponse<T> {
        val data =
            try {
                block()
            } catch (error: DataAccessException) {
                throw when ((error.mostSpecificCause as? SQLException)?.sqlState) {
                    "42501" -> ApiException(ErrorCode.FORBIDDEN)
                    "22023" -> ApiException(ErrorCode.VALIDATION_ERROR)
                    "23514" -> ApiException(ErrorCode.CONFLICT, "This change would leave the service without an active administrator.")
                    "P0002" -> ApiException(ErrorCode.NOT_FOUND)
                    else -> error
                }
            }
        return ApiResponseFactory.success(RequestIds.currentOrCreate(request), data)
    }
}

data class AdminUserAccessRequest(
    @field:Pattern(regexp = "USER|ADMIN")
    val role: String,
    @field:Pattern(regexp = "ACTIVE|DISABLED")
    val status: String,
)

data class AdminOperatorFallbackRequest(
    @field:NotNull
    val enabled: Boolean?,
)

data class AdminAiReview(
    val operator: OperatorVertexStatus,
    /** 배포 설정이 공용 Vertex 를 허용하는가(상한). false 면 스위치를 켜도 공용 경로는 없다. */
    val deploymentAllowsShared: Boolean,
    /** 관리자 스위치 "공용 Vertex 사용 허용". */
    val sharedEnabled: Boolean,
    val sharedEffective: Boolean,
    val switchUpdatedBy: String?,
    val switchUpdatedAt: OffsetDateTime?,
    val users: List<AdminAiUsageRow>,
)

data class AdminLimitsRequest(
    @field:Min(1)
    @field:Max(1_000_000)
    val signupCap: Int?,
    @field:Min(1)
    @field:Max(1_000)
    val automationActiveCap: Int,
)
