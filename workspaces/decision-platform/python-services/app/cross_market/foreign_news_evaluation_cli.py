"""SENTiVENT gold + TFNS stress local evaluation의 one-shot operator CLI다.

실행은 ignored ``capstone-rag/runtime/finbert-eval`` 아래의 local models/datasets만 사용한다.
provider socket, model/dataset download, raw text/label/prediction 출력은 없고, one-shot receipt가
생긴 뒤에는 동일 cache에서 test evaluation을 반복하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from app.cross_market.foreign_news import ForeignNewsModelSelectionError, ForeignNewsSelectionRun
from app.cross_market.foreign_news_evaluator import ForeignNewsEvaluationExample, ForeignNewsLocalCandidate
from app.cross_market.foreign_news_local_evaluation import (
    DEFAULT_FINBERT_EVALUATION_ROOT,
    ForeignNewsDatasetReceipt,
    ForeignNewsLoadedExamples,
    ForeignNewsLocalEvaluationError,
    ForeignNewsLocalEvaluationInputs,
    ForeignNewsLocalSelectionResult,
    ForeignNewsModelArtifactReceipt,
    build_local_model_candidates,
    load_sentivent_gold_split,
    load_tfns_stress_split,
    run_local_model_selection,
)


_RECEIPT_NAME: Final[str] = "sentivent-gold-plus-tfns-stress.v1.json"
_LOCK_NAME: Final[str] = ".sentivent-gold-plus-tfns-stress.lock"
_RECEIPT_CONTRACT_ID: Final[str] = "foreign-news-local-evaluation-receipt-v1"
_TEST_RESERVATION_NAME: Final[str] = ".sentivent-gold-plus-tfns-stress.test-reservation.v1.json"
_TEST_RESERVATION_CONTRACT_ID: Final[str] = "foreign-news-local-evaluation-test-reservation-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ForeignNewsEvaluationCliError(ValueError):
    """operator receipt/control boundary가 깨졌음을 content-free marker로 나타낸다."""


def main(argv: Sequence[str] | None = None) -> int:
    """argument-free local evaluation만 허용한다. path/credential/raw input argv는 없다."""

    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if arguments != ("evaluate",):
        _emit({"code": "FOREIGN_NEWS_EVALUATION_COMMAND_INVALID", "state": "FAILED"})
        return 2
    try:
        _emit(_evaluate_once(evaluation_root=DEFAULT_FINBERT_EVALUATION_ROOT))
    except (
        ForeignNewsEvaluationCliError,
        ForeignNewsLocalEvaluationError,
        ForeignNewsModelSelectionError,
    ):
        _emit({"code": "FOREIGN_NEWS_LOCAL_EVALUATION_FAILED", "state": "FAILED"})
        return 2
    return 0


def load_verified_selected_local_candidate(
    *,
    evaluation_root: Path = DEFAULT_FINBERT_EVALUATION_ROOT,
) -> ForeignNewsLocalCandidate:
    """single blind-test를 통과한 local model만 foreign-news runtime analyzer로 연다.

    Validation ABSTAIN, failed test, malformed receipt는 model/dataset download 없이 typed failure로
    끝낸다. Runtime은 receipt의 aggregate-only proof와 same ignored cache의 local model만 사용한다.
    """

    root_stat = evaluation_root.lstat()
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or stat.S_ISLNK(root_stat.st_mode)
        or stat.S_IMODE(root_stat.st_mode) != 0o700
    ):
        raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_RUNTIME_MODEL_NOT_VERIFIED")
    payload = _load_receipt_if_present(evaluation_root / "receipts" / _RECEIPT_NAME)
    if payload is None:
        raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_RUNTIME_MODEL_NOT_VERIFIED")
    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_RUNTIME_MODEL_NOT_VERIFIED")
    selection = result.get("selection")
    if not isinstance(selection, Mapping):
        raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_RUNTIME_MODEL_NOT_VERIFIED")
    selected_model = selection.get("selectedModel")
    if (
        selection.get("selectionStatus") != "TEST_EVALUATED"
        or selection.get("testEvaluationCount") != 1
        or selection.get("testOutcome") != "PASSED"
        or selected_model
        not in {
            "PROSUSAI_FINBERT",
            "YIYANGHKUST_FINBERT_TONE",
            "LOUGHRAN_MCDONALD_BASELINE",
        }
    ):
        raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_RUNTIME_MODEL_NOT_VERIFIED")
    candidates, model_artifacts = build_local_model_candidates(evaluation_root=evaluation_root)
    current_model_artifacts = [artifact.to_payload() for artifact in model_artifacts]
    expected_input_digest = _sha256(
        _canonical(
            {
                "contractId": _RECEIPT_CONTRACT_ID,
                "modelArtifacts": current_model_artifacts,
                "sentiventValidation": payload.get("sentiventValidation"),
            }
        )
    )
    if (
        payload.get("modelArtifacts") != current_model_artifacts
        or payload.get("evaluationInputDigest") != expected_input_digest
    ):
        # 합격 뒤 weights/tokenizer/config가 바뀌면 과거 blind-test proof를 현재 runtime에 재사용하지 않는다.
        raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_RUNTIME_MODEL_NOT_VERIFIED")
    candidate = next((item for item in candidates if item.candidate_model == selected_model), None)
    if candidate is None:
        raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_RUNTIME_MODEL_NOT_VERIFIED")
    return candidate


def _evaluate_once(*, evaluation_root: Path) -> dict[str, object]:
    receipt_root = _ensure_receipt_root(evaluation_root)
    receipt_path = receipt_root / _RECEIPT_NAME
    reservation_path = receipt_root / _TEST_RESERVATION_NAME
    existing = _load_receipt_if_present(receipt_path)
    if existing is not None:
        return _summary(existing, code="FOREIGN_NEWS_LOCAL_EVALUATION_REUSED", state="REUSED")
    _fail_if_test_reservation_exists(reservation_path)

    lock_descriptor, lock_identity = _acquire_lock(receipt_root / _LOCK_NAME)
    try:
        # lock 획득 뒤에 다시 확인해 concurrent operator가 쓴 receipt를 재사용한다.
        existing = _load_receipt_if_present(receipt_path)
        if existing is not None:
            return _summary(existing, code="FOREIGN_NEWS_LOCAL_EVALUATION_REUSED", state="REUSED")
        _fail_if_test_reservation_exists(reservation_path)
        validation = load_sentivent_gold_split(dataset_root=evaluation_root / "sentivent", split="validation")
        candidates, model_artifacts = build_local_model_candidates(evaluation_root=evaluation_root)
        input_digest = _input_digest(validation.receipt, model_artifacts)
        blind_test: ForeignNewsLoadedExamples | None = None
        tfns_stress: ForeignNewsLoadedExamples | None = None
        test_reservation_identity: tuple[int, int] | None = None

        def load_blind_test() -> Sequence[ForeignNewsEvaluationExample]:
            nonlocal blind_test
            blind_test = load_sentivent_gold_split(
                dataset_root=evaluation_root / "sentivent",
                split="test",
            )
            return blind_test.examples

        def load_tfns_stress() -> Sequence[ForeignNewsEvaluationExample]:
            nonlocal tfns_stress
            tfns_stress = load_tfns_stress_split(
                dataset_root=evaluation_root / "twitter-financial-news-sentiment",
            )
            return tfns_stress.examples

        def reserve_selected_test(selection: ForeignNewsSelectionRun) -> None:
            nonlocal test_reservation_identity
            test_reservation_identity = _write_test_reservation(
                reservation_path,
                evaluation_input_digest=input_digest,
                selection=selection,
            )

        result = run_local_model_selection(
            inputs=ForeignNewsLocalEvaluationInputs(
                candidates=candidates,
                validation_examples=validation.examples,
                blind_test_loader=load_blind_test,
                before_blind_test=reserve_selected_test,
                tfns_stress_loader=load_tfns_stress,
            ),
            selection_id=f"fns_{input_digest[:32]}",
            selection_generation=1,
        )
        payload = _receipt_payload(
            blind_test=blind_test,
            evaluation_input_digest=input_digest,
            model_artifacts=model_artifacts,
            result=result,
            tfns_stress=tfns_stress,
            validation=validation,
        )
        _write_new_receipt(receipt_path, payload)
        if test_reservation_identity is not None:
            _remove_test_reservation_if_owned(reservation_path, test_reservation_identity)
        return _summary(payload, code="FOREIGN_NEWS_LOCAL_EVALUATION_COMPLETE", state="COMPLETE")
    finally:
        _release_lock(lock_descriptor, lock_identity, receipt_root / _LOCK_NAME)


def _input_digest(
    validation: ForeignNewsDatasetReceipt,
    model_artifacts: Sequence[ForeignNewsModelArtifactReceipt],
) -> str:
    if tuple(item.candidate_model for item in model_artifacts) != (
        "PROSUSAI_FINBERT",
        "YIYANGHKUST_FINBERT_TONE",
        "LOUGHRAN_MCDONALD_BASELINE",
    ):
        raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_MODEL_ARTIFACT_ORDER_INVALID")
    return _sha256(
        _canonical(
            {
                "contractId": _RECEIPT_CONTRACT_ID,
                "modelArtifacts": [item.to_payload() for item in model_artifacts],
                "sentiventValidation": validation.to_payload(),
            }
        )
    )


def _receipt_payload(
    *,
    blind_test: ForeignNewsLoadedExamples | None,
    evaluation_input_digest: str,
    model_artifacts: Sequence[ForeignNewsModelArtifactReceipt],
    result: ForeignNewsLocalSelectionResult,
    tfns_stress: ForeignNewsLoadedExamples | None,
    validation: ForeignNewsLoadedExamples,
) -> dict[str, object]:
    if _SHA256.fullmatch(evaluation_input_digest) is None:
        raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_EVALUATION_DIGEST_INVALID")
    if result.selection.test_evaluation_count == 0 and (blind_test is not None or tfns_stress is not None):
        raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_EVALUATION_TEST_BOUNDARY")
    if result.selection.test_evaluation_count == 1 and blind_test is None:
        raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_EVALUATION_TEST_BOUNDARY")
    if result.selection.selection_status == "TEST_EVALUATED" and tfns_stress is None:
        raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_EVALUATION_STRESS_BOUNDARY")
    if result.selection.selection_status != "TEST_EVALUATED" and tfns_stress is not None:
        raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_EVALUATION_STRESS_BOUNDARY")
    return {
        "contractId": _RECEIPT_CONTRACT_ID,
        "evaluationInputDigest": evaluation_input_digest,
        "modelArtifacts": [item.to_payload() for item in model_artifacts],
        "result": result.to_payload(),
        "sentiventBlindTest": None if blind_test is None else blind_test.receipt.to_payload(),
        "sentiventValidation": validation.receipt.to_payload(),
        "tfnsStress": None if tfns_stress is None else tfns_stress.receipt.to_payload(),
    }


def _summary(payload: Mapping[str, object], *, code: str, state: str) -> dict[str, object]:
    if not _valid_receipt_payload(payload):
        raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_EVALUATION_RECEIPT_INVALID")
    result = payload["result"]
    assert isinstance(result, Mapping)
    selection = result["selection"]
    assert isinstance(selection, Mapping)
    return {
        "code": code,
        "evaluationInputDigest": payload["evaluationInputDigest"],
        "selectedModel": selection["selectedModel"],
        "selectionStatus": selection["selectionStatus"],
        "state": state,
        "testEvaluationCount": selection["testEvaluationCount"],
        "testOutcome": selection["testOutcome"],
    }


def _ensure_receipt_root(evaluation_root: Path) -> Path:
    root_stat = evaluation_root.lstat()
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or stat.S_ISLNK(root_stat.st_mode)
        or stat.S_IMODE(root_stat.st_mode) != 0o700
    ):
        raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_EVALUATION_ROOT_INVALID")
    receipt_root = evaluation_root / "receipts"
    try:
        os.mkdir(receipt_root, mode=0o700)
    except FileExistsError:
        pass
    metadata = receipt_root.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_EVALUATION_RECEIPT_ROOT_INVALID")
    return receipt_root


def _acquire_lock(path: Path) -> tuple[int, tuple[int, int]]:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError as error:
        raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_LOCAL_EVALUATION_IN_PROGRESS") from error
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
        os.close(descriptor)
        raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_LOCAL_EVALUATION_LOCK_INVALID")
    return descriptor, (metadata.st_dev, metadata.st_ino)


def _release_lock(descriptor: int, identity: tuple[int, int], path: Path) -> None:
    os.close(descriptor)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if (
        stat.S_ISREG(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and (metadata.st_dev, metadata.st_ino) == identity
    ):
        path.unlink()


def _load_receipt_if_present(path: Path) -> Mapping[str, object] | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_size <= 0
        or metadata.st_size > 256 * 1024
    ):
        raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_EVALUATION_RECEIPT_INVALID")
    raw = _read_regular_bytes(path, maximum_bytes=256 * 1024)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_EVALUATION_RECEIPT_INVALID") from error
    if not isinstance(payload, Mapping) or not _valid_receipt_payload(payload):
        raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_EVALUATION_RECEIPT_INVALID")
    return payload


def _write_new_receipt(path: Path, payload: Mapping[str, object]) -> None:
    if not _valid_receipt_payload(payload):
        raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_EVALUATION_RECEIPT_INVALID")
    encoded = _canonical(payload) + b"\n"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError as error:
        raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_EVALUATION_RECEIPT_EXISTS") from error
    try:
        written = 0
        while written < len(encoded):
            count = os.write(descriptor, encoded[written:])
            if count <= 0:
                raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_EVALUATION_RECEIPT_WRITE_FAILED")
            written += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fail_if_test_reservation_exists(path: Path) -> None:
    """crash 후 test가 소비됐는지 모르면 재실행하지 않고 fail-closed한다."""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_size <= 0
        or metadata.st_size > 64 * 1024
    ):
        raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_TEST_RESERVATION_INVALID")
    try:
        payload = json.loads(_read_regular_bytes(path, maximum_bytes=64 * 1024).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_TEST_RESERVATION_INVALID") from error
    if not isinstance(payload, Mapping) or not _valid_test_reservation_payload(payload):
        raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_TEST_RESERVATION_INVALID")
    raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_TEST_EVALUATION_RESUME_BLOCKED")


def _write_test_reservation(
    path: Path,
    *,
    evaluation_input_digest: str,
    selection: ForeignNewsSelectionRun,
) -> tuple[int, int]:
    """blind test 직전에 selected model과 input digest만 durable하게 reserve한다."""

    if (
        _SHA256.fullmatch(evaluation_input_digest) is None
        or selection.selection_status != "SELECTED_PENDING_TEST"
        or selection.selected_model not in {
            "PROSUSAI_FINBERT",
            "YIYANGHKUST_FINBERT_TONE",
            "LOUGHRAN_MCDONALD_BASELINE",
        }
        or selection.test_evaluation_count != 0
    ):
        raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_TEST_RESERVATION_INVALID")
    payload: dict[str, object] = {
        "contractId": _TEST_RESERVATION_CONTRACT_ID,
        "evaluationInputDigest": evaluation_input_digest,
        "selectedModel": selection.selected_model,
        "state": "TEST_RESERVED",
    }
    encoded = _canonical(payload) + b"\n"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError as error:
        raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_TEST_EVALUATION_RESUME_BLOCKED") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_TEST_RESERVATION_INVALID")
        written = 0
        while written < len(encoded):
            count = os.write(descriptor, encoded[written:])
            if count <= 0:
                raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_TEST_RESERVATION_WRITE_FAILED")
            written += count
        os.fsync(descriptor)
        return metadata.st_dev, metadata.st_ino
    finally:
        os.close(descriptor)


def _remove_test_reservation_if_owned(path: Path, identity: tuple[int, int]) -> None:
    """final receipt 후에도 다른 process가 바꾼 reservation은 삭제하지 않는다."""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if (
        stat.S_ISREG(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and (metadata.st_dev, metadata.st_ino) == identity
    ):
        path.unlink()


def _valid_receipt_payload(value: Mapping[str, object]) -> bool:
    if set(value) != {
        "contractId",
        "evaluationInputDigest",
        "modelArtifacts",
        "result",
        "sentiventBlindTest",
        "sentiventValidation",
        "tfnsStress",
    }:
        return False
    if value.get("contractId") != _RECEIPT_CONTRACT_ID:
        return False
    digest = value.get("evaluationInputDigest")
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        return False
    model_artifacts = value.get("modelArtifacts")
    if not isinstance(model_artifacts, list) or len(model_artifacts) != 3:
        return False
    result = value.get("result")
    if not isinstance(result, Mapping) or set(result) != {"blindTest", "selection", "tfnsStress"}:
        return False
    selection = result.get("selection")
    if not isinstance(selection, Mapping):
        return False
    status = selection.get("selectionStatus")
    count = selection.get("testEvaluationCount")
    if status not in {"ABSTAIN", "TEST_EVALUATED"} or count not in {0, 1}:
        return False
    blind = value.get("sentiventBlindTest")
    stress = value.get("tfnsStress")
    if count == 0 and (blind is not None or stress is not None):
        return False
    if count == 1 and not isinstance(blind, Mapping):
        return False
    if status == "TEST_EVALUATED" and not isinstance(stress, Mapping):
        return False
    if status != "TEST_EVALUATED" and stress is not None:
        return False
    return isinstance(value.get("sentiventValidation"), Mapping)


def _valid_test_reservation_payload(value: Mapping[str, object]) -> bool:
    digest = value.get("evaluationInputDigest")
    return (
        set(value) == {"contractId", "evaluationInputDigest", "selectedModel", "state"}
        and value.get("contractId") == _TEST_RESERVATION_CONTRACT_ID
        and isinstance(digest, str)
        and _SHA256.fullmatch(digest) is not None
        and value.get("selectedModel")
        in {"PROSUSAI_FINBERT", "YIYANGHKUST_FINBERT_TONE", "LOUGHRAN_MCDONALD_BASELINE"}
        and value.get("state") == "TEST_RESERVED"
    )


def _read_regular_bytes(path: Path, *, maximum_bytes: int) -> bytes:
    before = path.lstat()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        current = os.fstat(descriptor)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_ino != before.st_ino
            or current.st_dev != before.st_dev
            or current.st_nlink != 1
            or current.st_size != before.st_size
            or current.st_size <= 0
            or current.st_size > maximum_bytes
        ):
            raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_EVALUATION_RECEIPT_RACE")
        raw = bytearray()
        while len(raw) <= maximum_bytes:
            chunk = os.read(descriptor, min(64 * 1024, maximum_bytes + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        if len(raw) != current.st_size:
            raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_EVALUATION_RECEIPT_SIZE")
        return bytes(raw)
    finally:
        os.close(descriptor)


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _emit(value: Mapping[str, object]) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
