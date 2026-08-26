package com.capstone.decision.infrastructure.security

import java.net.InetSocketAddress
import java.net.Socket

object P1TcpHealthCheck {
    private const val HOST = "127.0.0.1"
    private const val PORT = 18081
    private const val TIMEOUT_MILLIS = 2_000

    @JvmStatic
    fun main(args: Array<String>) {
        require(args.isEmpty()) { "health check arguments are not supported" }
        check(HOST, PORT, TIMEOUT_MILLIS)
    }

    internal fun check(
        host: String,
        port: Int,
        timeoutMillis: Int,
    ) {
        require(host == HOST)
        require(port in 1..65_535)
        require(timeoutMillis in 1..5_000)
        Socket().use { socket ->
            socket.connect(InetSocketAddress(host, port), timeoutMillis)
        }
    }
}
