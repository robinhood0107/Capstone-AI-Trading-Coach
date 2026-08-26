package com.capstone.decision

import com.capstone.decision.infrastructure.security.P1TcpHealthCheck
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Test
import java.net.ServerSocket

class P1TcpHealthCheckTest {
    @Test
    fun `health check accepts a listening loopback service`() {
        ServerSocket(0, 1).use { server ->
            P1TcpHealthCheck.check("127.0.0.1", server.localPort, 500)
        }
    }

    @Test
    fun `health check rejects a closed loopback port`() {
        val port = ServerSocket(0, 1).use { it.localPort }
        assertThrows(Exception::class.java) {
            P1TcpHealthCheck.check("127.0.0.1", port, 500)
        }
    }
}
