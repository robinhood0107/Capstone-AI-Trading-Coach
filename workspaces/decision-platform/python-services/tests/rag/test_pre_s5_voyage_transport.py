from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.rag.pre_s5_provider_control import (
    PreS5VoyageActivation,
    PreS5VoyageDocumentBatchActivation,
)
from app.rag.pre_s5_voyage_transport import (
    PreS5VoyageBundleComponent,
    PreS5VoyageFullBundle,
    PreS5VoyageHttpRequest,
    PreS5VoyageHttpResponse,
    PreS5VoyageTransportError,
    PreS5VoyageContext4Transport,
    UrllibPreS5VoyageHttpSender,
    build_pre_s5_voyage_full_bundle,
)
from app.rag.rag_v2_external_exact30_voyage_runner import (
    VoyagePreChunkedChunk,
    VoyagePreChunkedDocumentGroup,
)
from app.rag.rag_v2_voyage_batching import VoyagePreparedComponent, build_public_voyage_batch_plan


NOW = datetime(2026, 8, 3, 1, tzinfo=UTC)


def test_voyage_context4_transport_consumes_only_exact_manifest_bound_document_batch() -> None:
    public = _public_bundle()
    plan = build_public_voyage_batch_plan(
        components=tuple(
            VoyagePreparedComponent(
                component_scope=component.component_scope,
                owner_scope_sha256=component.owner_scope_sha256,
                groups=component.groups,
            )
            for component in public.components
        ),
        token_counter=_FixtureTokenCounter(),
    )
    assert len(plan.batches) == 1
    batch = plan.batches[0]
    sender = _FixtureSender(response=_response_for_groups(batch.groups, total_tokens=batch.token_count))
    lease = _FixtureLease()
    activation = PreS5VoyageDocumentBatchActivation(
        packet_sha256="a" * 64,
        nonce_sha256="b" * 64,
        batch_plan_sha256=plan.plan_sha256,
        batch_id=batch.batch_id,
        batch_manifest_sha256=batch.batch_manifest_sha256,
        batch_ordinal=batch.batch_ordinal,
        batch_count=batch.batch_count,
        expected_token_count=batch.token_count,
        expected_chunk_count=batch.chunk_count,
        expected_group_count=batch.group_count,
        rate_evidence_sha256="c" * 64,
        tokenizer_sha256="e" * 64,
        provider="VOYAGE",
        operation="CONTEXTUALIZED_DOCUMENT_EMBEDDING",
        origin="https://api.voyageai.com",
        endpoint="/v1/contextualizedembeddings",
        expires_at=NOW + timedelta(minutes=5),
        logical_call_cap=1,
        physical_call_cap=1,
        token_cap=110_000,
        byte_cap=16_777_216,
        cost_cap_microusd=110_000,
        input_microusd_per_token=1,
        retry_count=0,
        raw_artifact_count=0,
    )
    transport = PreS5VoyageContext4Transport(
        activation=activation,
        api_key="test-key",
        lease=lease,
        token_counter=_FixtureTokenCounter(),
        sender=sender,
        clock=lambda: NOW,
    )

    result = transport.embed_document_batch(batch_plan_sha256=plan.plan_sha256, batch=batch)

    assert result.vectors.shape == (142, 1024)
    assert result.expected_input_tokens == batch.token_count
    assert result.provider_total_tokens == batch.token_count
    assert sender.calls == 1
    assert sender.requests[0].max_response_bytes == 16_777_216
    assert lease.claim_calls == 1
    assert lease.committed == []
    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_SINGLE_USE"):
        transport.embed_document_batch(batch_plan_sha256=plan.plan_sha256, batch=batch)

    fresh = PreS5VoyageContext4Transport(
        activation=activation,
        api_key="test-key",
        lease=_FixtureLease(),
        token_counter=_FixtureTokenCounter(),
        sender=_FixtureSender(response=_response_for_groups(batch.groups, total_tokens=batch.token_count)),
        clock=lambda: NOW,
    )
    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_DOCUMENT_BATCH_INVALID"):
        fresh.embed_document_batch(batch_plan_sha256="f" * 64, batch=batch)


