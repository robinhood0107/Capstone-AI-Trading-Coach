"""S5.6 one-shot/resume provider call accounting과 fail-stop orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeVar

from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.production_policy import BootstrapBudget


class BootstrapPhase(StrEnum):
    KRX = "KRX"
    KIS = "KIS"
    ECOS = "ECOS"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class BootstrapCallReceipt:
    """Provider content나 URL 없이 physical handoff 결과만 보존한다."""

    ordinal: int
    provider: str
    operation_id: str
    query_key_sha256: str
    success: bool


@dataclass(slots=True)
class BootstrapLedger:
    """실패 후 remaining call 0과 cumulative resume budget을 강제한다."""

    budget: BootstrapBudget
    phase: BootstrapPhase = BootstrapPhase.KRX
    receipts: list[BootstrapCallReceipt] = field(default_factory=list)
    failed_query_key_sha256: str | None = None

    def physical_call(
        self,
        *,
        provider: str,
        operation_id: str,
        query_key_sha256: str,
        call: Callable[[], _T],
    ) -> _T:
        """한 provider handoff를 예약하고 성공/실패 모두 누적 budget에 반영한다."""

        if self.phase in {BootstrapPhase.FAILED, BootstrapPhase.COMPLETE}:
            raise LightGbmContractError("bootstrap run no longer accepts provider calls")
        if provider != self.phase.value:
            raise LightGbmContractError("bootstrap provider order is invalid")
        if len(self.receipts) >= self.budget.total:
            raise LightGbmContractError("bootstrap cumulative physical budget exhausted")
        if provider == "KIS":
            token_call = operation_id == "oauth2/tokenP"
            provider_limit = self.budget.kis_token if token_call else self.budget.kis_get
            provider_count = sum(
                receipt.provider == provider
                and (receipt.operation_id == "oauth2/tokenP") is token_call
                for receipt in self.receipts
            )
        else:
            provider_limit = {
                "KRX": self.budget.krx_get,
                "ECOS": self.budget.ecos_get,
            }[provider]
            provider_count = sum(receipt.provider == provider for receipt in self.receipts)
        if provider_count >= provider_limit:
            raise LightGbmContractError("bootstrap provider physical budget exhausted")
        ordinal = len(self.receipts) + 1
        try:
            result = call()
        except Exception:
            self.receipts.append(
                BootstrapCallReceipt(ordinal, provider, operation_id, query_key_sha256, False)
            )
            self.failed_query_key_sha256 = query_key_sha256
            self.phase = BootstrapPhase.FAILED
            raise
        self.receipts.append(
            BootstrapCallReceipt(ordinal, provider, operation_id, query_key_sha256, True)
        )
        return result

    def advance(self, completed_phase: BootstrapPhase) -> None:
        """KRX→KIS→ECOS 순서만 허용하며 incomplete phase를 caller가 건너뛸 수 없다."""

        expected = {
            BootstrapPhase.KRX: BootstrapPhase.KIS,
            BootstrapPhase.KIS: BootstrapPhase.ECOS,
            BootstrapPhase.ECOS: BootstrapPhase.COMPLETE,
        }
        if self.phase is not completed_phase or completed_phase not in expected:
            raise LightGbmContractError("bootstrap phase transition is invalid")
        self.phase = expected[completed_phase]

    def resume_failed(self, *, query_key_sha256: str) -> None:
        """동일 failed chunk만 한 번 재개하며 성공 chunk는 재호출하지 않는다."""

        if (
            self.phase is not BootstrapPhase.FAILED
            or self.failed_query_key_sha256 != query_key_sha256
        ):
            raise LightGbmContractError("bootstrap resume target is not the failed chunk")
        if sum(not receipt.success for receipt in self.receipts) != 1:
            raise LightGbmContractError("bootstrap failed chunk retry budget is exhausted")
        provider = self.receipts[-1].provider
        self.phase = BootstrapPhase(provider)
        self.failed_query_key_sha256 = None


_T = TypeVar("_T")
