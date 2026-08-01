from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Final


_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(
    0,
    str(_REPOSITORY_ROOT / "workspaces/decision-platform/python-services"),
)
from app.rag.benchmark_receipt_io import write_benchmark_receipt  # noqa: E402
from app.rag.ocr_benchmark import parse_grounded_ocr_output  # noqa: E402


_ANSI: Final = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_IMAGE: Final = "capstone/unlimited-ocr-llama@sha256:1d96b6f71f35876beed2de645e27c5f8291a9c461a576a95b4e9cf911bf6ef98"
_MODEL_VOLUME: Final = "capstone-rag-ocr-unlimited-5bf3f3ec69934593"


def _repository_root() -> Path:
    return _REPOSITORY_ROOT


def _load_manifest() -> dict[str, Any]:
    return json.loads(
        Path(__file__).with_name("benchmark-manifest.v1.json").read_text(encoding="utf-8")
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _fixtures(manifest: dict[str, Any], fixture: str | None) -> list[tuple[str, Path, str]]:
    pages = _repository_root() / "capstone-rag/runtime/local-corpus/ocr-benchmark/pages"
    rows: list[tuple[str, Path, str]] = []
    for source in manifest["sources"]:
        for page in source["pages"]:
            if fixture is not None and page["fixtureId"] != fixture:
                continue
            path = pages / page["imageFile"]
            if not path.is_file() or _sha256(path) != page["imageSha256"]:
                raise RuntimeError("OCR_BENCHMARK_PAGE_DIGEST_MISMATCH")
            rows.append((page["fixtureId"], path, page["imageSha256"]))
    if not rows:
        raise RuntimeError("OCR_BENCHMARK_FIXTURE_UNKNOWN")
    return rows


def _verify_runtime(manifest: dict[str, Any]) -> tuple[Path, Path, str]:
    docker = shutil.which("docker")
    if docker is None or not Path(docker).is_absolute():
        raise RuntimeError("OCR_DOCKER_UNAVAILABLE")
    inspected = subprocess.run(  # noqa: S603 - resolved trusted Docker executable.
        (docker, "image", "inspect", _IMAGE, "--format", "{{.Id}}"),
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    expected_image = manifest["unlimitedGguf"]["containerImageDigest"]
    if inspected != expected_image:
        raise RuntimeError("OCR_CONTAINER_DIGEST_MISMATCH")
    models = _repository_root() / "capstone-rag/runtime/local-corpus/ocr-models/unlimited"
    model = models / manifest["unlimitedGguf"]["modelFile"]
    projector = models / manifest["unlimitedGguf"]["projectorFile"]
    if (
        not model.is_file()
        or not projector.is_file()
        or _sha256(model) != manifest["unlimitedGguf"]["modelFileSha256"]
        or _sha256(projector) != manifest["unlimitedGguf"]["projectorFileSha256"]
    ):
        raise RuntimeError("OCR_MODEL_DIGEST_MISMATCH")
    _ensure_model_volume(docker, model, projector, manifest)
    return model, projector, docker


def _command(docker: str, container_name: str) -> tuple[str, ...]:
    shell_command = (
        "set -eu; umask 077; cat > /tmp/page.png; "
        "exec /usr/local/bin/llama-mtmd-cli "
        "-m /models/model.gguf --mmproj /models/mmproj.gguf --image /tmp/page.png "
        "-p '<|grounding|>Convert the document to markdown.' "
        "--chat-template deepseek-ocr --temp 0 --repeat-penalty 1.05 --flash-attn off "
        "--no-warmup --offline --log-colors off --log-timestamps --threads 8 "
        "--threads-batch 8 --predict 4096 --ctx-size 16384"
    )
    return (
        docker,
        "run",
        "--rm",
        "--interactive",
        "--name",
        container_name,
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "256",
        "--memory",
        "8g",
        "--cpus",
        "8",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=16m",
        "--mount",
        f"type=volume,source={_MODEL_VOLUME},target=/models,readonly",
        "--entrypoint",
        "/bin/sh",
        _IMAGE,
        "-c",
        shell_command,
    )


def _ensure_model_volume(
    docker: str,
    model: Path,
    projector: Path,
    manifest: dict[str, Any],
) -> None:
    inspected = subprocess.run(  # noqa: S603 - resolved trusted Docker executable.
        (docker, "volume", "inspect", _MODEL_VOLUME),
        check=False,
        capture_output=True,
        timeout=30,
    )
    if inspected.returncode != 0:
        subprocess.run(  # noqa: S603 - exact generated cache volume.
            (
                docker,
                "volume",
                "create",
                "--label",
                "com.capstone.rag.cache=unlimited-ocr",
                _MODEL_VOLUME,
            ),
            check=True,
            capture_output=True,
            timeout=30,
        )
        seed_name = f"capstone-ocr-model-seed-{secrets.token_hex(6)}"
        try:
            subprocess.run(  # noqa: S603 - exact pinned image and generated cache volume.
                (
                    docker,
                    "create",
                    "--name",
                    seed_name,
                    "--mount",
                    f"type=volume,source={_MODEL_VOLUME},target=/models",
                    "--entrypoint",
                    "/bin/true",
                    _IMAGE,
                ),
                check=True,
                capture_output=True,
                timeout=30,
            )
            for source, target in (
                (model, "/models/model.gguf"),
                (projector, "/models/mmproj.gguf"),
            ):
                subprocess.run(  # noqa: S603 - Docker CLI streams verified public model bytes.
                    (docker, "cp", str(source), f"{seed_name}:{target}"),
                    check=True,
                    capture_output=True,
                    timeout=600,
                )
        finally:
            subprocess.run(  # noqa: S603 - exact generated seed container.
                (docker, "rm", "--force", seed_name),
                check=False,
                capture_output=True,
                timeout=30,
            )
    expected = (
        f"{manifest['unlimitedGguf']['modelFileSha256']}  /models/model.gguf\n"
        f"{manifest['unlimitedGguf']['projectorFileSha256']}  /models/mmproj.gguf"
    )
    verified = subprocess.run(  # noqa: S603 - exact read-only cache verification command.
        (
            docker,
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--mount",
            f"type=volume,source={_MODEL_VOLUME},target=/models,readonly",
            "--entrypoint",
            "/usr/bin/sha256sum",
            _IMAGE,
            "/models/model.gguf",
            "/models/mmproj.gguf",
        ),
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    ).stdout.strip()
    if verified != expected:
        raise RuntimeError("OCR_MODEL_VOLUME_DIGEST_MISMATCH")


def _write_receipt(
    runtime_root: Path,
    filename: str,
    value: dict[str, object],
) -> None:
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    write_benchmark_receipt(
        approved_root=runtime_root,
        relative_directory="ocr-benchmark/results/UNLIMITED_GGUF/CPU",
        filename=filename,
        payload=payload,
    )


def _run(output_directory: Path, fixture: str | None) -> None:
    manifest = _load_manifest()
    _model, _projector, docker = _verify_runtime(manifest)
    runtime_root = _repository_root() / "capstone-rag/runtime/local-corpus"
    expected_output = runtime_root / "ocr-benchmark/results/UNLIMITED_GGUF/CPU"
    if Path(os.path.abspath(output_directory)) != expected_output:
        raise RuntimeError("OCR_BENCHMARK_OUTPUT_INVALID")
    for fixture_id, image, image_hash in _fixtures(manifest, fixture):
        started = time.perf_counter()
        filename = f"{fixture_id}.json"
        container_name = f"capstone-ocr-{fixture_id[:32]}-{secrets.token_hex(4)}"
        base: dict[str, object] = {
            "candidate": "UNLIMITED_GGUF",
            "containerImageDigest": manifest["unlimitedGguf"]["containerImageDigest"],
            "fixtureId": fixture_id,
            "imageSha256": image_hash,
            "lane": "CPU",
            "modelSha256": manifest["candidates"]["UNLIMITED_GGUF"]["modelSha256"],
        }
        try:
            result = subprocess.run(  # noqa: S603 - exact Docker command and digest.
                _command(docker, container_name),
                check=False,
                capture_output=True,
                input=image.read_bytes(),
                timeout=900,
            )
        except subprocess.TimeoutExpired:
            _force_remove_container(docker, container_name)
            base.update(
                {
                    "elapsedSeconds": time.perf_counter() - started,
                    "failureCode": "OCR_CANDIDATE_TIMEOUT",
                    "status": "FAILED",
                }
            )
            _write_receipt(runtime_root, filename, base)
            continue
        if result.returncode != 0 or len(result.stdout) > 16 * 1024 * 1024:
            base.update(
                {
                    "elapsedSeconds": time.perf_counter() - started,
                    "failureCode": "OCR_CANDIDATE_PROCESS_FAILED",
                    "status": "FAILED",
                }
            )
            _write_receipt(runtime_root, filename, base)
            continue
        parsed = parse_grounded_ocr_output(
            _ANSI.sub("", result.stdout.decode("utf-8", errors="replace")).strip()
        )
        if not parsed.text:
            base.update(
                {
                    "elapsedSeconds": time.perf_counter() - started,
                    "failureCode": "OCR_CANDIDATE_EMPTY",
                    "status": "FAILED",
                }
            )
        else:
            base.update(
                {
                    "elapsedSeconds": time.perf_counter() - started,
                    "grounding": [
                        {
                            "bbox": list(span.bbox),
                            "label": span.label,
                            "text": span.text,
                        }
                        for span in parsed.spans
                    ],
                    "result": parsed.text,
                    "status": "SUCCEEDED",
                }
            )
        _write_receipt(runtime_root, filename, base)


def _force_remove_container(docker: str, container_name: str) -> None:
    subprocess.run(  # noqa: S603 - exact generated benchmark container.
        (docker, "rm", "--force", container_name),
        check=False,
        capture_output=True,
        timeout=30,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture")
    parser.add_argument("--output-directory", required=True, type=Path)
    arguments = parser.parse_args()
    _run(Path(os.path.abspath(arguments.output_directory)), arguments.fixture)


if __name__ == "__main__":
    main()
