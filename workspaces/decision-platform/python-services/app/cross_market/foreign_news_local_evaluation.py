"""Pre-S5 외신 감성의 local-only gold/stress 평가와 FinBERT runner 경계다.

SENTiVENT·TFNS 원문, Loughran--McDonald 사전, PyTorch 가중치는 ignored local cache에서만 읽는다.
이 모듈은 provider 호출, model download, raw 문장·label·prediction persistence를 만들지 않으며,
반환하는 receipt도 revision/license/hash와 aggregate metric만 포함한다.
"""

from __future__ import annotations

import csv
import hashlib
import importlib
import json
import os
import re
import stat
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Final

from app.cross_market.foreign_news import (
    MODEL_CANDIDATES,
    ForeignNewsSelectionMetrics,
    ForeignNewsSelectionRun,
)
from app.cross_market.foreign_news_evaluator import (
    ForeignNewsEvaluationExample,
    ForeignNewsEvaluationHarness,
    ForeignNewsLocalCandidate,
    ForeignNewsPrediction,
)

_LABELS: Final[frozenset[str]] = frozenset({"NEGATIVE", "NEUTRAL", "POSITIVE"})
_SENTIVENT_POLARITY_MAP: Final[Mapping[str, str]] = {
    "negative": "NEGATIVE",
    "neutral": "NEUTRAL",
    "positive": "POSITIVE",
}
_TFNS_LABEL_MAP: Final[Mapping[str, str]] = {
    "0": "NEGATIVE",
    "1": "POSITIVE",
    "2": "NEUTRAL",
}
_MAX_DATASET_BYTES: Final[int] = 64 * 1024 * 1024
_MAX_DATASET_ROWS: Final[int] = 20_000
_MAX_MODEL_FILE_BYTES: Final[int] = 1_024 * 1024 * 1024
_MAX_VOCABULARY_FILE_BYTES: Final[int] = 8 * 1024 * 1024
_MAX_TEXT_BYTES: Final[int] = 32 * 1024
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")
_CRITICAL = re.compile(
    r"(?:\b(?:no|not|never|neither|nor|without)\b|\b(?:declin|decreas|loss|miss|fall)\w*\b|\d+(?:[.,]\d+)?(?:%|\s*(?:bp|bps|million|billion|trillion))?)",
    re.IGNORECASE,
)
_REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_FINBERT_EVALUATION_ROOT: Final[Path] = (
    _REPOSITORY_ROOT / "capstone-rag" / "runtime" / "finbert-eval"
)


class ForeignNewsLocalEvaluationError(ValueError):
    """local-only evaluation input/model/runtime contract가 깨졌음을 나타낸다."""


def _no_op_before_blind_test(_: ForeignNewsSelectionRun) -> None:
    """generic runner는 persistence를 소유하지 않으므로 default에는 durable side effect가 없다."""


@dataclass(frozen=True, slots=True)
class ForeignNewsDatasetReceipt:
    """원문을 포함하지 않는 local dataset revision/license/count receipt다."""

    dataset_id: str
    excluded_ambiguous_or_unlabeled_count: int
    included_example_count: int
    license_evidence_sha256: str
    raw_sha256: str
    source_revision_sha256: str
    split: str

    def __post_init__(self) -> None:
        if (
            self.dataset_id
            not in {"GillesJacobs/sentivent", "zeroshot/twitter-financial-news-sentiment"}
            or self.split not in {"validation", "test", "stress-validation"}
            or self.included_example_count <= 0
            or self.excluded_ambiguous_or_unlabeled_count < 0
            or any(_sha256_pattern(value) is None for value in self._hashes())
        ):
            raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_DATASET_RECEIPT_INVALID")

    def _hashes(self) -> tuple[str, str, str]:
        return (self.license_evidence_sha256, self.raw_sha256, self.source_revision_sha256)

    def to_payload(self) -> dict[str, object]:
        """content-free receipt를 반환해 evaluation input이 log/DB에 새지 않게 한다."""

        return {
            "datasetId": self.dataset_id,
            "excludedAmbiguousOrUnlabeledCount": self.excluded_ambiguous_or_unlabeled_count,
            "includedExampleCount": self.included_example_count,
            "licenseEvidenceSha256": self.license_evidence_sha256,
            "rawSha256": self.raw_sha256,
            "sourceRevisionSha256": self.source_revision_sha256,
            "split": self.split,
        }


