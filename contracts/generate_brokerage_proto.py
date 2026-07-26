from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from google.protobuf import descriptor_pb2

REPO_ROOT = Path(__file__).resolve().parents[1]
PROTO_PATH = REPO_ROOT / "contracts/proto/brokerage.proto"
DESCRIPTOR_PATH = REPO_ROOT / "contracts/proto/brokerage.descriptor.pb"
DESCRIPTOR_SHA_PATH = REPO_ROOT / "contracts/proto/brokerage.descriptor.sha256"
PYTHON_GENERATED_DIR = REPO_ROOT / "workspaces/decision-platform/python-services/app/generated"
OUTPUTS = {
    DESCRIPTOR_PATH: b"",
    DESCRIPTOR_SHA_PATH: b"",
    PYTHON_GENERATED_DIR / "__init__.py": b'"""Tracked Python gRPC codegen package for Decision Platform contracts."""\n',
    PYTHON_GENERATED_DIR / "brokerage_pb2.py": b"",
    PYTHON_GENERATED_DIR / "brokerage_pb2.pyi": b"",
    PYTHON_GENERATED_DIR / "brokerage_pb2_grpc.py": b"",
}


class ProtoGenerationError(RuntimeError):
    """Generated brokerage proto artifacts drifted from the committed contract."""


def _output_relative_path(path: Path) -> Path:
    try:
        relative = path.relative_to(REPO_ROOT)
    except ValueError as error:
        raise ProtoGenerationError("generated proto output escaped the repository root") from error
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ProtoGenerationError("generated proto output path is invalid")
    return relative


def _open_output_parent(path: Path, *, create: bool) -> tuple[int, str]:
    relative = _output_relative_path(path)
    directory_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        root_status = REPO_ROOT.lstat()
    except OSError as error:
        raise ProtoGenerationError("repository root is not accessible") from error
    if stat.S_ISLNK(root_status.st_mode) or not stat.S_ISDIR(root_status.st_mode):
        raise ProtoGenerationError("repository root must be a non-symlink directory")

    try:
        directory_fd = os.open(REPO_ROOT, directory_flags)
    except OSError as error:
        raise ProtoGenerationError("repository root could not be opened safely") from error
    try:
        for component in relative.parts[:-1]:
            if create:
                try:
                    os.mkdir(component, mode=0o755, dir_fd=directory_fd)
                except FileExistsError:
                    pass
            try:
                next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            except FileNotFoundError:
                raise
            except OSError as error:
                raise ProtoGenerationError(
                    "generated proto output parent must be a non-symlink directory"
                ) from error
            os.close(directory_fd)
            directory_fd = next_fd
        return directory_fd, relative.name
    except Exception:
        os.close(directory_fd)
        raise


