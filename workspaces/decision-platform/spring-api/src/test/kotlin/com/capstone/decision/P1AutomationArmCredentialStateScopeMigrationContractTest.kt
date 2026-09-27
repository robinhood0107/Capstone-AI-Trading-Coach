package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test

class P1AutomationArmCredentialStateScopeMigrationContractTest {
    private val migration =
        requireNotNull(
            javaClass.getResource("/db/migration/V220__automation_arm_reads_owner_credential_state.sql"),
        ).readText()

    @Test
    fun `arm response can reread the owner credential state inside its own scope`() {
        // 무장 응답은 ARM_AUTOMATION 스코프 안에서 상태를 다시 읽는다. 이 스코프를 빼면
        // 자동운용 시작이 42501 로 거부되어 화면에 권한 오류만 남는다.
        assertThat(migration)
            .contains("CREATE OR REPLACE FUNCTION public.p1_read_owner_mock_credential_state_for_automation_v218")
            .contains("scope.operation IN ('READ_AUTOMATION_STATUS','ARM_AUTOMATION')")
            .contains("scope.target_kind='AUTOMATION' AND scope.target_id=p_owner_user_id")
            .contains("pg_catalog.current_setting('app.actor_user_id',true) IS DISTINCT FROM p_owner_user_id")
            .contains("REVOKE ALL ON FUNCTION public.p1_read_owner_mock_credential_state_for_automation_v218(text) FROM PUBLIC")
    }

    @Test
    fun `only the credential state string leaves the function`() {
        assertThat(migration)
            .contains("RETURNS text")
            .contains("SELECT credential.credential_state INTO credential_state_value")
            .doesNotContain("ciphertext")
            .doesNotContain("PUT_MOCK_CREDENTIAL")
    }
}