@dataclass(frozen=True, slots=True)
class ForeignNewsLoadedExamples:
    """in-memory evaluation examples와 공개 가능한 source receipt를 함께 보관한다."""

    examples: tuple[ForeignNewsEvaluationExample, ...]
    receipt: ForeignNewsDatasetReceipt

    def __post_init__(self) -> None:
        if len(self.examples) != self.receipt.included_example_count:
            raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_DATASET_COUNT_INVALID")


@dataclass(frozen=True, slots=True)
class ForeignNewsModelArtifactReceipt:
    """각 local candidate의 file evidence만 담는다. 가중치 자체는 포함하지 않는다."""

    candidate_model: str
    config_sha256: str
    footprint_bytes: int
    tokenizer_sha256: str
    weights_sha256: str

    def __post_init__(self) -> None:
        if (
            self.candidate_model not in MODEL_CANDIDATES
            or self.footprint_bytes <= 0
            or any(_sha256_pattern(value) is None for value in self._hashes())
        ):
            raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_MODEL_ARTIFACT_INVALID")

    def _hashes(self) -> tuple[str, str, str]:
        return (self.config_sha256, self.tokenizer_sha256, self.weights_sha256)

    def to_payload(self) -> dict[str, object]:
        return {
            "candidateModel": self.candidate_model,
            "configSha256": self.config_sha256,
            "footprintBytes": self.footprint_bytes,
            "tokenizerSha256": self.tokenizer_sha256,
            "weightsSha256": self.weights_sha256,
        }


@dataclass(frozen=True, slots=True)
class ForeignNewsLocalEvaluationInputs:
    """validation→single blind test→TFNS stress 순서를 명시적으로 고정한다."""

    candidates: Sequence[ForeignNewsLocalCandidate]
    validation_examples: Sequence[ForeignNewsEvaluationExample]
    blind_test_loader: Callable[[], Sequence[ForeignNewsEvaluationExample]]
    tfns_stress_loader: Callable[[], Sequence[ForeignNewsEvaluationExample]]
    before_blind_test: Callable[[ForeignNewsSelectionRun], None] = _no_op_before_blind_test
    harness: ForeignNewsEvaluationHarness = field(default_factory=ForeignNewsEvaluationHarness)

    def __post_init__(self) -> None:
        if (
            not callable(self.blind_test_loader)
            or not callable(self.tfns_stress_loader)
            or not callable(self.before_blind_test)
        ):
            raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_EVALUATION_LOADER_INVALID")
        if not isinstance(self.harness, ForeignNewsEvaluationHarness):
            raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_EVALUATION_HARNESS_INVALID")


@dataclass(frozen=True, slots=True)
class ForeignNewsLocalSelectionResult:
    """selection과 selected-only blind/stress aggregate metrics다."""

    blind_test_metrics: ForeignNewsSelectionMetrics | None
    selection: ForeignNewsSelectionRun
    tfns_stress_metrics: ForeignNewsSelectionMetrics | None

    def __post_init__(self) -> None:
        if self.selection.selection_status == "TEST_EVALUATED":
            if self.blind_test_metrics is None or self.tfns_stress_metrics is None:
                raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_SELECTION_METRICS_REQUIRED")
            return
        if self.selection.test_evaluation_count == 1:
            if self.blind_test_metrics is None or self.tfns_stress_metrics is not None:
                raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_SELECTION_METRICS_REQUIRED")
            return
        if self.blind_test_metrics is not None or self.tfns_stress_metrics is not None:
            raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_SELECTION_METRICS_FORBIDDEN")

    def to_payload(self) -> dict[str, object]:
        """selection schema payload와 content-free optional results를 결합한다."""

        return {
            "blindTest": None
            if self.blind_test_metrics is None
            else self.blind_test_metrics.to_payload(),
            "selection": self.selection.to_storage_payload(),
            "tfnsStress": None
            if self.tfns_stress_metrics is None
            else self.tfns_stress_metrics.to_payload(),
        }


