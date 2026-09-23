"""배포용 compose 를 개발용 compose 에서 생성한다.

왜 생성하는가 - 손으로 두 벌을 유지하면 반드시 어긋난다. 개발용 `compose.yml` 이 단일
진실이고, 배포용은 거기서 두 가지만 덜어낸 파생물이다.

    1. `build:` 를 뺀다. 배포자는 소스를 갖고 있지 않다.
    2. 이미지에 구운 정적 호스트 바인드를 뺀다. 초기화 SQL, 비밀 로더, 54M 시드,
       백테스트 설정은 이제 이미지 안에 있다.

덜어내지 않는 것:

    - `secrets:` — 운영자가 넣는 값이다. 이미지에 굽지 않는다.
    - 운영자 상태 바인드(`${P1_RAG_RUNTIME_DIR}` 등) — 배포마다 다르고 컨테이너가 쓰기도
      한다. 기본값이 `/dev/null`·`/nonexistent` 라 없어도 기본 스택은 뜬다.
    - 이름 있는 볼륨 — 상태는 볼륨에 남아야 한다.

사용:  python3 deploy/p1/generate_release_compose.py [--check]
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Any

import yaml

HERE = pathlib.Path(__file__).resolve().parent
SOURCE = HERE / "compose.yml"
TARGET = HERE / "compose.release.yml"

#: 이미지에 구운 정적 파일. 이 접두사로 시작하는 바인드는 배포용에서 지운다.
#: 굽는 위치는 docker/postgres-pgvector.Dockerfile, docker/redis.Dockerfile,
#: docker/decision-platform.Dockerfile 이다.
BAKED_PREFIXES = (
    "./docker/secret-entrypoint.sh",
    "./seed",
    "../../infra/init/",
    "../../shared-docs/backtest_config.yaml",
)

#: 이미지에 구운 산출물이 이미 들어 있는 마운트 지점. 배포용에서는 이름 있는 볼륨이
#: 그 위를 빈 디렉터리로 덮어 버리므로 지운다. 채우는 잡(`return-engine-preview-prepare`)
#: 은 torch 가 들어간 별도 이미지를 요구해 배포 전제(이미지 다섯 벌)와 맞지 않는다.
BAKED_MOUNT_TARGETS = ("/artifacts/team-b",)

HEADER = """# 생성 파일이다. 손으로 고치지 않는다.
#
# 만드는 법:  python3 deploy/p1/generate_release_compose.py
# 확인하는 법: python3 deploy/p1/generate_release_compose.py --check
#
# 개발용 compose.yml 에서 build 지시자와 이미지에 구운 정적 바인드만 덜어낸 것이다.
# 소스 트리 없이 이미지와 비밀값 파일만으로 띄우는 용도다.
#
#   docker compose -f compose.release.yml --env-file release.env up -d --wait
#
# 운영자가 준비할 것은 두 가지뿐이다.
#   1. 이미지 (P1_SPRING_IMAGE, P1_PYTHON_IMAGE, P1_POSTGRES_IMAGE, P1_REDIS_IMAGE,
#      P1_DASHBOARD_IMAGE)
#   2. 비밀값 파일 디렉터리 (P1_SECRETS_DIR)
"""


class IndentedSafeDumper(yaml.SafeDumper):
    """Emit sequence children under their keys so the generated YAML lints."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        super().increase_indent(flow, indentless=False)


def strip_service(name: str, service: dict[str, Any]) -> dict[str, Any]:
    """한 서비스에서 build 와 구워진 바인드를 덜어낸다."""

    result = dict(service)
    result.pop("build", None)

    volumes = result.get("volumes")
    if isinstance(volumes, list):
        kept = [
            volume
            for volume in volumes
            if not (
                isinstance(volume, str)
                and (
                    any(volume.startswith(prefix) for prefix in BAKED_PREFIXES)
                    or any(
                        f":{target}:" in volume or volume.endswith(f":{target}")
                        for target in BAKED_MOUNT_TARGETS
                    )
                )
            )
        ]
        if kept:
            result["volumes"] = kept
        else:
            result.pop("volumes", None)
    return result


def build_release_document() -> dict[str, Any]:
    document = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or "services" not in document:
        raise SystemExit("compose.yml 을 읽지 못했다")

    # x-* 확장 키는 앵커를 펼친 뒤라 더 필요 없다.
    document = {
        key: value for key, value in document.items() if not key.startswith("x-")
    }
    document["services"] = {
        name: strip_service(name, service)
        for name, service in document["services"].items()
    }
    return document


def render(document: dict[str, Any]) -> str:
    body = yaml.dump(
        document,
        Dumper=IndentedSafeDumper,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=100,
    )
    return HEADER + "\n" + body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="파일을 쓰지 않고 현재 내용과 같은지만 확인한다.",
    )
    args = parser.parse_args(argv)

    rendered = render(build_release_document())

    if args.check:
        if not TARGET.is_file():
            print("P1_RELEASE_COMPOSE=MISSING")
            return 1
        if TARGET.read_text(encoding="utf-8") != rendered:
            print("P1_RELEASE_COMPOSE=STALE")
            return 1
        print("P1_RELEASE_COMPOSE=CURRENT")
        return 0

    TARGET.write_text(rendered, encoding="utf-8")
    services = len(yaml.safe_load(rendered)["services"])
    print(f"P1_RELEASE_COMPOSE=GENERATED services={services}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
