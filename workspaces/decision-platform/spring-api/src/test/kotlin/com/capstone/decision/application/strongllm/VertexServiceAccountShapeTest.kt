package com.capstone.decision.application.strongllm

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.util.Base64

class VertexServiceAccountShapeTest {
    private fun encode(json: String) = Base64.getEncoder().encodeToString(json.toByteArray())

    private val fake =
        """{"type":"service_account","project_id":"mars-test-dummy","private_key_id":"0123456789abcdef",""" +
            """"private_key":"-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n",""" +
            """"client_email":"dummy@mars-test-dummy.iam.gserviceaccount.com","token_uri":"https://oauth2.googleapis.com/token"}"""

    @Test
    fun `a canonical base64 service account is accepted and shows its key id tail`() {
        val encoded = encode(fake)
        assertThat(VertexServiceAccountShape.isValid(encoded)).isTrue()
        assertThat(VertexServiceAccountShape.displayLast4(encoded)).isEqualTo("cdef")
    }

    @Test
    fun `API keys, other JSON and non canonical encodings are refused`() {
        assertThat(VertexServiceAccountShape.isValid("AIzaSyFAKEFAKEFAKEFAKE")).isFalse()
        assertThat(VertexServiceAccountShape.isValid(encode(fake.replace("service_account", "authorized_user")))).isFalse()
        assertThat(VertexServiceAccountShape.isValid(encode(fake.replace("oauth2.googleapis.com", "evil.example")))).isFalse()
        assertThat(VertexServiceAccountShape.isValid(Base64.getMimeEncoder().encodeToString(fake.toByteArray()))).isFalse()
        assertThat(VertexServiceAccountShape.isValid(encode(fake) + "\n")).isFalse()
        val padded = encode(fake + " ")
        if (padded.endsWith("=")) assertThat(VertexServiceAccountShape.isValid(padded.trimEnd('='))).isFalse()
        assertThat(VertexServiceAccountShape.isValid("")).isFalse()
    }

    @Test
    fun `the display tail always fits the stored last4 shape`() {
        val noKeyId = encode(fake.replace(""""private_key_id":"0123456789abcdef",""", ""))
        assertThat(VertexServiceAccountShape.displayLast4(noKeyId)).matches("^[A-Za-z0-9_-]{4}$")
    }
}