def load_sentivent_gold_split(*, dataset_root: Path, split: str) -> ForeignNewsLoadedExamples:
    """SENTiVENT event-polarity에서 단일 3-class 문장만 local gold profile로 변환한다.

    mixed-polarity 또는 event polarity가 없는 문장은 감성 합성 없이 제외한다. train split은 이
    evaluator가 사용하지 않으며 validation만 model selection, test만 selected-model blind test에 쓴다.
    """

    if split not in {"validation", "test"}:
        raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_SENTIVENT_SPLIT_INVALID")
    split_path = _regular_file(
        dataset_root,
        PurePosixPath("data") / "sentivent_unified_sentence" / f"{split}.jsonl",
        maximum_bytes=_MAX_DATASET_BYTES,
    )
    raw = _read_regular_bytes(split_path, maximum_bytes=_MAX_DATASET_BYTES)
    source_revision = _read_regular_bytes(
        _regular_file(
            dataset_root,
            PurePosixPath("metadata") / "build_info.json",
            maximum_bytes=256 * 1024,
        ),
        maximum_bytes=256 * 1024,
    )
    license_evidence = _read_regular_bytes(
        _regular_file(dataset_root, PurePosixPath("LICENSE"), maximum_bytes=256 * 1024),
        maximum_bytes=256 * 1024,
    )
    examples: list[ForeignNewsEvaluationExample] = []
    excluded = 0
    for raw_line in raw.splitlines():
        if not raw_line.strip():
            raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_SENTIVENT_ROW_INVALID")
        if len(examples) + excluded >= _MAX_DATASET_ROWS:
            raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_SENTIVENT_ROW_CAP")
        try:
            row = json.loads(raw_line)
        except json.JSONDecodeError as error:
            raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_SENTIVENT_JSON_INVALID") from error
        label = _sentivent_label(row)
        if label is None:
            excluded += 1
            continue
        text = _row_text(row, code="FOREIGN_NEWS_SENTIVENT_TEXT_INVALID")
        examples.append(
            ForeignNewsEvaluationExample(
                text=text,
                expected_label=label,
                critical_negation_number_unit=_is_critical_text(text),
            )
        )
    if not examples:
        raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_SENTIVENT_NO_USABLE_EXAMPLES")
    receipt = ForeignNewsDatasetReceipt(
        dataset_id="GillesJacobs/sentivent",
        excluded_ambiguous_or_unlabeled_count=excluded,
        included_example_count=len(examples),
        license_evidence_sha256=_sha256(license_evidence),
        raw_sha256=_sha256(raw),
        source_revision_sha256=_sha256(source_revision),
        split=split,
    )
    return ForeignNewsLoadedExamples(examples=tuple(examples), receipt=receipt)


def load_tfns_stress_split(*, dataset_root: Path) -> ForeignNewsLoadedExamples:
    """TFNS validation split을 selected model의 stress-only input으로 local parse한다."""

    path = _regular_file(
        dataset_root,
        PurePosixPath("sent_valid.csv"),
        maximum_bytes=_MAX_DATASET_BYTES,
    )
    raw = _read_regular_bytes(path, maximum_bytes=_MAX_DATASET_BYTES)
    readme = _read_regular_bytes(
        _regular_file(dataset_root, PurePosixPath("README.md"), maximum_bytes=512 * 1024),
        maximum_bytes=512 * 1024,
    )
    try:
        rows = tuple(csv.DictReader(raw.decode("utf-8-sig").splitlines()))
    except (UnicodeDecodeError, csv.Error) as error:
        raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_TFNS_CSV_INVALID") from error
    if not rows or len(rows) > _MAX_DATASET_ROWS:
        raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_TFNS_ROW_INVALID")
    examples: list[ForeignNewsEvaluationExample] = []
    for row in rows:
        if set(row) != {"label", "text"}:
            raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_TFNS_SCHEMA_INVALID")
        label = _TFNS_LABEL_MAP.get(str(row.get("label", "")))
        if label is None:
            raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_TFNS_LABEL_INVALID")
        text_value = row.get("text")
        text = _validated_text(text_value, code="FOREIGN_NEWS_TFNS_TEXT_INVALID")
        examples.append(
            ForeignNewsEvaluationExample(
                text=text,
                expected_label=label,
                critical_negation_number_unit=_is_critical_text(text),
            )
        )
    receipt = ForeignNewsDatasetReceipt(
        dataset_id="zeroshot/twitter-financial-news-sentiment",
        excluded_ambiguous_or_unlabeled_count=0,
        included_example_count=len(examples),
        license_evidence_sha256=_sha256(readme),
        raw_sha256=_sha256(raw),
        source_revision_sha256=_sha256(readme),
        split="stress-validation",
    )
    return ForeignNewsLoadedExamples(examples=tuple(examples), receipt=receipt)


