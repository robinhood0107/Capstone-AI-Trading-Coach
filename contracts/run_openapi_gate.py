from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Final, Mapping, Sequence

_SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_REPO_ROOT))

from contracts.openapi_env import OpenApiEnvironmentError, parse_openapi_environment


REPO_ROOT = _SCRIPT_REPO_ROOT
COMPOSE_FILE = REPO_ROOT / "infra" / "docker-compose.infra.yml"
GRADLEW = REPO_ROOT / "workspaces" / "decision-platform" / "spring-api" / "gradlew"
GENERATED_OPENAPI = (
    REPO_ROOT
    / "workspaces"
    / "decision-platform"
    / "spring-api"
    / "build"
    / "openapi.json"
)
TRACKED_OPENAPI = REPO_ROOT / "contracts" / "openapi" / "openapi.json"
FIXTURE_PORT = 55_432

_INHERITED_ENV_ALLOWLIST: Final[tuple[str, ...]] = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "JAVA_HOME",
    "GRADLE_USER_HOME",
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "XDG_RUNTIME_DIR",
    "TMPDIR",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
    "TERM",
    "CI",
    "WSL_DISTRO_NAME",
)


class OpenApiGateError(RuntimeError):
    """격리 OpenAPI 생성기의 안전 전제나 subprocess gate가 실패할 때 발생한다."""


def _explicit_process_environment(values: Mapping[str, str]) -> dict[str, str]:
    environment = {
        name: os.environ[name]
        for name in _INHERITED_ENV_ALLOWLIST
        if name in os.environ
    }
    environment.update(values)
    environment.update(
        {
            "SPRING_PROFILES_ACTIVE": "openapi",
            "SERVER_PORT": "18080",
            "COMPOSE_DISABLE_ENV_FILE": "1",
        }
    )
    # Docker Desktop의 Windows CLI를 경유하는 WSL에서도 fixture 변수만 전달하고 기존 WSLENV 주입은 버린다.
    environment["WSLENV"] = ":".join((*values, "COMPOSE_DISABLE_ENV_FILE"))
    return environment


def _require_fixture_port_available() -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        probe.bind(("127.0.0.1", FIXTURE_PORT))
    except OSError as error:
        raise OpenApiGateError(
            f"Isolated PostgreSQL host port {FIXTURE_PORT} is already in use."
        ) from error
    finally:
        probe.close()


def _run(
    command: Sequence[str],
    *,
    environment: Mapping[str, str],
    quiet: bool = False,
) -> None:
    stdout = subprocess.DEVNULL if quiet else None
    stderr = subprocess.DEVNULL if quiet else None
    completed = subprocess.run(
        list(command),
        cwd=REPO_ROOT,
        env=dict(environment),
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
        check=False,
    )
    if completed.returncode != 0:
        raise OpenApiGateError(
            f"Command failed with exit {completed.returncode}: {' '.join(command[:4])}"
        )


def _compose_command(project_name: str, *arguments: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        project_name,
        "--env-file",
        "/dev/null",
        "-f",
        str(COMPOSE_FILE),
        *arguments,
    ]


def run_gate(env_file: Path, *, write: bool) -> None:
    values = parse_openapi_environment(env_file)
    environment = _explicit_process_environment(values)
    _require_fixture_port_available()
    project_name = f"s21-openapi-{os.getpid()}"
    primary_error: BaseException | None = None
    cleanup_required = False

    try:
        # compose가 일부 리소스를 만든 뒤 health wait에서 실패해도 같은 고유 project를 정리한다.
        cleanup_required = True
        _run(
            _compose_command(project_name, "up", "-d", "--wait", "postgres"),
            environment=environment,
        )
        _run(
            [
                str(GRADLEW),
                "-p",
                "workspaces/decision-platform/spring-api",
                "--no-daemon",
                "cleanOpenApiOutput",
                "generateOpenApiDocs",
            ],
            environment=environment,
        )
        if not GENERATED_OPENAPI.is_file() or GENERATED_OPENAPI.stat().st_size == 0:
            raise OpenApiGateError("Gradle did not create a non-empty OpenAPI JSON file.")
        normalizer_action = "--write" if write else "--check"
        _run(
            [
                sys.executable,
                "contracts/normalize_openapi.py",
                normalizer_action,
                "--implementation",
                "--input",
                str(GENERATED_OPENAPI),
                "--expected",
                str(TRACKED_OPENAPI),
            ],
            environment=environment,
        )
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if cleanup_required:
            try:
                _run(
                    _compose_command(
                        project_name,
                        "down",
                        "--volumes",
                        "--remove-orphans",
                    ),
                    environment=environment,
                    quiet=primary_error is not None,
                )
            except BaseException:
                if primary_error is None:
                    raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate the actual Spring OpenAPI against an isolated PostgreSQL fixture "
            "without sourcing dotenv text."
        )
    )
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write the normalized tracked OpenAPI instead of checking drift.",
    )
    arguments = parser.parse_args()
    try:
        run_gate(arguments.env_file, write=arguments.write)
    except (OpenApiEnvironmentError, OpenApiGateError, OSError) as error:
        print(f"OpenAPI gate failed: {error}", file=sys.stderr)
        return 1
    print("OpenAPI generation and drift gate succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