def test_voyage_context4_transport_binds_fixed_one_shot_request_to_full_bundle_lease() -> None:
    bundle = _bundle()
    sender = _FixtureSender(response=_response_for(bundle))
    lease = _FixtureLease()
    transport = PreS5VoyageContext4Transport(
        activation=_activation(bundle),
        api_key="test-key",
        lease=lease,
        token_counter=_FixtureTokenCounter(),
        sender=sender,
        clock=lambda: NOW,
    )

    vectors = transport.embed_full_bundle(bundle=bundle)

    assert vectors.shape == (143, 1024)
    assert vectors.dtype == np.float32
    assert sender.calls == 1
    assert transport.external_physical_calls == 1
    assert lease.claim_calls == 1
    assert lease.committed == [(143, 143, 143)]
    request = sender.requests[0]
    assert request.url == "https://api.voyageai.com/v1/contextualizedembeddings"
    assert request.timeout_seconds == 20
    assert request.headers["Authorization"] == "Bearer test-key"
    body = json.loads(request.body)
    assert body["enable_auto_chunking"] is False
    assert body["input_type"] == "document"
    assert body["model"] == "voyage-context-4"
    assert body["output_dimension"] == 1024
    assert body["output_dtype"] == "float"
    assert len(body["inputs"]) == 143
    assert body["inputs"][0] == ["exact30 canonical chunk 000"]
    assert body["inputs"][29] == ["exact30 canonical chunk 029"]
    assert body["inputs"][30] == ["oa112 canonical chunk 000"]
    assert body["inputs"][141] == ["oa112 canonical chunk 111"]
    assert body["inputs"][142] == ["owner_private canonical chunk 000"]
    receipt = json.dumps(transport.content_free_summary(), ensure_ascii=False, sort_keys=True)
    assert "test-key" not in receipt
    assert "canonical chunk" not in receipt
    assert "response" not in receipt


def test_voyage_context4_transport_allows_public_base_empty_owner_private_sentinel() -> None:
    bundle = _public_bundle()
    sender = _FixtureSender(response=_response_for(bundle))
    lease = _FixtureLease()
    transport = PreS5VoyageContext4Transport(
        activation=_activation(bundle),
        api_key="test-key",
        lease=lease,
        token_counter=_FixtureTokenCounter(),
        sender=sender,
        clock=lambda: NOW,
    )

    vectors = transport.embed_full_bundle(bundle=bundle)

    assert vectors.shape == (142, 1024)
    assert sender.calls == 1
    body = json.loads(sender.requests[0].body)
    assert len(body["inputs"]) == 142
    assert all("owner_private" not in value[0] for value in body["inputs"])


def test_voyage_context4_transport_rejects_empty_real_owner_or_nonempty_sentinel() -> None:
    public_bundle = _public_bundle()
    components = list(public_bundle.components)
    components[2] = PreS5VoyageBundleComponent(
        component_scope="OWNER_PRIVATE",
        owner_scope_sha256="d" * 64,
        groups=(),
    )
    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_FULL_BUNDLE_INVALID"):
        build_pre_s5_voyage_full_bundle(components=tuple(components))

    components[2] = PreS5VoyageBundleComponent(
        component_scope="OWNER_PRIVATE",
        owner_scope_sha256=None,
        groups=_groups(prefix="owner_private", count=1),
    )
    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_FULL_BUNDLE_INVALID"):
        build_pre_s5_voyage_full_bundle(components=tuple(components))


def test_voyage_context4_transport_marks_first_attempt_consumed_and_never_retries() -> None:
    bundle = _bundle()
    sender = _FixtureSender(error=OSError("fixture transport down"))
    lease = _FixtureLease()
    transport = PreS5VoyageContext4Transport(
        activation=_activation(bundle),
        api_key="test-key",
        lease=lease,
        token_counter=_FixtureTokenCounter(),
        sender=sender,
        clock=lambda: NOW,
    )

    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_TRANSPORT_UNAVAILABLE"):
        transport.embed_full_bundle(bundle=bundle)
    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_SINGLE_USE"):
        transport.embed_full_bundle(bundle=bundle)
    assert sender.calls == 1
    assert transport.external_physical_calls == 1
    assert lease.unknown_billing_calls == 1


