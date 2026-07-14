from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


NaverSearchProfile = Literal["legacy", "api-hub"]

_REPOSITORY_ROOT = Path(__file__).resolve().parents[6]
_PYTHON_SERVICE_ROOT = Path(__file__).resolve().parents[3]


class NaverSettings(BaseSettings):
    """Naver collector가 공유해도 되는 profile·호출·응답 상한만 보관한다.

    인증정보는 이 public 설정 모델에 선언하지 않고 향후 private transport가 send 시점에만 읽는다.
    """

    model_config = SettingsConfigDict(
        env_file=(_REPOSITORY_ROOT / ".env",),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    naver_search_profile: NaverSearchProfile = Field(
        default="legacy",
        validation_alias="NAVER_SEARCH_PROFILE",
    )
    naver_batch_size: int = Field(default=4, ge=1, le=4, validation_alias="NAVER_BATCH_SIZE")
    naver_display: int = Field(default=10, ge=1, le=20, validation_alias="NAVER_DISPLAY")
    naver_max_calls_per_run: int = Field(
        default=8,
        ge=1,
        le=8,
        validation_alias="NAVER_MAX_CALLS_PER_RUN",
    )
    naver_response_max_bytes: int = Field(
        default=512 * 1024,
        ge=1,
        le=1024 * 1024,
        validation_alias="NAVER_RESPONSE_MAX_BYTES",
    )
    naver_json_max_depth: int = Field(
        default=6,
        ge=1,
        le=8,
        validation_alias="NAVER_JSON_MAX_DEPTH",
    )
    naver_connect_timeout_seconds: float = Field(
        default=2.0,
        gt=0,
        le=3.0,
        validation_alias="NAVER_CONNECT_TIMEOUT_SECONDS",
    )
    naver_read_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        le=8.0,
        validation_alias="NAVER_READ_TIMEOUT_SECONDS",
    )
    naver_write_timeout_seconds: float = Field(
        default=2.0,
        gt=0,
        le=3.0,
        validation_alias="NAVER_WRITE_TIMEOUT_SECONDS",
    )
    naver_pool_timeout_seconds: float = Field(
        default=1.0,
        gt=0,
        le=2.0,
        validation_alias="NAVER_POOL_TIMEOUT_SECONDS",
    )
    naver_logical_deadline_seconds: float = Field(
        default=12.0,
        gt=0,
        le=20.0,
        validation_alias="NAVER_LOGICAL_DEADLINE_SECONDS",
    )
    snapshot_root: Path = Field(
        default=_PYTHON_SERVICE_ROOT / "data" / "source_snapshots",
        validation_alias="SOURCE_SNAPSHOT_ROOT",
    )

    @property
    def search_profile(self) -> NaverSearchProfile:
        """운영자가 명시한 profile을 반환하며 날짜 기반 자동 전환은 하지 않는다."""
        return self.naver_search_profile

    @property
    def batch_size(self) -> int:
        """한 실행에서 순서대로 처리할 감사된 종목 수를 반환한다."""
        return self.naver_batch_size

    @property
    def display(self) -> int:
        """검색 query 한 건에 요청할 결과 수를 project hard cap 안에서 반환한다."""
        return self.naver_display

    @property
    def max_calls_per_run(self) -> int:
        """재시도를 포함한 한 실행의 physical attempt 상한을 반환한다."""
        return self.naver_max_calls_per_run

    @property
    def response_max_bytes(self) -> int:
        """압축 해제 후 provider 응답 byte 상한을 반환한다."""
        return self.naver_response_max_bytes

    @property
    def json_max_depth(self) -> int:
        """provider JSON 구조의 최대 중첩 깊이를 반환한다."""
        return self.naver_json_max_depth

    @property
    def connect_timeout_seconds(self) -> float:
        """fixed-origin connection의 lower-only timeout을 반환한다."""
        return self.naver_connect_timeout_seconds

    @property
    def read_timeout_seconds(self) -> float:
        """bounded response stream read timeout을 반환한다."""
        return self.naver_read_timeout_seconds

    @property
    def write_timeout_seconds(self) -> float:
        """News GET request write timeout을 반환한다."""
        return self.naver_write_timeout_seconds

    @property
    def pool_timeout_seconds(self) -> float:
        """HTTP connection pool 대기 timeout을 반환한다."""
        return self.naver_pool_timeout_seconds

    @property
    def logical_deadline_seconds(self) -> float:
        """retry/backoff를 포함한 query 전체 실행 deadline을 반환한다."""
        return self.naver_logical_deadline_seconds
