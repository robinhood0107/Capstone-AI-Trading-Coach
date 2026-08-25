package com.capstone.decision.infrastructure.security

import com.capstone.decision.application.security.AuthenticatedActorRef

interface ActorCapabilityIssuer {
    fun issue(
        actor: AuthenticatedActorRef,
        binding: ActorCapabilityBinding,
    ): String
}

class ActorCapabilityDeniedException(
    message: String = "Actor capability is unavailable.",
) : RuntimeException(message)