def test_voyage_context4_transport_exposes_only_safe_http_status_class_after_rejection() -> None:
    """Provider 4XX 원문은 폐기하되 다음 exact packet을 고칠 수 있는 상태 분류는 보존한다."""

    bundle = _public_bundle()
    sender = _FixtureSender(response=PreS5VoyageHttpResponse(status=429, headers={}, body=b""))
    transport = PreS5VoyageContext4Transport(
        activation=_activation(bundle),
        api_key="test-key",
        lease=_FixtureLease(),
        token_counter=_FixtureTokenCounter(),
        sender=sender,
        clock=lambda: NOW,
    )

    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_RESPONSE_INVALID"):
        transport.embed_full_bundle(bundle=bundle)

    assert transport.content_free_summary()["providerStatusClass"] == "HTTP_4XX"
    assert "429" not in json.dumps(transport.content_free_summary(), sort_keys=True)


def test_voyage_context4_transport_rejects_expired_subset_or_manifest_drift_before_any_call() -> None:
    bundle = _bundle()
    sender = _FixtureSender(response=_response_for(bundle))
    expired = PreS5VoyageContext4Transport(
        activation=_activation(bundle, expires_at=NOW - timedelta(seconds=1)),
        api_key="test-key",
        lease=_FixtureLease(),
        token_counter=_FixtureTokenCounter(),
        sender=sender,
        clock=lambda: NOW,
    )
    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_ACTIVATION_EXPIRED"):
        expired.embed_full_bundle(bundle=bundle)
    assert sender.calls == 0

    fresh = PreS5VoyageContext4Transport(
        activation=_activation(bundle),
        api_key="test-key",
        lease=_FixtureLease(),
        token_counter=_FixtureTokenCounter(),
        sender=sender,
        clock=lambda: NOW,
    )
    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_FULL_BUNDLE_REQUIRED"):
        fresh.embed_document_groups(groups=_all_groups(bundle))
    assert sender.calls == 0

    stale_manifest = replace(bundle, manifest_sha256="0" * 64)
    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_FULL_BUNDLE_INVALID"):
        fresh.embed_full_bundle(bundle=stale_manifest)
    assert sender.calls == 0


def test_voyage_context4_transport_rechecks_expiry_after_claim_before_sender_post() -> None:
    bundle = _bundle()
    expires_at = NOW + timedelta(minutes=5)
    clock = _MutableClock(NOW)
    lease = _LeaseThatExpiresClock(clock=clock, expires_at=expires_at)
    sender = _FixtureSender(response=_response_for(bundle))
    transport = PreS5VoyageContext4Transport(
        activation=_activation(bundle, expires_at=expires_at),
        api_key="test-key",
        lease=lease,
        token_counter=_FixtureTokenCounter(),
        sender=sender,
        clock=clock,
    )

    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_ACTIVATION_EXPIRED"):
        transport.embed_full_bundle(bundle=bundle)

    assert lease.claim_calls == 1
    assert lease.unknown_billing_calls == 1
    assert sender.calls == 0
    assert transport.external_physical_calls == 0


def test_real_voyage_sender_rechecks_packet_expiry_immediately_before_opening_socket() -> None:
    expired_at = NOW
    sender = UrllibPreS5VoyageHttpSender(clock=lambda: expired_at)
    opener = _NeverOpenOpener()
    sender._opener = opener  # type: ignore[assignment]
    request = PreS5VoyageHttpRequest(
        url="https://api.voyageai.com/v1/contextualizedembeddings",
        headers={
            "Accept": "application/json",
            "Authorization": "Bearer test-key",
            "Content-Type": "application/json",
        },
        body=b"{}",
        timeout_seconds=20,
        max_response_bytes=1,
        expires_at=expired_at,
    )

    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_ACTIVATION_EXPIRED"):
        sender.post(request)

    assert opener.calls == 0


