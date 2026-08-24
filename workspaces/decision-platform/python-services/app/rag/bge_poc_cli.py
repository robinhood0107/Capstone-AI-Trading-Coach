from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence

from app.rag.bge_acquisition import (
    DEFAULT_MODEL_MANIFEST,
    DEFAULT_MODEL_ROOT,
    BgeAcquisitionError,
    verify_bge_completion_manifest,
)
from app.rag.bge_artifact import BgeArtifactError
from app.rag.bge_poc import (
    BgePocError,
    PsycopgBgePocRepository,
    execute_bge_poc,
    prepare_bge_poc,
)
from app.rag.bge_runtime import (
    BgeRuntimeError,
    BgeStaticTokenizer,
    load_bge_onnx_embedder,
)
from app.rag.official_evidence import (
    OfficialEvidenceError,
    validate_official_evidence_manifest,
)
from app.rag.source_card import (
    OFFICIAL_SOURCE_CARD_ROOT,
    RagSourceCardError,
    load_rag_source_cards,
)

_DATABASE_DSN_ENV = "RAG_SOURCE_WRITER_DATABASE_DSN"
_CARD_PATHS = (
    "src_project_ecos_pit_availability_001.md",
    "src_project_gold_futures_etf_132030_001.md",
    "src_project_kis_adjusted_price_001.md",
    "src_project_krx_service_coverage_001.md",
    "src_project_opendart_status_quota_001.md",
)


def main(argv: Sequence[str] | None = None) -> int:
    """fixed local model/card roots에서만 S4.2A PoC를 실행하고 bounded receipt를 출력한다."""

    parser = argparse.ArgumentParser(
        description="Materialize the exact S4.2A official five-card BGE PoC.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    database_dsn = os.environ.get(_DATABASE_DSN_ENV, "").strip()
    if not database_dsn:
        print(f"S4_2A_BGE_POC_FAILED:{_DATABASE_DSN_ENV}_MISSING")
        return 2
    try:
        artifact = verify_bge_completion_manifest(
            DEFAULT_MODEL_ROOT,
            manifest_path=DEFAULT_MODEL_MANIFEST,
        )
        official_evidence = validate_official_evidence_manifest()
        cards = load_rag_source_cards(
            approved_root=OFFICIAL_SOURCE_CARD_ROOT,
            relative_paths=_CARD_PATHS,
        )
        tokenizer = BgeStaticTokenizer.from_file(DEFAULT_MODEL_ROOT / "onnx/tokenizer.json")
        plan = prepare_bge_poc(
            cards=cards,
            tokenizer=tokenizer,
            artifact=artifact,
            official_evidence=official_evidence,
        )
        receipt = execute_bge_poc(
            plan=plan,
            embedder=load_bge_onnx_embedder(DEFAULT_MODEL_ROOT),
            repository=PsycopgBgePocRepository(database_dsn=database_dsn),
        )
    except (
        BgeAcquisitionError,
        BgeArtifactError,
        BgePocError,
        BgeRuntimeError,
        OfficialEvidenceError,
        RagSourceCardError,
    ) as error:
        print(f"S4_2A_BGE_POC_FAILED:{error}")
        return 2
    payload = {
        "activePointerChanged": receipt.active_pointer_changed,
        "artifactManifestSha256": plan.artifact_manifest_sha256,
        "corpusHash": plan.corpus_hash,
        "finalRowCount": receipt.final_row_count,
        "generationHash": plan.generation_hash,
        "generationId": receipt.generation_id,
        "materializationRunId": receipt.materialization_run_id,
        "modelRevision": plan.model_revision,
        "status": receipt.status,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(
            "S4_2A_BGE_POC_EVAL_PASSED "
            f"generation={receipt.generation_id} rows={receipt.final_row_count}"
        )
    return 0