def run_local_model_selection(
    *,
    inputs: ForeignNewsLocalEvaluationInputs,
    selection_id: str,
    selection_generation: int,
) -> ForeignNewsLocalSelectionResult:
    """validation winner 하나만 blind test와 TFNS stress로 진행해 test-shopping을 막는다."""

    selection = inputs.harness.evaluate_validation(
        selection_id=selection_id,
        selection_generation=selection_generation,
        candidates=inputs.candidates,
        examples=inputs.validation_examples,
    )
    if selection.selection_status != "SELECTED_PENDING_TEST":
        return ForeignNewsLocalSelectionResult(
            blind_test_metrics=None,
            selection=selection,
            tfns_stress_metrics=None,
        )
    # CLI가 durable reservation을 먼저 남겨 crash 뒤 blind test를 재소비하지 않게 한다.
    inputs.before_blind_test(selection)
    tested = inputs.harness.evaluate_selected_test(
        selection=selection,
        candidates=inputs.candidates,
        examples=inputs.blind_test_loader(),
    )
    if tested.selection.selection_status != "TEST_EVALUATED":
        return ForeignNewsLocalSelectionResult(
            blind_test_metrics=tested.metrics,
            selection=tested.selection,
            tfns_stress_metrics=None,
        )
    selected = next(
        (
            candidate
            for candidate in inputs.candidates
            if candidate.candidate_model == tested.selection.selected_model
        ),
        None,
    )
    if selected is None:
        raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_SELECTED_CANDIDATE_MISSING")
    stress_metrics = inputs.harness.evaluate_candidate(
        candidate=selected,
        examples=inputs.tfns_stress_loader(),
    )
    return ForeignNewsLocalSelectionResult(
        blind_test_metrics=tested.metrics,
        selection=tested.selection,
        tfns_stress_metrics=stress_metrics,
    )


def build_local_model_candidates(
    *,
    evaluation_root: Path = DEFAULT_FINBERT_EVALUATION_ROOT,
) -> tuple[tuple[ForeignNewsLocalCandidate, ...], tuple[ForeignNewsModelArtifactReceipt, ...]]:
    """ignored local cache의 정확한 세 후보만 CPU runner로 열고 network/remote code를 거부한다."""

    model_root = _regular_directory(evaluation_root, PurePosixPath("models"))
    prosus, prosus_receipt = _load_finbert_candidate(
        model_root=model_root,
        directory_name="ProsusAI--finbert",
        candidate_model="PROSUSAI_FINBERT",
    )
    tone, tone_receipt = _load_finbert_candidate(
        model_root=model_root,
        directory_name="yiyanghkust--finbert-tone",
        candidate_model="YIYANGHKUST_FINBERT_TONE",
    )
    dictionary_path = _regular_file(
        evaluation_root,
        PurePosixPath("loughran-mcdonald-master-dictionary.csv"),
        maximum_bytes=_MAX_DATASET_BYTES,
    )
    baseline, baseline_receipt = _load_loughran_mcdonald_candidate(dictionary_path)
    candidates = (
        ForeignNewsLocalCandidate(
            candidate_model="PROSUSAI_FINBERT",
            classifier=prosus,
            footprint_bytes=prosus_receipt.footprint_bytes,
        ),
        ForeignNewsLocalCandidate(
            candidate_model="YIYANGHKUST_FINBERT_TONE",
            classifier=tone,
            footprint_bytes=tone_receipt.footprint_bytes,
        ),
        ForeignNewsLocalCandidate(
            candidate_model="LOUGHRAN_MCDONALD_BASELINE",
            classifier=baseline,
            footprint_bytes=baseline_receipt.footprint_bytes,
        ),
    )
    return candidates, (prosus_receipt, tone_receipt, baseline_receipt)


