from __future__ import annotations

import inspect
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.p1_owner import team_a_acceptance_seed as seed


def test_dsn_is_offline_loopback_compose_only() -> None:
    valid = {
        "P1_OFFLINE_DEMO": "true",
        "P1_TEAM_A_ACCEPTANCE_DATABASE_DSN": "postgresql://postgres:secret@postgres:5432/capstone_p1?sslmode=disable",
    }
    assert seed._dsn(valid) == valid["P1_TEAM_A_ACCEPTANCE_DATABASE_DSN"]
    for value in (
        "postgresql://postgres:secret@example.org:5432/capstone_p1",
        "postgresql://decision_app:secret@postgres:5432/capstone_p1",
        "postgresql://postgres:secret@postgres:5432/production",
    ):
        with pytest.raises(ValueError):
            seed._dsn({**valid, "P1_TEAM_A_ACCEPTANCE_DATABASE_DSN": value})
    with pytest.raises(ValueError):
        seed._dsn({**valid, "P1_OFFLINE_DEMO": "false"})


def test_reset_is_owner_bounded_and_never_drops_schema() -> None:
    statements = "\n".join(seed._RESET_STATEMENTS).upper()
    assert "DROP " not in statements
    assert "TRUNCATE " not in statements
    assert "USR_DEMO_ADMIN" not in statements
    assert all(
        "%S" in statement.upper() or "TEAM-A-ACCEPTANCE-V1" in statement.upper()
        for statement in seed._RESET_STATEMENTS
    )


def test_fixed_clock_is_current_utc_and_bounded() -> None:
    current = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    assert seed._fixed_instant({"P1_TEAM_A_ACCEPTANCE_FIXED_CLOCK": current}) == current
    with pytest.raises(ValueError):
        seed._fixed_instant({"P1_TEAM_A_ACCEPTANCE_FIXED_CLOCK": "2020-01-01T00:00:00Z"})


def test_seed_has_no_provider_transport_or_raw_secret_output() -> None:
    source = inspect.getsource(seed)
    assert "requests." not in source
    assert "urllib.request" not in source
    assert "KIS_APP" not in source
    assert "print(dsn" not in source
    assert "PROVIDER_CALLS=0" in source
    assert "acceptance_requires_disposable_database" in source


def test_module_is_packaged_as_a_regular_file() -> None:
    path = Path(seed.__file__)
    assert path.is_file()
    assert not path.is_symlink()
