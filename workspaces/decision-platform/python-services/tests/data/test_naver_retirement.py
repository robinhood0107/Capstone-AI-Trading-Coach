from __future__ import annotations

from pathlib import Path

PYTHON_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[5]


def test_active_naver_runtime_contract_and_credentials_are_removed() -> None:
    """퇴역된 Naver source가 실행·credential·active 계약 경계에 남지 않게 고정한다."""

    removed_paths = (
        PYTHON_ROOT / "app/data/naver",
        PYTHON_ROOT / "tests/data/naver",
        PYTHON_ROOT / "tests/fixtures/naver",
        REPO_ROOT / "contracts/schemas/naver_news_metadata_snapshot.schema.json",
        REPO_ROOT / "contracts/examples/naver_news_metadata_snapshot.valid.json",
        REPO_ROOT / "contracts/examples/naver_news_metadata_snapshot.one_query.valid.json",
        REPO_ROOT / "contracts/examples/pairs/naver_snapshot_manifest.one_query.valid.json",
        REPO_ROOT
        / "contracts/examples/pairs/naver_snapshot_manifest.query_count_mismatch.invalid.json",
    )
    assert all(not path.exists() for path in removed_paths)

    pyproject = (PYTHON_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    validator = (REPO_ROOT / "contracts/validate.py").read_text(encoding="utf-8")
    manifest_schema = (
        REPO_ROOT / "contracts/schemas/source_snapshot_manifest.schema.json"
    ).read_text(encoding="utf-8")
    retention_cli = (PYTHON_ROOT / "app/data/source_snapshot_retention_cli.py").read_text(
        encoding="utf-8"
    )
    shared_models = (PYTHON_ROOT / "app/data/_shared/source_snapshot_models.py").read_text(
        encoding="utf-8"
    )
    secure_storage = (PYTHON_ROOT / "app/data/_shared/secure_snapshot_storage.py").read_text(
        encoding="utf-8"
    )

    assert "naver-news-metadata-collect" not in pyproject
    assert "NAVER_" not in env_example
    assert "validate_naver_pair_examples" not in validator
    assert '"naver"' not in manifest_schema
    assert "app.data.naver" not in retention_cli
    assert "Naver" not in shared_models
    assert "naver" not in secure_storage.casefold()


def test_historical_boundary_card_and_supersession_record_are_preserved() -> None:
    """퇴역은 과거 감사와 project-authored 정책 경계 source card를 삭제하지 않는다."""

    preserved_paths = (
        REPO_ROOT
        / "capstone-rag/source-cards/s4-7b/src_project_naver_news_discovery_boundary_001.md",
        REPO_ROOT / "contracts/changes/20260714-s1-3-ecos-naver-source-snapshots.md",
        REPO_ROOT / "contracts/changes/20260731-s1-3g-naver-retirement-gdelt-aggregate-lock.md",
        REPO_ROOT / "docs/adr/ADR-038-naver-retirement-gdelt-aggregate.md",
        REPO_ROOT / "contracts/examples/rag-source-card-v2.naver-official.valid.json",
    )
    assert all(path.is_file() for path in preserved_paths)

    authority = (
        REPO_ROOT / "contracts/changes/20260731-s1-3g-naver-retirement-gdelt-aggregate-lock.md"
    ).read_text(encoding="utf-8")
    assert "NAVER_ACTIVE_PROVIDER_RUNTIME_STORAGE=RETIRED" in authority
