"""seed_export 의 계약 정합 회귀.

가장 중요한 것은 `canonical_json_bytes` 가 Owner 쪽 정의와 **바이트가 같은지**다.
Owner importer 가 `featureOrderSha256` 를 자기 정의로 재계산해 대조하므로, 이 함수가
어긋나면 exact-10 번들이 import 경계에서 조용히 거부된다. 워크스페이스가 달라
같은 모듈을 import 할 수 없어 정의를 복제했고, 그래서 잠금이 필요하다.
"""

import hashlib
import json
import struct

import pytest

from seed_export import (
    CONTRACT_FEATURE_ORDER,
    SIGNAL_DEADBAND,
    TARGET_COLUMN,
    TARGET_LOG_RETURN,
    TARGET_RAW_CLOSE,
    SeedExportError,
    canonical_json_bytes,
    classify_signal,
    load_universe,
)

# Owner `app.data._shared.canonical_json.canonical_json_bytes` 의 정의를 그대로 옮긴 기대값.
# 정렬 키, 구분자, ensure_ascii=False, 마지막 개행, -0.0 정규화까지 일치해야 한다.
_EXPECTED = (
    ({"b": 1, "a": 2}, b'{"a":2,"b":1}\n'),
    ({"a": [3, 1, 2]}, b'{"a":[3,1,2]}\n'),
    ({"a": "한글"}, '{"a":"한글"}\n'.encode()),
    ({"a": -0.0}, b'{"a":0}\n'),
    ({"a": 0.0}, b'{"a":0}\n'),
    ({"a": True, "b": None}, b'{"a":true,"b":null}\n'),
    ({"a": 1.5}, b'{"a":1.5}\n'),
    ([], b"[]\n"),
)


@pytest.mark.parametrize(("value", "expected"), _EXPECTED)
def test_canonical_json_matches_the_owner_definition(value: object, expected: bytes) -> None:
    assert canonical_json_bytes(value) == expected


def test_feature_order_hash_matches_the_owner_recomputation() -> None:
    """Owner 는 이 해시를 producer.featureOrderSha256 과 대조한다."""

    digest = hashlib.sha256(canonical_json_bytes(list(CONTRACT_FEATURE_ORDER))).hexdigest()
    # 계약 feature 순서가 바뀌면 이 값도 바뀐다. 순서 자체를 함께 잠근다.
    assert list(CONTRACT_FEATURE_ORDER) == [
        "open",
        "high",
        "low",
        "raw_close",
        "volume",
        "return_1d",
        "ma5",
        "ma20",
        "rsi14",
    ]
    assert len(digest) == 64
    assert digest == hashlib.sha256(canonical_json_bytes(list(CONTRACT_FEATURE_ORDER))).hexdigest()


def test_canonical_json_rejects_non_finite_numbers() -> None:
    for value in (float("inf"), float("-inf"), float("nan")):
        with pytest.raises(SeedExportError):
            canonical_json_bytes({"a": value})


def test_signal_deadband_matches_the_owner_rule() -> None:
    assert SIGNAL_DEADBAND == 0.005
    assert classify_signal(0.005) == "HOLD"
    assert classify_signal(-0.005) == "HOLD"
    assert classify_signal(0.0051) == "BUY"
    assert classify_signal(-0.0051) == "SELL"
    assert classify_signal(0.0) == "HOLD"


def test_target_column_mapping_is_closed() -> None:
    assert TARGET_COLUMN == {TARGET_RAW_CLOSE: "Close", TARGET_LOG_RETURN: "LogRet"}


def _catalog(symbols: list[dict[str, object]]) -> dict[str, object]:
    return {
        "contractId": "p1-return-universe.v1",
        "membershipMonth": "2026-08",
        "source": "test",
        "symbolCount": len(symbols),
        "symbols": symbols,
    }


def _exact31() -> list[dict[str, object]]:
    codes = [f"{index:06d}" for index in range(1, 31)] + ["132030"]
    return [
        {
            "isFixedMember": code == "132030",
            "market": "KOSPI",
            "rank": rank,
            "symbol": code,
            "yfinanceTicker": f"{code}.KS",
        }
        for rank, code in enumerate(codes, start=1)
    ]


def test_load_universe_accepts_exact31(tmp_path) -> None:
    path = tmp_path / "universe.json"
    path.write_text(json.dumps(_catalog(_exact31())), encoding="utf-8")
    specs = load_universe(path)
    assert len(specs) == 31
    assert [spec.rank for spec in specs] == list(range(1, 32))
    assert sum(1 for spec in specs if spec.symbol == "132030") == 1


def test_load_universe_fails_closed_on_bad_catalogs(tmp_path) -> None:
    short = _exact31()[:-1]
    duplicated = _exact31()
    duplicated[0] = dict(duplicated[0], symbol="132030")
    no_gold = [
        dict(item, symbol="000001") if item["symbol"] == "132030" else item for item in _exact31()
    ]
    cases = {
        "종목 30개": _catalog(short),
        "금 ETF 중복": _catalog(duplicated),
        "금 ETF 없음": _catalog(no_gold),
    }
    for label, payload in cases.items():
        path = tmp_path / f"{label}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(SeedExportError):
            load_universe(path)

    wrong_contract = tmp_path / "wrong.json"
    wrong_contract.write_text(
        json.dumps({**_catalog(_exact31()), "contractId": "other.v1"}), encoding="utf-8"
    )
    with pytest.raises(SeedExportError):
        load_universe(wrong_contract)


def test_safetensors_header_is_padded_to_eight_bytes() -> None:
    """Owner 는 header_length 를 그대로 믿고 데이터 구간이 빈틈 없이 이어지길 요구한다."""

    from seed_export import _safetensors_bytes

    class _Result:
        symbol = "005930"
        state_dict = {
            "lstm.weight_ih_l0": __import__("numpy").zeros((256, 9), dtype="<f4"),
            "lstm.weight_hh_l0": __import__("numpy").zeros((256, 64), dtype="<f4"),
            "lstm.bias_ih_l0": __import__("numpy").zeros((256,), dtype="<f4"),
            "lstm.bias_hh_l0": __import__("numpy").zeros((256,), dtype="<f4"),
            "fc.weight": __import__("numpy").zeros((1, 64), dtype="<f4"),
            "fc.bias": __import__("numpy").zeros((1,), dtype="<f4"),
        }

    content = _safetensors_bytes([_Result()])  # type: ignore[list-item]
    header_length = struct.unpack("<Q", content[:8])[0]
    assert header_length % 8 == 0
    header = json.loads(content[8 : 8 + header_length])
    metadata = header.pop("__metadata__")
    assert metadata["symbolCount"] == "1"
    assert set(header) == {
        "005930.weight_ih_l0",
        "005930.weight_hh_l0",
        "005930.bias_ih_l0",
        "005930.bias_hh_l0",
        "005930.head.weight",
        "005930.head.bias",
    }
    # 데이터 구간이 0부터 빈틈 없이 이어지고 파일 끝과 정확히 맞아야 한다.
    extents = sorted(tuple(item["data_offsets"]) for item in header.values())
    cursor = 0
    for start, end in extents:
        assert start == cursor
        cursor = end
    assert cursor == len(content) - 8 - header_length
    assert all(item["dtype"] == "F32" for item in header.values())