def test_voyage_context4_transport_uses_official_token_count_not_utf8_byte_count() -> None:
    public = _public_bundle()
    compact_components: list[PreS5VoyageBundleComponent] = []
    for component in public.components:
        compact_groups: list[VoyagePreChunkedDocumentGroup] = []
        for group_index, group in enumerate(component.groups):
            compact_chunks = tuple(
                replace(
                    chunk,
                    canonical_text="x",
                    canonical_text_sha256=_sha256("x"),
                    # BGE chunk count is part of the signed bundle manifest, but only the official
                    # Voyage tokenizer can authorize its outbound input token cap.
                    token_count=600 if component.component_scope == "EXACT30" and group_index == 0 else 1,
                )
                for chunk in group.chunks
            )
            compact_groups.append(replace(group, chunks=compact_chunks))
        compact_components.append(replace(component, groups=tuple(compact_groups)))
    token_heavy = build_pre_s5_voyage_full_bundle(components=tuple(compact_components))
    sender = _FixtureSender(response=_response_for(token_heavy))
    transport = PreS5VoyageContext4Transport(
        activation=_activation(token_heavy, token_cap=200),
        api_key="test-key",
        lease=_FixtureLease(),
        token_counter=_FixtureTokenCounter(overrides={"x": 201}),
        sender=sender,
        clock=lambda: NOW,
    )

    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_OFFICIAL_TOKENIZER_REQUIRED"):
        transport.embed_full_bundle(bundle=token_heavy)

    assert sender.calls == 0


def test_voyage_context4_transport_rejects_more_than_documented_contextual_chunk_cap() -> None:
    public = _public_bundle()
    components = list(public.components)
    first_group = components[0].groups[0]
    # The public profile has 142 base chunks. Add just enough valid chunks to exceed Voyage's
    # documented 16,000 contextual-chunk cap without creating another document group.
    overflow_chunks = tuple(
        _extra_chunk(index)
        for index in range(16_000 - 142 + 1)
    )
    components[0] = replace(
        components[0],
        groups=(replace(first_group, chunks=first_group.chunks + overflow_chunks), *components[0].groups[1:]),
    )

    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_FULL_BUNDLE_INVALID"):
        build_pre_s5_voyage_full_bundle(components=tuple(components))


def test_voyage_context4_transport_rejects_cross_group_or_nonunit_response_without_followup_call() -> None:
    bundle = _bundle()
    malformed = _response_for(bundle)
    body = json.loads(malformed.body)
    body["data"][1]["data"][0]["text"] = "wrong group text"
    sender = _FixtureSender(
        response=PreS5VoyageHttpResponse(status=200, headers={}, body=json.dumps(body).encode("utf-8"))
    )
    lease = _FixtureLease()
    transport = PreS5VoyageContext4Transport(
        activation=_activation(bundle),
        api_key="test-key",
        lease=lease,
        token_counter=_FixtureTokenCounter(),
        sender=sender,
        clock=lambda: NOW,
    )

    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_RESPONSE_INVALID"):
        transport.embed_full_bundle(bundle=bundle)
    assert sender.calls == 1
    assert transport.external_physical_calls == 1
    assert lease.unknown_billing_calls == 1


def test_voyage_context4_transport_keeps_default_sender_disabled_without_consuming_packet() -> None:
    bundle = _bundle()
    lease = _FixtureLease()
    transport = PreS5VoyageContext4Transport(
        activation=_activation(bundle),
        api_key="test-key",
        lease=lease,
        token_counter=_FixtureTokenCounter(),
        clock=lambda: NOW,
    )

    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_OUTBOUND_DISABLED"):
        transport.embed_full_bundle(bundle=bundle)

    assert transport.external_physical_calls == 0
    assert transport.content_free_summary()["logicalCallsConsumed"] == 0
    assert lease.claim_calls == 0


