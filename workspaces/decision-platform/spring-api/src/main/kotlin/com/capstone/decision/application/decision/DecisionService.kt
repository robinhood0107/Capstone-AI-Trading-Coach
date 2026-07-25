package com.capstone.decision.application.decision

import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.application.risk.PortfolioEvaluationCommand
import com.capstone.decision.application.risk.PortfolioEvaluationUseCase
import com.capstone.decision.application.risk.port.PrincipleSnapshotPort
import com.capstone.decision.domain.principle.PrincipleNotFoundException
import com.capstone.decision.domain.risk.EvaluationBounds
import com.capstone.decision.domain.risk.SnapshotHashService
import org.springframework.stereotype.Service
import tools.jackson.databind.ObjectMapper
import java.time.Clock
import java.time.Duration
import java.util.UUID

/**
 * remote/stored source read와 pure evaluation은 transaction 밖에서 끝내고 마지막 writer 호출만 원자 transaction에 맡긴다.
 */
@Service
class DecisionService(
    private val principleSnapshotPort: PrincipleSnapshotPort,
    private val evaluationUseCase: PortfolioEvaluationUseCase,
    private val persistencePort: DecisionPersistencePort,
    private val idempotencyHasher: DecisionIdempotencyIdentityPort,
    private val claimService: DecisionIdempotencyClaimPort,
    private val projectionFactory: DecisionProjectionFactory,
    private val observability: DecisionObservability,
    private val validityPolicy: DecisionValidityPolicy,
    private val objectMapper: ObjectMapper,
    private val clock: Clock,
) {
    fun evaluate(
        actor: DecisionActor,
        rawIdempotencyKey: String,
        command: EvaluateOrderCommand,
    ): DecisionProjection {
        val startedAtNanos = System.nanoTime()
        var metricMode = DecisionMetricMode.UNPINNED
        val evaluationAsOf = clock.instant()
        val identity = idempotencyHasher.identity(actor.userId, rawIdempotencyKey, command)
        try {
            persistencePort
                .findIdempotencyResult(
                    identity.scopeHash,
                    identity.ownerScopeHash,
                    evaluationAsOf,
                )?.let { stored ->
                    if (stored.requestHash != identity.requestHash) {
                        throw DecisionIdempotencyConflictException()
                    }
                    return recordTimed(
                        projectionFactory.fromCanonicalJson(stored.projectionCanonicalJson),
                        startedAtNanos,
                    )
                }
            val pinned =
                principleSnapshotPort.findActiveOwned(actor.userId, command.principleId)
                    ?: throw DecisionNotFoundException()
            metricMode = DecisionMetricMode.valueOf(pinned.mode.name)
            val claim =
                when (val lookup = claimService.acquire(identity.scopeHash, identity.requestHash)) {
                    is DecisionClaimLookup.Acquired -> lookup.claim
                    DecisionClaimLookup.Conflict -> throw DecisionIdempotencyConflictException()
                    DecisionClaimLookup.InProgress -> throw DecisionIdempotencyInProgressException()
                }
            return recordTimed(
                evaluateWithClaim(
                    actor = actor,
                    command = command,
                    identity = identity,
                    evaluationAsOf = evaluationAsOf,
                    pinned = pinned,
                    claim = claim,
                ),
                startedAtNanos,
            )
        } catch (exception: DecisionNotFoundException) {
            throw exception
        } catch (exception: DecisionIdempotencyConflictException) {
            throw exception
        } catch (exception: DecisionIdempotencyInProgressException) {
            throw exception
        } catch (exception: DecisionVersionConflictException) {
            throw exception
        } catch (exception: DecisionTechnicalException) {
            recordTechnicalFailure(startedAtNanos, metricMode)
            throw exception
        } catch (exception: Exception) {
            recordTechnicalFailure(startedAtNanos, metricMode)
            throw DecisionTechnicalException(exception)
        }
    }

    fun getOwned(
        actorUserId: String,
        decisionId: String,
    ): DecisionProjection =
        try {
            persistencePort.findOwnedProjection(actorUserId, decisionId)
                ?: throw DecisionNotFoundException()
        } catch (exception: DecisionNotFoundException) {
            throw exception
        } catch (exception: Exception) {
            throw DecisionTechnicalException(exception)
        }

    fun getOwnedAudit(
        actorUserId: String,
        decisionId: String,
    ): DecisionAuditProjection =
        try {
            persistencePort.findOwnedAudit(actorUserId, decisionId)
                ?: throw DecisionNotFoundException()
        } catch (exception: DecisionNotFoundException) {
            throw exception
        } catch (exception: Exception) {
            throw DecisionTechnicalException(exception)
        }

    private fun evaluateWithClaim(
        actor: DecisionActor,
        command: EvaluateOrderCommand,
        identity: DecisionIdempotencyIdentity,
        evaluationAsOf: java.time.Instant,
        pinned: com.capstone.decision.application.risk.port.ActivePrincipleSnapshot,
        claim: DecisionIdempotencyClaim,
    ): DecisionProjection {
        try {
            val evaluationId = id("evl")
            val decisionId = id("dec")
            val evaluation =
                evaluationUseCase.evaluatePinned(
                    PortfolioEvaluationCommand(
                        actorUserId = actor.userId,
                        principleId = command.principleId,
                        portfolioSource = command.portfolioSource,
                        evaluationId = evaluationId,
                        evaluationAsOf = evaluationAsOf,
                        orderIntent = command.orderIntent,
                        decisionId = decisionId,
                    ),
                    pinned,
                )
            val projection =
                projectionFactory.create(
                    decisionId = decisionId,
                    createdAt = evaluationAsOf,
                    configuredValidity = validityPolicy.configuredValidity,
                    evaluation = evaluation,
                )
            val projectionJson = projectionFactory.canonicalJson(projection)
            val envelope =
                ApiResponseFactory.success(
                    requestId = actor.requestId,
                    data = projection,
                )
            check(objectMapper.writeValueAsBytes(envelope).size <= EvaluationBounds.MAX_RESPONSE_BYTES) {
                "Decision response exceeded the approved response bound."
            }
            val snapshot = requireNotNull(evaluation.snapshot)
            val hashService = SnapshotHashService()
            val writeRequest =
                DecisionWriteRequest(
                    actor = actor,
                    decisionId = decisionId,
                    evaluationId = evaluationId,
                    projection = projection,
                    projectionCanonicalJson = projectionJson,
                    snapshotArtifactCanonicalJson = hashService.snapshotArtifactCanonicalJson(snapshot),
                    semanticInputHash = requireNotNull(evaluation.semanticInputHash),
                    snapshotArtifactHash = requireNotNull(evaluation.snapshotArtifactHash),
                    snapshotSchemaVersion = snapshot.snapshotSchemaVersion,
                    catalogVersion = snapshot.systemRuleCatalogVersion,
                    readinessPolicyVersion = snapshot.readinessPolicyVersion,
                    mappingVersions =
                        buildMap {
                            snapshot.disclosureEvidence?.let {
                                put("disclosure", it.mappingVersion)
                            }
                        },
                    orderIntent = command.orderIntent,
                    idempotency = identity,
                    principleMode = pinned.mode,
                )
            try {
                persistencePort.persist(writeRequest)
                observability.recordPersisted(projection, actor.requestId)
                return projection
            } catch (exception: DecisionPersistenceReplayException) {
                return projectionFactory.fromCanonicalJson(exception.projectionCanonicalJson)
            }
        } catch (exception: PrincipleNotFoundException) {
            throw DecisionNotFoundException()
        } finally {
            // durable DB result가 권위이므로 release 실패가 이미 commit된 200을 뒤집지는 않는다.
            runCatching { claimService.release(claim) }
        }
    }

    private fun recordTimed(
        projection: DecisionProjection,
        startedAtNanos: Long,
    ): DecisionProjection {
        observability.recordTimer(
            outcome = DecisionMetricOutcome.valueOf(projection.riskDecision.decision),
            mode = DecisionMetricMode.valueOf(projection.mode),
            duration = elapsed(startedAtNanos),
        )
        return projection
    }

    private fun recordTechnicalFailure(
        startedAtNanos: Long,
        mode: DecisionMetricMode,
    ) {
        observability.recordTimer(
            outcome = DecisionMetricOutcome.ERROR,
            mode = mode,
            duration = elapsed(startedAtNanos),
        )
    }

    private fun elapsed(startedAtNanos: Long): Duration = Duration.ofNanos((System.nanoTime() - startedAtNanos).coerceAtLeast(0))

    private fun id(prefix: String): String = "${prefix}_${UUID.randomUUID().toString().replace("-", "")}"
}
