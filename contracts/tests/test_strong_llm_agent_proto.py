from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from google.protobuf import descriptor_pb2


ROOT = Path(__file__).resolve().parents[2]
PROTO = ROOT / "contracts/internal/proto/strong_llm_agent.proto"


def test_internal_strong_llm_proto_is_bidi_and_public_protos_are_untouched() -> None:
    with tempfile.TemporaryDirectory(prefix="strong-llm-descriptor-") as raw:
        descriptor = Path(raw) / "descriptor.pb"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "grpc_tools.protoc",
                f"--proto_path={PROTO.parent}",
                f"--descriptor_set_out={descriptor}",
                str(PROTO),
            ],
            cwd=ROOT,
            check=False,
        )
        assert result.returncode == 0
        file_descriptor = descriptor_pb2.FileDescriptorSet.FromString(descriptor.read_bytes()).file[0]

    assert file_descriptor.package == "capstone.decision.internal.s49"
    service = file_descriptor.service[0]
    method = service.method[0]
    assert service.name == "StrongLlmAgentService"
    assert method.name == "Generate"
    assert method.client_streaming is True
    assert method.server_streaming is True
    assert {message.name for message in file_descriptor.message_type} >= {
        "HostEvent",
        "AgentEvent",
        "StartRun",
        "ProviderCallPermit",
        "ToolResult",
        "ProviderCallPlanned",
        "RegisterGroundingRoots",
        "Completed",
        "Failed",
    }


def test_generated_internal_proto_is_current() -> None:
    result = subprocess.run(
        [sys.executable, "contracts/generate_strong_llm_agent_proto.py", "--check"],
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 0