def test_voyage_context4_transport_rejects_nonunit_vector_after_exactly_one_attempt() -> None:
    bundle = _bundle()
    malformed = _response_for(bundle)
    body = json.loads(malformed.body)
    body["data"][0]["data"][0]["embedding"] = [0.0] * 1024
    sender = _FixtureSender(
        response=PreS5VoyageHttpResponse(
            status=200,
            headers={},
            body=json.dumps(body, separators=(",", ":")).encode("utf-8"),
        )
    )
    transport = PreS5VoyageContext4Transport(
        activation=_activation(bundle),
        api_key="test-key",
        lease=_FixtureLease(),
        token_counter=_FixtureTokenCounter(),
        sender=sender,
        clock=lambda: NOW,
    )

    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_RESPONSE_INVALID"):
        transport.embed_full_bundle(bundle=bundle)
    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_SINGLE_USE"):
        transport.embed_full_bundle(bundle=bundle)
    assert sender.calls == 1
    assert transport.external_physical_calls == 1


def test_voyage_context4_transport_lease_blocks_two_transport_instances_from_reusing_packet() -> None:
    bundle = _bundle()
    lease = _FixtureLease()
    first_sender = _FixtureSender(response=_response_for(bundle))
    first = PreS5VoyageContext4Transport(
        activation=_activation(bundle),
        api_key="test-key",
        lease=lease,
        token_counter=_FixtureTokenCounter(),
        sender=first_sender,
        clock=lambda: NOW,
    )
    first.embed_full_bundle(bundle=bundle)

    second_sender = _FixtureSender(response=_response_for(bundle))
    second = PreS5VoyageContext4Transport(
        activation=_activation(bundle),
        api_key="test-key",
        lease=lease,
        token_counter=_FixtureTokenCounter(),
        sender=second_sender,
        clock=lambda: NOW,
    )
    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_SINGLE_USE"):
        second.embed_full_bundle(bundle=bundle)
    assert first_sender.calls == 1
    assert second_sender.calls == 0
    assert second.external_physical_calls == 0


def test_voyage_context4_transport_rechecks_expiry_immediately_before_claiming_lease() -> None:
    bundle = _bundle()
    sender = _FixtureSender(response=_response_for(bundle))
    moments = iter((NOW, NOW + timedelta(minutes=6)))
    lease = _FixtureLease()
    transport = PreS5VoyageContext4Transport(
        activation=_activation(bundle),
        api_key="test-key",
        lease=lease,
        token_counter=_FixtureTokenCounter(),
        sender=sender,
        clock=lambda: next(moments),
    )

    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_ACTIVATION_EXPIRED"):
        transport.embed_full_bundle(bundle=bundle)
    assert sender.calls == 0
    assert lease.claim_calls == 0


def test_voyage_context4_transport_discards_malformed_raw_body_without_exception_cause() -> None:
    bundle = _bundle()
    sender = _FixtureSender(
        response=PreS5VoyageHttpResponse(status=200, headers={}, body=b"\xffSECRET_RAW_PROVIDER_BODY")
    )
    transport = PreS5VoyageContext4Transport(
        activation=_activation(bundle),
        api_key="test-key",
        lease=_FixtureLease(),
        token_counter=_FixtureTokenCounter(),
        sender=sender,
        clock=lambda: NOW,
    )

    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_RESPONSE_INVALID") as raised:
        transport.embed_full_bundle(bundle=bundle)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_voyage_context4_transport_rejects_rate_that_exceeds_packet_cost_cap() -> None:
    bundle = _bundle()
    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_ACTIVATION_INVALID"):
        PreS5VoyageContext4Transport(
            activation=_activation(bundle, cost_cap_microusd=142),
            api_key="test-key",
            lease=_FixtureLease(),
            token_counter=_FixtureTokenCounter(),
            clock=lambda: NOW,
        )


