from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
from pathlib import Path

import numpy as np
import openvino as ov


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    """실제 OpenVINO GPU compile/infer와 execution device를 JSON receipt로 출력한다."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path)
    parser.add_argument("--expected-sha256")
    arguments = parser.parse_args()
    core = ov.Core()
    if "GPU" not in core.available_devices:
        raise SystemExit("OPENVINO_GPU_UNAVAILABLE")
    if arguments.model is None:
        parameter = ov.opset13.parameter([1, 3, 32, 32], np.float32, name="pixels")
        weights = ov.opset13.constant(np.ones((4, 3, 3, 3), dtype=np.float32))
        convolution = ov.opset13.convolution(
            parameter,
            weights,
            strides=[1, 1],
            pads_begin=[1, 1],
            pads_end=[1, 1],
            dilations=[1, 1],
        )
        model = ov.Model([ov.opset13.relu(convolution)], [parameter], "s4_7d_gpu_probe")
        model_sha256 = hashlib.sha256(b"s4-7d-openvino-gpu-probe-v1").hexdigest()
        input_value = np.ones((1, 3, 32, 32), dtype=np.float32)
        model_kind = "SYNTHETIC_HARDWARE_PROBE"
    else:
        model_path = arguments.model.resolve()
        if not model_path.is_file() or not arguments.expected_sha256:
            raise SystemExit("OPENVINO_MODEL_INVALID")
        model_sha256 = _sha256(model_path)
        if model_sha256 != arguments.expected_sha256:
            raise SystemExit("OPENVINO_MODEL_DIGEST_MISMATCH")
        model = core.read_model(model_path)
        if len(model.inputs) != 1 or len(model.outputs) != 1:
            raise SystemExit("OPENVINO_MODEL_SHAPE_INVALID")
        model.reshape({model.input(0): [1, 3, 48, 320]})
        input_value = np.ones((1, 3, 48, 320), dtype=np.float32)
        model_kind = "PP_OCRV6_SMALL_RECOGNITION"
    started = time.perf_counter()
    compiled = core.compile_model(model, "GPU", {"PERFORMANCE_HINT": "LATENCY"})
    output = compiled([input_value])[0]
    elapsed = time.perf_counter() - started
    execution_devices = [str(value) for value in compiled.get_property("EXECUTION_DEVICES")]
    if not execution_devices or any(not value.startswith("GPU") for value in execution_devices):
        raise SystemExit("OPENVINO_GPU_SILENT_FALLBACK")
    receipt = {
        "compileInferVerified": True,
        "deviceName": core.get_property("GPU", "FULL_DEVICE_NAME"),
        "elapsedSeconds": elapsed,
        "executionDevices": execution_devices,
        "host": platform.platform(),
        "modelKind": model_kind,
        "modelSha256": model_sha256,
        "openvinoDevice": "GPU",
        "openvinoVersion": ov.__version__,
        "outputSha256": hashlib.sha256(output.tobytes()).hexdigest(),
        "silentFallbackDetected": False,
    }
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
