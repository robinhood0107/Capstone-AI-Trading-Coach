package com.capstone.decision.infrastructure.security

interface ActorCapabilityIssuer {
    fun issue(
        actorUserId: String,
        binding: ActorCapabilityBinding,
    ): String
}

class ActorCapabilityDeniedException(
    message: String = "Actor capability is unavailable.",
) : RuntimeException(message)
