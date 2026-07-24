"""offline-only source fixture가 메모리로 들어오기 전 byte bound를 강제한다."""

from pathlib import Path


def read_bounded_fixture(path: Path, *, max_bytes: int, label: str) -> bytes:
    """파일 크기를 read 전후로 검증해 sparse/교체 파일도 정해진 메모리 상한을 넘기지 않는다."""
    if max_bytes <= 0:
        raise ValueError("fixture byte bound must be positive")
    size = path.stat().st_size
    if size <= 0 or size > max_bytes:
        raise ValueError(f"{label} fixture exceeds its byte bound")
    payload = path.read_bytes()
    if len(payload) != size or len(payload) > max_bytes:
        raise ValueError(f"{label} fixture changed while it was being read")
    return payload