class _LocalFinBertClassifier:
    """local PyTorch BERT를 CPU/weights-only/local-files-only 경계로 실행한다."""

    def __init__(self, *, model_root: Path, label_map: Mapping[int, str]) -> None:
        try:
            torch: Any = importlib.import_module("torch")
            transformers: Any = importlib.import_module("transformers")
            bert_config: Any = transformers.BertConfig
            bert_model: Any = transformers.BertForSequenceClassification
            bert_tokenizer: Any = transformers.BertTokenizer
            transformers_logging: Any = transformers.logging
        except (AttributeError, ImportError) as error:
            raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_FINBERT_RUNTIME_MISSING") from error
        transformers_logging.set_verbosity_error()
        config_payload = _load_json_file(model_root / "config.json", maximum_bytes=256 * 1024)
        if not isinstance(config_payload, dict):
            raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_FINBERT_CONFIG_INVALID")
        # finbert-tone의 historical config는 model_type을 생략하지만 BERT architecture 자체는 fixed다.
        config_payload = dict(config_payload)
        config_payload.setdefault("model_type", "bert")
        try:
            config = bert_config.from_dict(config_payload)
            tokenizer = bert_tokenizer(vocab=str(model_root / "vocab.txt"), do_lower_case=True)
            model = bert_model.from_pretrained(
                model_root,
                config=config,
                local_files_only=True,
                trust_remote_code=False,
                use_safetensors=False,
                weights_only=True,
            ).to("cpu")
            model.eval()
        except Exception as error:
            raise ForeignNewsLocalEvaluationError(
                "FOREIGN_NEWS_FINBERT_MODEL_LOAD_FAILED"
            ) from error
        if (
            model.config.num_labels != 3
            or set(label_map) != {0, 1, 2}
            or set(label_map.values()) != _LABELS
        ):
            raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_FINBERT_LABEL_MAP_INVALID")
        self._label_map = dict(label_map)
        self._model = model
        self._tokenizer = tokenizer
        self._torch = torch

    def predict(self, text: str) -> ForeignNewsPrediction:
        """in-memory sentence를 CPU single prediction으로만 변환하고 raw input을 보관하지 않는다."""

        safe_text = _validated_text(text, code="FOREIGN_NEWS_FINBERT_TEXT_INVALID")
        try:
            encoded = self._tokenizer(
                safe_text,
                max_length=512,
                padding=False,
                return_tensors="pt",
                truncation=True,
            )
            with self._torch.inference_mode():
                logits = self._model(**encoded).logits
                probabilities = self._torch.softmax(logits, dim=-1)[0]
            index = int(self._torch.argmax(probabilities).item())
            confidence = float(probabilities[index].item())
        except Exception as error:
            raise ForeignNewsLocalEvaluationError(
                "FOREIGN_NEWS_FINBERT_MODEL_EXECUTION_FAILED"
            ) from error
        label = self._label_map.get(index)
        if label is None:
            raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_FINBERT_LABEL_MAP_INVALID")
        return ForeignNewsPrediction(label=label, confidence=confidence)


class _LoughranMcDonaldClassifier:
    """추가 모델 download 없이 local master dictionary로 만든 CPU baseline이다."""

    def __init__(self, *, negative_words: frozenset[str], positive_words: frozenset[str]) -> None:
        if not negative_words or not positive_words:
            raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_LOUGHRAN_DICTIONARY_INVALID")
        self._negative_words = negative_words
        self._positive_words = positive_words

    def predict(self, text: str) -> ForeignNewsPrediction:
        words = tuple(
            token.upper()
            for token in _WORD.findall(
                _validated_text(text, code="FOREIGN_NEWS_LOUGHRAN_TEXT_INVALID")
            )
        )
        negative = sum(token in self._negative_words for token in words)
        positive = sum(token in self._positive_words for token in words)
        if positive > negative:
            return ForeignNewsPrediction("POSITIVE", positive / (positive + negative))
        if negative > positive:
            return ForeignNewsPrediction("NEGATIVE", negative / (positive + negative))
        return ForeignNewsPrediction("NEUTRAL", 1.0 if positive == 0 else 0.5)


