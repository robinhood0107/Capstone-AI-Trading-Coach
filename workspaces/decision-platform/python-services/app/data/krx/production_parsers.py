"""S5.6 KRX index/base-info/ETF의 closed allowlisted projections."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Final, cast

from app.data.krx.catalog import S5_PRODUCTION_ENDPOINTS
from app.lightgbm.errors import DatasetUnavailable, LightGbmContractError

S5_PRODUCTION_PROJECTION_FIELDS: Final[dict[str, frozenset[str]]] = {
    "stk_bydd_trd": frozenset(
        {
            "BAS_DD",
            "ISU_CD",
            "ISU_NM",
            "MKT_NM",
            "SECT_TP_NM",
            "TDD_CLSPRC",
            "CMPPREVDD_PRC",
            "FLUC_RT",
            "TDD_OPNPRC",
            "TDD_HGPRC",
            "TDD_LWPRC",
            "ACC_TRDVOL",
            "ACC_TRDVAL",
            "MKTCAP",
            "LIST_SHRS",
        }
    ),
    "ksq_bydd_trd": frozenset(
        {
            "BAS_DD",
            "ISU_CD",
            "ISU_NM",
            "MKT_NM",
            "SECT_TP_NM",
            "TDD_CLSPRC",
            "CMPPREVDD_PRC",
            "FLUC_RT",
            "TDD_OPNPRC",
            "TDD_HGPRC",
            "TDD_LWPRC",
            "ACC_TRDVOL",
            "ACC_TRDVAL",
            "MKTCAP",
            "LIST_SHRS",
        }
    ),
    "kospi_dd_trd": frozenset(
        {
            "BAS_DD",
            "IDX_CLSS",
            "IDX_NM",
            "CLSPRC_IDX",
            "CMPPREVDD_IDX",
            "FLUC_RT",
            "OPNPRC_IDX",
            "HGPRC_IDX",
            "LWPRC_IDX",
            "ACC_TRDVOL",
            "ACC_TRDVAL",
            "MKTCAP",
        }
    ),
    "kosdaq_dd_trd": frozenset(
        {
            "BAS_DD",
            "IDX_CLSS",
            "IDX_NM",
            "CLSPRC_IDX",
            "CMPPREVDD_IDX",
            "FLUC_RT",
            "OPNPRC_IDX",
            "HGPRC_IDX",
            "LWPRC_IDX",
            "ACC_TRDVOL",
            "ACC_TRDVAL",
            "MKTCAP",
        }
    ),
    "stk_isu_base_info": frozenset(
        {
            "ISU_CD",
            "ISU_SRT_CD",
            "ISU_NM",
            "ISU_ABBRV",
            "ISU_ENG_NM",
            "LIST_DD",
            "MKT_TP_NM",
            "SECUGRP_NM",
            "SECT_TP_NM",
            "KIND_STKCERT_TP_NM",
            "PARVAL",
            "LIST_SHRS",
        }
    ),
    "ksq_isu_base_info": frozenset(
        {
            "ISU_CD",
            "ISU_SRT_CD",
            "ISU_NM",
            "ISU_ABBRV",
            "ISU_ENG_NM",
            "LIST_DD",
            "MKT_TP_NM",
            "SECUGRP_NM",
            "SECT_TP_NM",
            "KIND_STKCERT_TP_NM",
            "PARVAL",
            "LIST_SHRS",
        }
    ),
    "etf_bydd_trd": frozenset(
        {
            "BAS_DD",
            "ISU_CD",
            "ISU_NM",
            "TDD_CLSPRC",
            "CMPPREVDD_PRC",
            "FLUC_RT",
            "NAV",
            "TDD_OPNPRC",
            "TDD_HGPRC",
            "TDD_LWPRC",
            "ACC_TRDVOL",
            "ACC_TRDVAL",
            "MKTCAP",
            "INVSTASST_NETASST_TOTAMT",
            "LIST_SHRS",
            "IDX_IND_NM",
            "OBJ_STKPRC_IDX",
            "CMPPREVDD_IDX",
            "FLUC_RT_IDX",
        }
    ),
}


def parse_s5_production_response(
    payload: Mapping[str, object], *, service: str, requested_date: date
) -> tuple[dict[str, str], ...]:
    """공식 field set과 requested date를 전수 확인한 후 string projection만 반환한다."""

    endpoint = S5_PRODUCTION_ENDPOINTS.get(service)
    fields = S5_PRODUCTION_PROJECTION_FIELDS.get(service)
    if endpoint is None or fields is None or type(requested_date) is not date:
        raise LightGbmContractError("KRX S5 production service is not allowlisted")
    if set(payload) != {endpoint.response_block}:
        raise LightGbmContractError("KRX S5 response envelope is invalid")
    rows = payload[endpoint.response_block]
    if not isinstance(rows, list) or not rows or len(rows) > 5_000:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: KRX S5 response rows are unavailable")
    result: list[dict[str, str]] = []
    expected_date = requested_date.strftime("%Y%m%d")
    identity_field = (
        "ISU_SRT_CD"
        if service.endswith("isu_base_info")
        else (
            "IDX_NM"
            if service.endswith("dd_trd") and service.startswith(("kospi", "kosdaq"))
            else "ISU_CD"
        )
    )
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, Mapping) or set(raw) != fields:
            raise LightGbmContractError("KRX S5 row field set is invalid")
        if any(not isinstance(raw[field], str) for field in fields):
            raise LightGbmContractError("KRX S5 row values must be strings")
        row = dict(cast(Mapping[str, str], raw))
        if "BAS_DD" in row and row["BAS_DD"] != expected_date:
            raise LightGbmContractError("KRX S5 row date does not match request")
        if service.endswith("isu_base_info"):
            list_date = row["LIST_DD"]
            if list_date and _parse_provider_date(list_date) > requested_date:
                raise LightGbmContractError("KRX base-info listing date is in the future")
        identity = row[identity_field]
        if not identity or identity in seen:
            raise LightGbmContractError("KRX S5 response identity is empty or duplicated")
        seen.add(identity)
        result.append(row)
    return tuple(sorted(result, key=lambda row: row[identity_field]))


def _parse_provider_date(value: str) -> date:
    try:
        parsed = datetime.strptime(value, "%Y/%m/%d").date()
    except ValueError:
        try:
            parsed = datetime.strptime(value, "%Y%m%d").date()
        except ValueError:
            raise LightGbmContractError("KRX provider date is invalid") from None
    return parsed
