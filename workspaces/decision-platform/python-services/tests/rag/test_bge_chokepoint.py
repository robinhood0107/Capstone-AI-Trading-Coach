"""BGE 모델을 여는 길이 하나이고, 그 길이 닫혀 있는지 고정한다.

왜 있나
-------
BGE 차단을 진입점마다 가드로 막으면 새 진입점이 생길 때마다 빠뜨린다. 실제로 그렇게
빠뜨린 호출자가 둘 있었다(`rag_v2_grpc_server`, `content_cli`). 그래서 가드를 ONNX
세션을 여는 단 한 곳으로 옮겼다.

이 테스트는 그 구조를 고정한다. 관문이 닫혀 있는지, 그리고 **모델을 여는 다른 길이
생기지 않았는지** 둘 다 본다. 뒤쪽이 없으면 누군가 `BgeOnnxEmbedder` 를 직접 만들어
관문을 우회해도 아무도 모른다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.rag.bge_runtime import BGE_ENABLED_ENV, BgeRuntimeError, bge_enabled, load_bge_onnx_embedder

_APP_ROOT = Path(__file__).resolve().parents[2] / "app"


def test_the_loader_refuses_while_bge_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(BGE_ENABLED_ENV, raising=False)
    assert bge_enabled() is False
    with pytest.raises(BgeRuntimeError, match="BGE_DISABLED"):
        # 패킷이 없는 경로를 준다. 가드가 먼저 서므로 경로는 읽히지도 않는다.
        load_bge_onnx_embedder(Path("/nonexistent/bge-packet"))


@pytest.mark.parametrize("value", ["", "0", "false", "no", " "])
def test_only_an_explicit_one_opens_the_gate(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(BGE_ENABLED_ENV, value)
    assert bge_enabled() is False
    with pytest.raises(BgeRuntimeError, match="BGE_DISABLED"):
        load_bge_onnx_embedder(Path("/nonexistent/bge-packet"))


def test_the_embedder_is_constructed_in_exactly_one_place() -> None:
    """관문을 지나지 않고 embedder 를 만드는 두 번째 길이 생기면 실패한다."""

    pattern = re.compile(r"\bBgeOnnxEmbedder\s*\(")
    sites = [
        f"{path.relative_to(_APP_ROOT)}:{index}"
        for path in sorted(_APP_ROOT.rglob("*.py"))
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if pattern.search(line)
    ]
    assert len(sites) == 1 and sites[0].startswith("rag/bge_runtime.py:"), (
        "BgeOnnxEmbedder 를 만드는 곳은 관문 하나여야 한다. 지금: " + ", ".join(sites)
    )