def _load_finbert_candidate(
    *,
    model_root: Path,
    directory_name: str,
    candidate_model: str,
) -> tuple[_LocalFinBertClassifier, ForeignNewsModelArtifactReceipt]:
    root = _regular_directory(model_root, PurePosixPath(directory_name))
    config_path = _regular_file(root, PurePosixPath("config.json"), maximum_bytes=256 * 1024)
    weights_path = _regular_file(
        root, PurePosixPath("pytorch_model.bin"), maximum_bytes=_MAX_MODEL_FILE_BYTES
    )
    vocabulary_path = _regular_file(
        root,
        PurePosixPath("vocab.txt"),
        maximum_bytes=_MAX_VOCABULARY_FILE_BYTES,
    )
    config_payload = _load_json_file(config_path, maximum_bytes=256 * 1024)
    labels = _finbert_label_map(candidate_model=candidate_model, config_payload=config_payload)
    classifier = _LocalFinBertClassifier(model_root=root, label_map=labels)
    receipt = ForeignNewsModelArtifactReceipt(
        candidate_model=candidate_model,
        config_sha256=_sha256(_read_regular_bytes(config_path, maximum_bytes=256 * 1024)),
        footprint_bytes=sum(
            path.stat().st_size for path in (config_path, weights_path, vocabulary_path)
        ),
        tokenizer_sha256=_sha256(
            _read_regular_bytes(vocabulary_path, maximum_bytes=_MAX_VOCABULARY_FILE_BYTES)
        ),
        weights_sha256=_sha256(
            _read_regular_bytes(weights_path, maximum_bytes=_MAX_MODEL_FILE_BYTES)
        ),
    )
    return classifier, receipt


def _load_loughran_mcdonald_candidate(
    path: Path,
) -> tuple[_LoughranMcDonaldClassifier, ForeignNewsModelArtifactReceipt]:
    raw = _read_regular_bytes(path, maximum_bytes=_MAX_DATASET_BYTES)
    try:
        rows = tuple(csv.DictReader(raw.decode("utf-8-sig").splitlines()))
    except (UnicodeDecodeError, csv.Error) as error:
        raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_LOUGHRAN_DICTIONARY_INVALID") from error
    negative: set[str] = set()
    positive: set[str] = set()
    for row in rows:
        if not {"Negative", "Positive", "Word"} <= set(row):
            raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_LOUGHRAN_DICTIONARY_INVALID")
        word = row.get("Word", "").strip().upper()
        if not word or len(word) > 128:
            raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_LOUGHRAN_DICTIONARY_INVALID")
        # Master Dictionary의 category 값은 boolean 1이 아니라 최초 분류 연도다.
        if _dictionary_membership(row.get("Negative")):
            negative.add(word)
        if _dictionary_membership(row.get("Positive")):
            positive.add(word)
    classifier = _LoughranMcDonaldClassifier(
        negative_words=frozenset(negative),
        positive_words=frozenset(positive),
    )
    digest = _sha256(raw)
    receipt = ForeignNewsModelArtifactReceipt(
        candidate_model="LOUGHRAN_MCDONALD_BASELINE",
        config_sha256=digest,
        footprint_bytes=len(raw),
        tokenizer_sha256=digest,
        weights_sha256=digest,
    )
    return classifier, receipt


def _finbert_label_map(*, candidate_model: str, config_payload: object) -> Mapping[int, str]:
    if not isinstance(config_payload, Mapping):
        raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_FINBERT_CONFIG_INVALID")
    id_to_label = config_payload.get("id2label")
    if not isinstance(id_to_label, Mapping):
        raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_FINBERT_CONFIG_INVALID")
    normalized: dict[int, str] = {}
    for index in range(3):
        value = id_to_label.get(str(index))
        if not isinstance(value, str):
            raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_FINBERT_CONFIG_INVALID")
        label = value.upper()
        if label not in _LABELS:
            raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_FINBERT_CONFIG_INVALID")
        normalized[index] = label
    expected = {
        "PROSUSAI_FINBERT": {0: "POSITIVE", 1: "NEGATIVE", 2: "NEUTRAL"},
        "YIYANGHKUST_FINBERT_TONE": {0: "NEUTRAL", 1: "POSITIVE", 2: "NEGATIVE"},
    }.get(candidate_model)
    if expected is None or normalized != expected:
        raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_FINBERT_LABEL_MAP_INVALID")
    return normalized


