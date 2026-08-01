from __future__ import annotations

import importlib.util
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest

from app.rag.safe_io import RagSafeIoError


REPO_ROOT = Path(__file__).resolve().parents[5]
GENERATOR_PATHS = {
    "s4_5": REPO_ROOT / "capstone-rag/generate_s4_5_evaluation.py",
    "s4_7b": REPO_ROOT / "capstone-rag/generate_s4_7b_source_cards.py",
    "s4_7c": REPO_ROOT / "capstone-rag/generate_s4_7c_external_corpus.py",
}


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


@dataclass(frozen=True)
class _GeneratorFixture:
    """한 generator의 isolated output tree와 deterministic expected bytes를 묶는다."""

    leaf: Path
    output_parent: Path
    expected: dict[Path, bytes]
    write: Callable[[], object]
    check: Callable[[], object]
    main: Callable[[], int]
    argv_module: ModuleType


def _load_generator(generator_id: str) -> ModuleType:
    generator_path = GENERATOR_PATHS[generator_id]
    module_name = f"_safe_write_test_{generator_id}_{os.getpid()}"
    spec = importlib.util.spec_from_file_location(module_name, generator_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return module


def _configure_generator(
    *,
    generator_id: str,
    approved_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> _GeneratorFixture:
    module = _load_generator(generator_id)
    artifact_root = approved_root / "artifacts"
    monkeypatch.setattr(module, "CAPSTONE_RAG_ROOT", approved_root, raising=False)

    if generator_id == "s4_5":
        manifest = artifact_root / "manifest.json"
        report = artifact_root / "report.json"
        provider_report = artifact_root / "provider.json"
        expected = {
            manifest: _json_bytes({"manifest": "s4-5"}),
            report: _json_bytes({"report": "s4-5"}),
            provider_report: _json_bytes({"provider": "offline"}),
        }
        monkeypatch.setattr(module, "S4_5_EVAL_MANIFEST_PATH", manifest)
        monkeypatch.setattr(module, "S4_5_REPORT_PATH", report)
        monkeypatch.setattr(module, "S4_5_PROVIDER_REPORT_PATH", provider_report)
        monkeypatch.setattr(module, "build_s4_5_manifest", lambda: {"manifest": "s4-5"})
        monkeypatch.setattr(
            module,
            "evaluate_s4_5_manifest",
            lambda _manifest: {"report": "s4-5"},
        )
        monkeypatch.setattr(
            module,
            "build_s4_5_provider_report",
            lambda: {"provider": "offline"},
        )
        return _GeneratorFixture(
            leaf=manifest,
            output_parent=artifact_root,
            expected=expected,
            write=module._write,
            check=module._check,
            main=module.main,
            argv_module=module,
        )

    card_root = artifact_root / "cards"
    manifest = artifact_root / "manifest.json"
    if generator_id == "s4_7b":
        generated_manifest = {
            "financeCards": 1,
            "officialCards": 0,
            "upstreamReferenceCardsExcluded": 0,
            "corpusManifestSha256": "a" * 64,
        }
        expected = {
            card_root / "card.md": b"# deterministic card\n",
            manifest: _json_bytes(generated_manifest),
        }
        monkeypatch.setattr(module, "S4_7B_SOURCE_CARD_ROOT", card_root)
        monkeypatch.setattr(module, "S4_7B_CORPUS_MANIFEST_PATH", manifest)
        monkeypatch.setattr(module, "render_cards", lambda: {"card.md": expected[card_root / "card.md"]})
        monkeypatch.setattr(
            module,
            "build_source_card_corpus_manifest",
            lambda: generated_manifest,
        )
        return _GeneratorFixture(
            leaf=card_root / "card.md",
            output_parent=card_root,
            expected=expected,
            write=module._write_artifacts,
            check=module._check_artifacts,
            main=module.main,
            argv_module=module,
        )

    generated_manifest = {
        "externalProcessingCardCount": 1,
        "corpusManifestSha256": "b" * 64,
    }
    expected = {
        card_root / "card.md": b"# deterministic card\n",
        manifest: _json_bytes(generated_manifest),
    }
    monkeypatch.setattr(module, "S4_7C_SOURCE_CARD_ROOT", card_root)
    monkeypatch.setattr(module, "S4_7C_CORPUS_MANIFEST_PATH", manifest)
    monkeypatch.setattr(
        module,
        "render_external_cards",
        lambda: {"card.md": expected[card_root / "card.md"]},
    )
    monkeypatch.setattr(
        module,
        "build_external_processing_manifest",
        lambda: generated_manifest,
    )
    return _GeneratorFixture(
        leaf=card_root / "card.md",
        output_parent=card_root,
        expected=expected,
        write=module._write,
        check=module._check,
        main=module.main,
        argv_module=module,
    )


@pytest.mark.parametrize("generator_id", sorted(GENERATOR_PATHS))
def test_generators_keep_regular_file_write_and_check_byte_parity(
    generator_id: str,
    posix_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved_root = posix_tmp_path / "capstone-rag"
    approved_root.mkdir()
    fixture = _configure_generator(
        generator_id=generator_id,
        approved_root=approved_root,
        monkeypatch=monkeypatch,
    )
    fixture.output_parent.mkdir(parents=True)

    write_args = [] if generator_id == "s4_5" else ["--write"]
    monkeypatch.setattr(fixture.argv_module.sys, "argv", ["generator", *write_args])
    assert fixture.main() == 0
    for path, expected in fixture.expected.items():
        assert path.read_bytes() == expected

    monkeypatch.setattr(fixture.argv_module.sys, "argv", ["generator", "--check"])
    assert fixture.main() == 0
    fixture.check()


@pytest.mark.parametrize("generator_id", sorted(GENERATOR_PATHS))
@pytest.mark.parametrize("unsafe_kind", ("root_symlink", "parent_symlink", "leaf_symlink", "directory", "hardlink"))
def test_generators_reject_unsafe_output_paths_without_mutating_outside_sentinel(
    generator_id: str,
    unsafe_kind: str,
    posix_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outside = posix_tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"outside sentinel must not change")
    approved_root = posix_tmp_path / "capstone-rag"

    if unsafe_kind == "root_symlink":
        os.symlink(outside, approved_root)
    else:
        approved_root.mkdir()

    fixture = _configure_generator(
        generator_id=generator_id,
        approved_root=approved_root,
        monkeypatch=monkeypatch,
    )
    if unsafe_kind == "parent_symlink":
        os.symlink(outside, approved_root / "artifacts")
    elif unsafe_kind in {"leaf_symlink", "directory", "hardlink"}:
        fixture.output_parent.mkdir(parents=True)
        if unsafe_kind == "leaf_symlink":
            os.symlink(sentinel, fixture.leaf)
        elif unsafe_kind == "directory":
            fixture.leaf.mkdir()
        else:
            os.link(sentinel, fixture.leaf)

    before = sentinel.read_bytes()
    try:
        fixture.write()
    except RagSafeIoError:
        rejected = True
    else:
        rejected = False

    assert sentinel.read_bytes() == before
    assert rejected