def test_voyage_context4_transport_rejects_wrong_official_tokenizer_before_claim_or_send() -> None:
    bundle = _public_bundle()
    sender = _FixtureSender(response=_response_for(bundle))
    lease = _FixtureLease()

    with pytest.raises(PreS5VoyageTransportError, match="PRE_S5_VOYAGE_OFFICIAL_TOKENIZER_REQUIRED"):
        PreS5VoyageContext4Transport(
            activation=replace(_activation(bundle), tokenizer_sha256="f" * 64),
            api_key="test-key",
            lease=lease,
            token_counter=_FixtureTokenCounter(),
            sender=sender,
            clock=lambda: NOW,
        )

    assert lease.claim_calls == 0
    assert sender.calls == 0


class _FixtureSender:
    """network 없이 fixed response/error 한 번만 내는 transport seam이다."""

    def __init__(self, *, response: PreS5VoyageHttpResponse | None = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.calls = 0
        self.requests: list[PreS5VoyageHttpRequest] = []

    def post(self, request: PreS5VoyageHttpRequest) -> PreS5VoyageHttpResponse:
        self.calls += 1
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


class _NeverOpenOpener:
    def __init__(self) -> None:
        self.calls = 0

    def open(self, *_args: object, **_kwargs: object) -> object:
        self.calls += 1
        raise AssertionError("expired packet must not open a socket")


class _MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class _FixtureLease:
    """DB lease가 보장해야 할 one-shot/outcome semantics를 offline으로 재현한다."""

    def __init__(self) -> None:
        self._claimed = False
        self.claim_calls = 0
        self.committed: list[tuple[int, int, int]] = []
        self.unknown_billing_calls = 0

    def claim_attempt(self, *, now: datetime) -> None:
        del now
        if self._claimed:
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_SINGLE_USE")
        self._claimed = True
        self.claim_calls += 1

    def commit(
        self,
        *,
        expected_input_tokens: int,
        total_tokens: int,
        actual_cost_microusd: int,
    ) -> None:
        self.committed.append((expected_input_tokens, total_tokens, actual_cost_microusd))

    def mark_unknown_billing(self) -> None:
        self.unknown_billing_calls += 1


class _LeaseThatExpiresClock(_FixtureLease):
    def __init__(self, *, clock: _MutableClock, expires_at: datetime) -> None:
        super().__init__()
        self._clock = clock
        self._expires_at = expires_at

    def claim_attempt(self, *, now: datetime) -> None:
        super().claim_attempt(now=now)
        self._clock.value = self._expires_at


class _FixtureTokenCounter:
    """Real provider tokenizer artifact 없이 pre-call official-count boundary를 exercise한다."""

    model = "voyage-context-4"
    tokenizer_sha256 = "e" * 64

    def __init__(self, *, overrides: dict[str, int] | None = None) -> None:
        self._overrides = overrides or {}

    def count_texts(self, *, texts: tuple[str, ...], token_cap: int) -> int:
        total = sum(self._overrides.get(text, 1) for text in texts)
        if total > token_cap:
            raise ValueError("fixture cap")
        return total


def _activation(
    bundle: PreS5VoyageFullBundle,
    *,
    expires_at: datetime | None = None,
    cost_cap_microusd: int = 200_000,
    token_cap: int = 120_000,
) -> PreS5VoyageActivation:
    return PreS5VoyageActivation(
        packet_sha256="a" * 64,
        nonce_sha256="b" * 64,
        bundle_manifest_sha256=bundle.manifest_sha256,
        rate_evidence_sha256="c" * 64,
        tokenizer_sha256="e" * 64,
        provider="VOYAGE",
        operation="CONTEXTUALIZED_DOCUMENT_EMBEDDING",
        origin="https://api.voyageai.com",
        endpoint="/v1/contextualizedembeddings",
        expires_at=expires_at or NOW + timedelta(minutes=5),
        logical_call_cap=1,
        physical_call_cap=1,
        token_cap=token_cap,
        byte_cap=4_194_304,
        cost_cap_microusd=cost_cap_microusd,
        input_microusd_per_token=1,
        retry_count=0,
        raw_artifact_count=0,
    )


def _bundle() -> PreS5VoyageFullBundle:
    return build_pre_s5_voyage_full_bundle(
        components=(
            PreS5VoyageBundleComponent(
                component_scope="EXACT30",
                owner_scope_sha256=None,
                groups=_groups(prefix="exact30", count=30),
            ),
            PreS5VoyageBundleComponent(
                component_scope="OA112",
                owner_scope_sha256=None,
                groups=_groups(prefix="oa112", count=112),
            ),
            PreS5VoyageBundleComponent(
                component_scope="OWNER_PRIVATE",
                owner_scope_sha256="d" * 64,
                groups=_groups(prefix="owner_private", count=1),
            ),
        )
    )


def _public_bundle() -> PreS5VoyageFullBundle:
    return build_pre_s5_voyage_full_bundle(
        components=(
            PreS5VoyageBundleComponent(
                component_scope="EXACT30",
                owner_scope_sha256=None,
                groups=_groups(prefix="exact30", count=30),
            ),
            PreS5VoyageBundleComponent(
                component_scope="OA112",
                owner_scope_sha256=None,
                groups=_groups(prefix="oa112", count=112),
            ),
            PreS5VoyageBundleComponent(
                component_scope="OWNER_PRIVATE",
                owner_scope_sha256=None,
                groups=(),
            ),
        )
    )


def _groups(*, prefix: str, count: int) -> tuple[VoyagePreChunkedDocumentGroup, ...]:
    values: list[VoyagePreChunkedDocumentGroup] = []
    for index in range(count):
        text = f"{prefix} canonical chunk {index:03d}"
        source_id = f"src_{prefix}_{index:03d}"
        source_revision_id = f"srv_{prefix}_{index:03d}"
        chunk_digest = _sha256(f"chunk|{prefix}|{index}")
        values.append(
            VoyagePreChunkedDocumentGroup(
                source_id=source_id,
                source_revision_id=source_revision_id,
                context_set_hash=_sha256(f"context|{prefix}|{index}"),
                chunks=(
                    VoyagePreChunkedChunk(
                        chunk_id=f"rag_v2_chk_{chunk_digest[:32]}",
                        canonical_text=text,
                        canonical_text_sha256=_sha256(text),
                        embedding_input_hash=_sha256(f"embedding|{prefix}|{index}"),
                        token_count=1,
                    ),
                ),
            )
        )
    return tuple(values)


def _extra_chunk(index: int) -> VoyagePreChunkedChunk:
    """16K request ceiling regression용으로 unique, hash-bound minimal chunk를 만든다."""

    text = f"contextual chunk cap {index:05d}"
    digest = _sha256(f"contextual-cap|{index}")
    return VoyagePreChunkedChunk(
        chunk_id=f"rag_v2_chk_{digest[:32]}",
        canonical_text=text,
        canonical_text_sha256=_sha256(text),
        embedding_input_hash=_sha256(f"contextual-input|{index}"),
        token_count=1,
    )


def _all_groups(bundle: PreS5VoyageFullBundle) -> tuple[VoyagePreChunkedDocumentGroup, ...]:
    return tuple(group for component in bundle.components for group in component.groups)


def _response_for(bundle: PreS5VoyageFullBundle) -> PreS5VoyageHttpResponse:
    return _response_for_groups(_all_groups(bundle), total_tokens=143)


def _response_for_groups(
    groups: tuple[VoyagePreChunkedDocumentGroup, ...],
    *,
    total_tokens: int,
) -> PreS5VoyageHttpResponse:
    data: list[dict[str, object]] = []
    for group_index, group in enumerate(groups):
        chunks: list[dict[str, object]] = []
        for chunk_index, chunk in enumerate(group.chunks):
            vector = [0.0] * 1024
            vector[(group_index + chunk_index) % 1024] = 1.0
            chunks.append({"embedding": vector, "index": chunk_index, "text": chunk.canonical_text})
        data.append({"data": chunks, "index": group_index})
    return PreS5VoyageHttpResponse(
        status=200,
        headers={"content-type": "application/json"},
        body=json.dumps(
            {
                "chunker_version": "fixture",
                "data": data,
                "model": "voyage-context-4",
                "usage": {"total_tokens": total_tokens},
            },
            separators=(",", ":"),
        ).encode("utf-8"),
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