def _dictionary_membership(value: object) -> bool:
    return isinstance(value, str) and value.strip() not in {"", "0"}


def _sentivent_label(row: object) -> str | None:
    if not isinstance(row, Mapping):
        raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_SENTIVENT_ROW_INVALID")
    annotations = row.get("annotations")
    if not isinstance(annotations, list) or len(annotations) > 4096:
        raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_SENTIVENT_ROW_INVALID")
    event_polarities: list[str] = []
    for annotation in annotations:
        if not isinstance(annotation, Mapping):
            raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_SENTIVENT_ROW_INVALID")
        if annotation.get("kind") != "event":
            continue
        polarity = annotation.get("scoped_polarity")
        if not isinstance(polarity, str) or polarity.casefold() not in _SENTIVENT_POLARITY_MAP:
            return None
        event_polarities.append(_SENTIVENT_POLARITY_MAP[polarity.casefold()])
    if not event_polarities or len(set(event_polarities)) != 1:
        return None
    return event_polarities[0]


def _row_text(row: object, *, code: str) -> str:
    if not isinstance(row, Mapping):
        raise ForeignNewsLocalEvaluationError(code)
    return _validated_text(row.get("text"), code=code)


def _validated_text(value: object, *, code: str) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise ForeignNewsLocalEvaluationError(code)
    normalized = unicodedata.normalize("NFC", value).strip()
    if not normalized or len(normalized.encode("utf-8")) > _MAX_TEXT_BYTES:
        raise ForeignNewsLocalEvaluationError(code)
    return normalized


def _is_critical_text(text: str) -> bool:
    """negation·loss vocabulary 또는 numeric/unit span이 있는 gold input을 critical subset으로 표시한다."""

    return _CRITICAL.search(text) is not None


def _regular_directory(root: Path, relative: PurePosixPath) -> Path:
    root_stat = root.lstat()
    if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
        raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_LOCAL_ROOT_INVALID")
    if relative.is_absolute() or ".." in relative.parts:
        raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_LOCAL_PATH_INVALID")
    candidate = root
    try:
        for part in relative.parts:
            candidate = candidate / part
            candidate_stat = candidate.lstat()
            if not stat.S_ISDIR(candidate_stat.st_mode) or stat.S_ISLNK(candidate_stat.st_mode):
                raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_LOCAL_PATH_INVALID")
    except FileNotFoundError as error:
        raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_LOCAL_PATH_INVALID") from error
    return candidate


def _regular_file(root: Path, relative: PurePosixPath, *, maximum_bytes: int) -> Path:
    if relative.is_absolute() or ".." in relative.parts:
        raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_LOCAL_PATH_INVALID")
    parent = _regular_directory(root, relative.parent)
    candidate = parent / relative.name
    try:
        metadata = candidate.lstat()
    except FileNotFoundError as error:
        raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_LOCAL_FILE_MISSING") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size <= 0
        or metadata.st_size > maximum_bytes
    ):
        raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_LOCAL_FILE_INVALID")
    return candidate


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
            raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_LOCAL_FILE_RACE")
        content = bytearray()
        while len(content) <= maximum_bytes:
            chunk = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
        if len(content) != current.st_size:
            raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_LOCAL_FILE_SIZE")
        return bytes(content)
    finally:
        os.close(descriptor)


def _load_json_file(path: Path, *, maximum_bytes: int) -> object:
    try:
        return json.loads(_read_regular_bytes(path, maximum_bytes=maximum_bytes).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ForeignNewsLocalEvaluationError("FOREIGN_NEWS_FINBERT_CONFIG_INVALID") from error


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_pattern(value: str) -> re.Match[str] | None:
    return re.fullmatch(r"[0-9a-f]{64}", value)
