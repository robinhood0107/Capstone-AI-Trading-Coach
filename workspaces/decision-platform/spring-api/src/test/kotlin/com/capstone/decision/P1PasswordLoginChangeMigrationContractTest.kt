package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test

class P1PasswordLoginChangeMigrationContractTest {
    private val migration =
        requireNotNull(
            javaClass.getResource("/db/migration/V221__password_login_change.sql"),
        ).readText()

    @Test
    fun `change verifies the current password and only touches email password identities`() {
        assertThat(migration)
            .contains("CREATE FUNCTION public.change_password_login_actor_v1")
            .contains("session_user <> 'decision_auth'")
            .contains("public.crypt(p_current_password, stored_hash) <> stored_hash")
            .contains("UPDATE public.password_login_identities")
            .contains("ERRCODE = 'P0002'")
            .doesNotContain("UPDATE public.users")
            .doesNotContain("credential_bundle_mac")
    }

    @Test
    fun `change revokes every earlier session and is audited`() {
        assertThat(migration)
            .contains("UPDATE public.actor_auth_session session")
            .contains("SET revoked_at = now_at")
            .contains("'PASSWORD_LOGIN_CHANGED'")
            .contains("GRANT EXECUTE ON FUNCTION public.change_password_login_actor_v1(text, text, text, integer)\n  TO decision_auth")
            .contains("FROM PUBLIC, decision_app, decision_identity, decision_worker, decision_replay")
    }
}
