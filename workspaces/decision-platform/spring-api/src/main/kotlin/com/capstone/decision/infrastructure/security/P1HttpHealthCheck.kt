package com.capstone.decision.infrastructure.security

import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.time.Duration

object P1HttpHealthCheck {
    @JvmStatic
    fun main(args: Array<String>) {
        require(args.isEmpty()) { "health check arguments are not supported" }
        val request =
            HttpRequest
                .newBuilder(URI.create("http://127.0.0.1:8080/actuator/health"))
                .timeout(Duration.ofSeconds(2))
                .GET()
                .build()
        val response =
            HttpClient
                .newBuilder()
                .connectTimeout(Duration.ofSeconds(2))
                .build()
                .send(request, HttpResponse.BodyHandlers.discarding())
        check(response.statusCode() == 200) { "health check failed" }
    }
}
