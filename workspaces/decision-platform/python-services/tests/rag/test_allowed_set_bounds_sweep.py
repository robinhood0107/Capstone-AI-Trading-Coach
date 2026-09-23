"""허용 집합보다 작은 상한이 코드 어디에도 없는지 전수로 본다.

왜 있나
-------
같은 버그를 두 번 만났다. RAG `topics` 는 허용값이 6종인데 상한이 5로 적혀 있었고,
그래서 화면에서 주제를 **전부 고른 질문**이 거부됐다. 사용자에게는 "Agent 가 죽었다"로
보였다. Python 에서 고쳤더니 Kotlin 파서에 같은 것이 그대로 남아 있었다.

한 곳을 고치는 것으로는 끝나지 않는 부류다. 두 언어 모두 상한을 허용 집합에서 끌어오도록
바꿨지만, 누군가 다시 숫자를 적어 넣으면 아무도 모른다. 이 테스트가 그 자리다.

무엇을 보나
----------
상한 숫자와 허용 집합이 **같은 호출에 함께 적힌 곳**을 찾아, 상한이 허용 집합보다 작으면
실패한다. 값을 실행해 보는 것이 아니라 소스를 읽는다 - 그래야 도달하지 않는 경로까지 본다.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_APP = Path(__file__).resolve().parents[2] / "app"
_SPRING = Path(__file__).resolve().parents[3] / "spring-api/src/main/kotlin"


def _allowed_set_sizes(tree: ast.Module) -> dict[str, int]:
    """모듈 수준에서 `NAME = frozenset({...})` 꼴의 크기를 모은다."""

    sizes: dict[str, int] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value = node.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            if value.func.id not in {"frozenset", "set"} or not value.args:
                continue
            inner = value.args[0]
        elif isinstance(value, (ast.Set, ast.Tuple, ast.List)):
            inner = value
        else:
            continue
        if isinstance(inner, (ast.Set, ast.Tuple, ast.List)) and all(
            isinstance(item, ast.Constant) for item in inner.elts
        ):
            sizes[target.id] = len({item.value for item in inner.elts})
    return sizes


def test_no_python_call_bounds_an_allowed_set_below_its_size() -> None:
    offenders: list[str] = []
    for path in sorted(_APP.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - 문법이 깨진 파일은 다른 게이트가 잡는다
            continue
        sizes = _allowed_set_sizes(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            keywords = {item.arg: item.value for item in node.keywords if item.arg}
            bound, allowed = keywords.get("maximum"), keywords.get("allowed")
            if not isinstance(bound, ast.Constant) or not isinstance(bound.value, int):
                continue
            if not isinstance(allowed, ast.Name) or allowed.id not in sizes:
                continue
            if bound.value < sizes[allowed.id]:
                offenders.append(
                    f"{path.name}:{node.lineno} 상한 {bound.value} < 허용 "
                    f"{allowed.id} {sizes[allowed.id]}종"
                )
    assert not offenders, "허용 집합보다 작은 상한이 있다. '전부 선택'이 거부된다: " + "; ".join(
        offenders
    )


@pytest.mark.skipif(not _SPRING.is_dir(), reason="Kotlin 소스가 없는 환경")
def test_the_kotlin_array_bound_comes_from_its_allowed_set() -> None:
    """Kotlin 파서가 상한 숫자를 다시 적어 넣지 않았는지 본다.

    Kotlin 은 여기서 파싱하지 않는다. 대신 상한이 허용 집합에서 나온다는 그 한 줄이
    그대로 있는지 본다 - 누가 숫자로 되돌리면 이 테스트가 먼저 깨진다.
    """

    source = (_SPRING / "com/capstone/decision/api/rag/RagRequestParser.kt").read_text(
        encoding="utf-8"
    )
    assert re.search(r"val maximum = allowed\?\.size \?: \d+", source), (
        "RagRequestParser 의 배열 상한이 더 이상 허용 집합에서 나오지 않는다."
    )
    assert "node.size() > maximum" in source, "RagRequestParser 가 유도된 상한을 쓰지 않는다."
