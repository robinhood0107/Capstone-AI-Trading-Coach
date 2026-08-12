from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.rag.pre_s5_fresh_namespace import (
    FreshNamespaceError,
    FreshNamespacePaths,
    initialize_fresh_namespace,
)


def test_initializer_creates_distinct_0700_output_and_0600_fresh_env_without_secret_receipt(
    tmp_path: Path,
) -> None:
    secret_root = tmp_path / "secrets"
    runtime_root = tmp_path / "runtime"
    source_root = runtime_root / "local-corpus"
    output_root = runtime_root / "pre-s5-fresh" / "local-corpus"
    secret_root.mkdir(mode=0o700)
    runtime_root.mkdir(mode=0o700)
    source_root.mkdir(mode=0o700)
    source_env = secret_root / "docker-compose-rag.env"
    source_env.write_text(_source_environment(), encoding="utf-8")
    source_env.chmod(0o600)
    target_env = secret_root / "docker-compose-pre-s5-fresh.env"

    receipt = initialize_fresh_namespace(
        FreshNamespacePaths(
            source_env=source_env,
            target_env=target_env,
            source_root=source_root,
            output_root=output_root,
        )
    )

    assert oct(output_root.stat().st_mode & 0o777) == "0o700"
    assert oct(target_env.stat().st_mode & 0o777) == "0o600"
    assert target_env.stat().st_nlink == 1
    values = _parse_environment(target_env)
    assert values["CAPSTONE_PRE_S5_COMPOSE_PROJECT"] == "capstone-pre-s5-fresh"
    assert values["CAPSTONE_RAG_SOURCE_ROOT"] == str(source_root)
    assert values["CAPSTONE_RAG_OUTPUT_ROOT"] == str(output_root)
    assert "CAPSTONE_RAG_LOCAL_ROOT" not in values
    assert values["POSTGRES_HOST"] == "127.0.0.1"
    assert values["POSTGRES_HOST_PORT"] == "55432"
    assert values["REDIS_HOST_PORT"] == "56379"
    assert "@127.0.0.1:55432/" in values["CAPSTONE_RAG_WRITER_DATABASE_DSN"]
    assert "secret-value" not in repr(receipt)
    assert receipt.provider_calls == 0
    assert receipt.bge_embedding_inference_calls == 0


@pytest.mark.parametrize("hostile", ["source_symlink", "target_hardlink", "existing_output"])
def test_initializer_rejects_reuse_or_linked_boundaries_without_overwrite(
    tmp_path: Path,
    hostile: str,
) -> None:
    secret_root = tmp_path / "secrets"
    runtime_root = tmp_path / "runtime"
    source_root = runtime_root / "local-corpus"
    output_root = runtime_root / "pre-s5-fresh" / "local-corpus"
    secret_root.mkdir(mode=0o700)
    runtime_root.mkdir(mode=0o700)
    source_root.mkdir(mode=0o700)
    source_env = secret_root / "docker-compose-rag.env"
    source_env.write_text(_source_environment(), encoding="utf-8")
    source_env.chmod(0o600)
    target_env = secret_root / "docker-compose-pre-s5-fresh.env"
    if hostile == "source_symlink":
        actual = secret_root / "actual.env"
        source_env.rename(actual)
        source_env.symlink_to(actual)
    elif hostile == "target_hardlink":
        sentinel = secret_root / "sentinel"
        sentinel.write_text("unchanged", encoding="utf-8")
        os.link(sentinel, target_env)
    else:
        output_root.mkdir(parents=True, mode=0o700)

    with pytest.raises(FreshNamespaceError, match="PRE_S5_FRESH_NAMESPACE_BOUNDARY"):
        initialize_fresh_namespace(
            FreshNamespacePaths(
                source_env=source_env,
                target_env=target_env,
                source_root=source_root,
                output_root=output_root,
            )
        )

    if hostile == "target_hardlink":
        assert target_env.read_text(encoding="utf-8") == "unchanged"


def _source_environment() -> str:
    return "\n".join(
        (
            "POSTGRES_DB=trading",
            "POSTGRES_ADMIN_PASSWORD=secret-value-admin",
            "POSTGRES_APP_PASSWORD=secret-value-app",
            "POSTGRES_MIGRATION_PASSWORD=secret-value-migration",
            "POSTGRES_COLLECTOR_PASSWORD=secret-value-collector",
            "POSTGRES_DISCLOSURE_READER_PASSWORD=secret-value-disclosure",
            "POSTGRES_MARKET_WRITER_PASSWORD=secret-value-market",
            "POSTGRES_PORTFOLIO_WRITER_PASSWORD=secret-value-portfolio",
            "POSTGRES_RISK_WRITER_PASSWORD=secret-value-risk",
            "POSTGRES_FILL_WRITER_PASSWORD=secret-value-fill",
            "POSTGRES_RAG_WRITER_PASSWORD=secret-value-writer",
            "POSTGRES_RAG_ADMIN_PASSWORD=secret-value-rag-admin",
            "POSTGRES_RAG_QUERY_PASSWORD=secret-value-query",
            "REDIS_PASSWORD=secret-value-redis",
            "CAPSTONE_RAG_LOCAL_ROOT=/legacy/root",
            "",
        )
    )


def _parse_environment(path: Path) -> dict[str, str]:
    return {
        key: value
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
        for key, value in (line.split("=", 1),)
    }
