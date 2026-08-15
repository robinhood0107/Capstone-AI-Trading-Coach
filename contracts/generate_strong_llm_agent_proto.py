from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PROTO = REPO_ROOT / "contracts/internal/proto/strong_llm_agent.proto"
PYTHON_OUT = REPO_ROOT / "workspaces/decision-platform/python-services/app/generated"
OUTPUT_NAMES = (
    "strong_llm_agent_pb2.py",
    "strong_llm_agent_pb2.pyi",
    "strong_llm_agent_pb2_grpc.py",
)


class StrongLlmProtoGenerationError(RuntimeError):
    """내부 bidi 계약 생성 실패가 공개 RAG proto drift처럼 숨겨지지 않게 한다."""


def _generate(target: Path) -> dict[str, bytes]:
    command = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"--proto_path={PROTO.parent}",
        f"--python_out={target}",
        f"--pyi_out={target}",
        f"--grpc_python_out={target}",
        str(PROTO),
    ]
    result = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, check=False)
    if result.returncode != 0:
        raise StrongLlmProtoGenerationError("strong LLM proto generation failed")
    generated = {name: (target / name).read_bytes() for name in OUTPUT_NAMES}
    grpc_name = "strong_llm_agent_pb2_grpc.py"
    grpc_text = generated[grpc_name].decode("utf-8").replace(
        "import strong_llm_agent_pb2 as strong__llm__agent__pb2",
        "from app.generated import strong_llm_agent_pb2 as strong__llm__agent__pb2",
    )
    if "from app.generated import strong_llm_agent_pb2" not in grpc_text:
        raise StrongLlmProtoGenerationError("generated gRPC import rewrite failed")
    generated[grpc_name] = grpc_text.encode("utf-8")
    return generated


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="strong-llm-proto-") as raw:
        generated = _generate(Path(raw))
    drift = []
    for name, payload in generated.items():
        destination = PYTHON_OUT / name
        if args.check:
            if not destination.is_file() or destination.read_bytes() != payload:
                drift.append(name)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
    if drift:
        raise StrongLlmProtoGenerationError("generated artifacts drifted: " + ", ".join(drift))
    if not args.check:
        digest = hashlib.sha256(PROTO.read_bytes()).hexdigest()
        print(f"strong_llm_agent.proto sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