def _read_regular_output(path: Path) -> bytes | None:
    try:
        directory_fd, name = _open_output_parent(path, create=False)
    except FileNotFoundError:
        return None
    try:
        try:
            target_status = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(target_status.st_mode):
            raise ProtoGenerationError("generated proto output must be a regular non-symlink file")
        try:
            output_fd = os.open(
                name,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
        except OSError as error:
            raise ProtoGenerationError("generated proto output could not be opened safely") from error
        try:
            if not stat.S_ISREG(os.fstat(output_fd).st_mode):
                raise ProtoGenerationError("generated proto output changed type during validation")
            chunks: list[bytes] = []
            while chunk := os.read(output_fd, 64 * 1024):
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(output_fd)
    finally:
        os.close(directory_fd)


def _validate_replace_target(directory_fd: int, name: str) -> None:
    try:
        target_status = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(target_status.st_mode):
        raise ProtoGenerationError("generated proto output must be a regular non-symlink file")


def _write_regular_output(path: Path, expected: bytes) -> None:
    directory_fd, name = _open_output_parent(path, create=True)
    temporary_name = f".{name}.{secrets.token_hex(16)}.tmp"
    temporary_created = False
    try:
        _validate_replace_target(directory_fd, name)
        try:
            temporary_fd = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                0o644,
                dir_fd=directory_fd,
            )
            temporary_created = True
        except OSError as error:
            raise ProtoGenerationError("generated proto temporary file could not be created safely") from error
        try:
            remaining = memoryview(expected)
            while remaining:
                written = os.write(temporary_fd, remaining)
                if written <= 0:
                    raise ProtoGenerationError("generated proto temporary write did not progress")
                remaining = remaining[written:]
            os.fsync(temporary_fd)
        finally:
            os.close(temporary_fd)

        _validate_replace_target(directory_fd, name)
        os.replace(
            temporary_name,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        temporary_created = False
        os.fsync(directory_fd)
    except OSError as error:
        raise ProtoGenerationError("generated proto output could not be replaced safely") from error
    finally:
        if temporary_created:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        os.close(directory_fd)


def _run_protoc(output_dir: Path) -> dict[Path, bytes]:
    python_dir = output_dir / "python"
    python_dir.mkdir(parents=True)
    descriptor_path = output_dir / "brokerage.descriptor.pb"
    command = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"--proto_path={PROTO_PATH.parent}",
        f"--python_out={python_dir}",
        f"--pyi_out={python_dir}",
        f"--grpc_python_out={python_dir}",
        f"--descriptor_set_out={descriptor_path}",
        "--include_imports",
        PROTO_PATH.name,
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise ProtoGenerationError("grpc_tools.protoc failed without exposing raw provider data")

    pb2 = (python_dir / "brokerage_pb2.py").read_text(encoding="utf-8")
    pb2_pyi = (python_dir / "brokerage_pb2.pyi").read_bytes()
    pb2_grpc = (python_dir / "brokerage_pb2_grpc.py").read_text(encoding="utf-8")
    pb2_grpc = pb2_grpc.replace("import brokerage_pb2 as brokerage__pb2", "from app.generated import brokerage_pb2 as brokerage__pb2")
    if "from app.generated import brokerage_pb2" not in pb2_grpc:
        raise ProtoGenerationError("Generated gRPC imports are not package-safe")

    descriptor_bytes = descriptor_path.read_bytes()
    descriptor_set = descriptor_pb2.FileDescriptorSet()
    descriptor_set.ParseFromString(descriptor_bytes)
    if len(descriptor_set.file) != 1:
        raise ProtoGenerationError("descriptor set must contain exactly one proto")
    descriptor = descriptor_set.file[0]
    if (
        descriptor.name != "brokerage.proto"
        or descriptor.package != "capstone.decision.v1"
        or descriptor.syntax != "proto3"
    ):
        raise ProtoGenerationError("brokerage proto identity drifted")
    service_names = [service.name for service in descriptor.service]
    if service_names != ["BrokerageService"]:
        raise ProtoGenerationError("brokerage service contract drifted")
    method_names = [method.name for method in descriptor.service[0].method]
    if method_names != [
        "SubmitMockCashOrder",
        "CancelMockCashOrder",
        "GetMockBalance",
        "GetMockBuyable",
    ]:
        raise ProtoGenerationError("brokerage rpc method order drifted")

    descriptor_sha = hashlib.sha256(descriptor_bytes).hexdigest().encode("ascii") + b"\n"
    return {
        DESCRIPTOR_PATH: descriptor_bytes,
        DESCRIPTOR_SHA_PATH: descriptor_sha,
        PYTHON_GENERATED_DIR / "__init__.py": OUTPUTS[PYTHON_GENERATED_DIR / "__init__.py"],
        PYTHON_GENERATED_DIR / "brokerage_pb2.py": pb2.encode("utf-8"),
        PYTHON_GENERATED_DIR / "brokerage_pb2.pyi": pb2_pyi,
        PYTHON_GENERATED_DIR / "brokerage_pb2_grpc.py": pb2_grpc.encode("utf-8"),
    }


def generate(*, check: bool) -> int:
    with tempfile.TemporaryDirectory(prefix="s31-brokerage-proto-") as raw_dir:
        outputs = _run_protoc(Path(raw_dir))
    failures = 0
    for path, expected in outputs.items():
        if check:
            actual = _read_regular_output(path)
            if actual is None:
                print(f"FAIL missing generated proto artifact {path.relative_to(REPO_ROOT)}", file=sys.stderr)
                failures += 1
                continue
            if actual != expected:
                print(f"FAIL generated proto drift {path.relative_to(REPO_ROOT)}", file=sys.stderr)
                failures += 1
                continue
            print(f"PASS generated proto {path.relative_to(REPO_ROOT)}")
        else:
            _write_regular_output(path, expected)
            print(f"WROTE {path.relative_to(REPO_ROOT)}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        return generate(check=args.check)
    except ProtoGenerationError as error:
        print(f"S3.1 brokerage proto generation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
