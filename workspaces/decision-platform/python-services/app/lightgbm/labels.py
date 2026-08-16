"""S5.2 exact t+1..t+6 adjusted-open label과 missing-boundary 처리."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import math
from typing import Mapping, Sequence

import numpy as np

from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.features import PriceEvidence, ProductionPriceEvidence
from app.lightgbm.temporal import label_as_of, require_receipt_eligible


LABEL_THRESHOLD = 0.006
CLASS_ORDER = {"SELL": 0, "HOLD": 1, "BUY": 2}


@dataclass(frozen=True)
class LabelRow:
    """feature session t와 양 끝 포함 label information interval을 결합한다."""

    symbol: str
    session_date: date
    interval_start: date
    interval_end: date
    forward_return: float
    label: int


def classify_forward_return(value: float) -> int:
    """양쪽 tau equality를 HOLD로 보존하는 exact 3-class label을 반환한다."""

    if not math.isfinite(value):
        raise LightGbmContractError("forward return must be finite")
    if value < -LABEL_THRESHOLD:
        return CLASS_ORDER["SELL"]
    if value > LABEL_THRESHOLD:
        return CLASS_ORDER["BUY"]
    return CLASS_ORDER["HOLD"]


def build_exact_labels(prices: Sequence[PriceEvidence]) -> tuple[LabelRow, ...]:
    """symbol별 `adjustedOpen[t+6]/adjustedOpen[t+1]-1`을 만들고 boundary missing row를 drop한다."""

    by_identity: dict[str, list[PriceEvidence]] = {}
    for row in prices:
        by_identity.setdefault(row.instrument_id, []).append(row)
    output: list[LabelRow] = []
    for _, identity_rows in sorted(by_identity.items()):
        ordered = sorted(identity_rows, key=lambda row: row.session_date)
        if len({row.session_date for row in ordered}) != len(ordered):
            raise LightGbmContractError("label input has duplicate symbol-session rows")
        for index in range(len(ordered) - 6):
            open_t1 = ordered[index + 1].adjusted_open
            open_t6 = ordered[index + 6].adjusted_open
            if open_t1 is None or open_t6 is None:
                continue
            if (
                not math.isfinite(open_t1)
                or not math.isfinite(open_t6)
                or open_t1 <= 0
                or open_t6 <= 0
            ):
                continue
            forward_return = open_t6 / open_t1 - 1.0
            output.append(
                LabelRow(
                    symbol=ordered[index].symbol,
                    session_date=ordered[index].session_date,
                    interval_start=ordered[index + 1].session_date,
                    interval_end=ordered[index + 6].session_date,
                    forward_return=forward_return,
                    label=classify_forward_return(forward_return),
                )
            )
    return tuple(sorted(output, key=lambda row: (row.session_date, row.symbol)))


def build_production_exact_labels(
    prices: Sequence[ProductionPriceEvidence], *, dataset_cutoff: datetime
) -> tuple[LabelRow, ...]:
    """t+6 maturity clock과 TemporalReceipt를 강제한 production label builder."""

    if dataset_cutoff.tzinfo is None:
        raise LightGbmContractError("production label cutoff must be timezone aware")
    by_identity: dict[str, list[ProductionPriceEvidence]] = {}
    for row in prices:
        by_identity.setdefault(row.instrument_id, []).append(row)
    output: list[LabelRow] = []
    for identity_rows in by_identity.values():
        ordered = sorted(identity_rows, key=lambda row: row.session_date)
        if len({row.session_date for row in ordered}) != len(ordered):
            raise LightGbmContractError("production label input has duplicate sessions")
        for index in range(len(ordered) - 6):
            t1 = ordered[index + 1]
            t6 = ordered[index + 6]
            open_t1 = t1.adjusted_open
            open_t6 = t6.adjusted_open
            if open_t1 is None or open_t6 is None:
                continue
            if not all(math.isfinite(value) and value > 0 for value in (open_t1, open_t6)):
                continue
            maturity_clock = label_as_of(t6.session_date)
            for receipt in (t1.receipt, t6.receipt):
                require_receipt_eligible(
                    receipt,
                    row_clock=maturity_clock,
                    dataset_cutoff=dataset_cutoff,
                )
            forward_return = open_t6 / open_t1 - 1.0
            output.append(
                LabelRow(
                    symbol=ordered[index].symbol,
                    session_date=ordered[index].session_date,
                    interval_start=t1.session_date,
                    interval_end=t6.session_date,
                    forward_return=forward_return,
                    label=classify_forward_return(forward_return),
                )
            )
    return tuple(sorted(output, key=lambda row: (row.session_date, row.symbol)))


def zero_fill_features(values: Mapping[str, float | None]) -> dict[str, np.float32]:
    """S5.2가 허용한 forward fill 0을 과거/미래 row 참조 없이 현재 nullable feature에만 적용한다."""

    output: dict[str, np.float32] = {}
    for name, value in values.items():
        if value is not None and not math.isfinite(value):
            raise LightGbmContractError("feature fill input must be finite or null")
        output[name] = np.float32(0.0 if value is None else value)
    return output
