package com.capstone.decision.domain.risk

// 저장·감사·metric에는 자유 서술 대신 이 low-cardinality 분류만 사용한다.
enum class KillSwitchReasonClass {
    USER_MANUAL_STOP,
    OPERATOR_MANUAL_STOP,
    DATA_FRESHNESS_STOP,
    BROKERAGE_FAILURE_STOP,
    DEMO_SAFETY_STOP,
    ADMIN_RESUME,
    INITIAL_STATE,
    ;

    companion object {
        /**
         * DB projection의 문자열은 allowlist 밖 값을 허용하지 않아 schema drift를 fail-closed한다.
         */
        fun fromStored(value: String?): KillSwitchReasonClass {
            require(!value.isNullOrBlank() && value.length <= MAX_REASON_CHARS && value.none(Char::isISOControl))
            return entries.firstOrNull { it.name == value }
                ?: throw IllegalArgumentException("Unknown Kill Switch reason class.")
        }

        /**
         * 자유 서술은 유효성만 확인하고 폐기하며 actor와 방향만으로 저장 class를 결정한다.
         */
        fun forManualChange(
            active: Boolean,
            actorRole: KillSwitchActorRole,
            rawReason: String? = null,
        ): KillSwitchReasonClass {
            validateManualReason(rawReason)
            return when {
                !active && actorRole == KillSwitchActorRole.ADMIN -> ADMIN_RESUME
                !active -> throw IllegalArgumentException("Only ADMIN can resume the Kill Switch.")
                actorRole == KillSwitchActorRole.USER -> USER_MANUAL_STOP
                actorRole == KillSwitchActorRole.ADMIN -> OPERATOR_MANUAL_STOP
                else -> throw IllegalArgumentException("SYSTEM changes require an internal reason class.")
            }
        }

        /**
         * API와 application이 동일한 allowlist를 사용해 자유 서술을 저장 경계 전에 폐기한다.
         */
        fun validateManualReason(rawReason: String?) {
            if (rawReason != null) {
                require(rawReason.isNotBlank())
                require(rawReason.length <= MAX_REASON_CHARS)
                require(rawReason.none(Char::isISOControl))
                require(FORBIDDEN_FRAGMENTS.none(rawReason::contains))
                require(!SQL_EXPRESSION.containsMatchIn(rawReason))
            }
        }

        const val MAX_REASON_CHARS: Int = 200
        private val FORBIDDEN_FRAGMENTS = listOf("'", "\"", ";", "--", "/*", "*/", "=")
        private val SQL_EXPRESSION =
            Regex("""(?i)(?:^|\s)(?:select|insert|update|delete|drop|alter|union|or|and)(?:\s|$)""")
    }
}

enum class KillSwitchActorRole {
    USER,
    ADMIN,
    SYSTEM,
}
