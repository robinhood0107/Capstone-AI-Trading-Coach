package com.capstone.decision

import java.security.KeyPairGenerator
import java.util.Base64

/** Generated local test credential; no provider account or network call. */
object TestVertexServiceAccount {
    val base64: String by lazy {
        val key =
            KeyPairGenerator
                .getInstance("RSA")
                .apply { initialize(2048) }
                .generateKeyPair()
                .private.encoded
        val pem = Base64.getMimeEncoder(64, byteArrayOf('\n'.code.toByte())).encodeToString(key)
        val escapedKey = "-----BEGIN PRIVATE KEY-----\n$pem\n-----END PRIVATE KEY-----\n".replace("\n", "\\n")
        val json =
            """{"type":"service_account","project_id":"test-project","private_key_id":"0123456789abcdef",""" +
                """"private_key":"$escapedKey",""" +
                """"client_email":"fixture@test-project.iam.gserviceaccount.com","token_uri":"https://oauth2.googleapis.com/token"}"""
        Base64.getEncoder().encodeToString(json.toByteArray())
    }
}
