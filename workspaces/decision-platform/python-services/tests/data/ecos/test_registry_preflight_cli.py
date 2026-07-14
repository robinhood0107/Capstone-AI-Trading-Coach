from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from app.data._shared.redis_quota import QuotaUnavailableError
from app.data.ecos import registry_preflight, registry_preflight_cli
from app.data.ecos.errors import ECOSApplicationError, ECOSParseError
from app.data.ecos.http_client import ECOSHttpClient, ECOSHttpError
from app.data.ecos.models import StatisticItemMetadata, StatisticTableMetadata
from app.data.ecos.series_registry import ECOSSeries
from app.data.ecos.settings import ECOSSettings

_OBSERVED_AT = datetime(2026, 7, 14, 1, 2, 3, tzinfo=UTC)


class _Client:
    def __init__(self, *, fail_stage: str | None = None, searchable: bool = True) -> None:
        self.fail_stage = fail_stage
        self.searchable = searchable
        self.calls: list[tuple[str, str]] = []
        self.closed = False

    def statistic_table_list(self, *, series: ECOSSeries) -> StatisticTableMetadata:
        self.calls.append(("table", series.series_id))
        if self.fail_stage == "table":
            raise RuntimeError(
                "synthetic-ecos-key https://ecos.bok.or.kr/api/private raw provider response"
            )
        return StatisticTableMetadata(
            stat_code=series.stat_code,
            name=f"합성-{series.series_id}-table",
            cycle="D",
            searchable=self.searchable,
        )

    def statistic_item_list(self, *, series: ECOSSeries) -> StatisticItemMetadata:
        self.calls.append(("item", series.series_id))
        if self.fail_stage == "item":
            raise ECOSParseError("invalid ECOS response")
        return StatisticItemMetadata(
            stat_code=series.stat_code,
            item_code=series.item_code1,
            name=f"합성-{series.series_id}-item",
            cycle="D",
            unit="합성단위",
        )

    @property
    def physical_attempt_count(self) -> int:
        return len(self.calls)

    def close(self) -> None:
        self.closed = True


def test_default_path_stops_before_redis_or_provider_client_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        registry_preflight_cli,
        "_build_client",
        lambda: pytest.fail("offline gate must not build a client"),
    )

    assert registry_preflight_cli.main([]) == 2


def test_online_path_makes_exactly_four_calls_and_prints_sanitized_inspection(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _Client()
    monkeypatch.setattr(registry_preflight_cli, "_build_client", lambda: client)
    monkeypatch.setattr(registry_preflight, "_utc_now", lambda: _OBSERVED_AT)

    assert registry_preflight_cli.main(["--online"]) == 0

    assert client.calls == [
        ("table", "policy-rate"),
        ("item", "policy-rate"),
        ("table", "krw-usd-rate"),
        ("item", "krw-usd-rate"),
    ]
    assert client.closed is True
    rendered = capsys.readouterr().out
    payload = json.loads(rendered)
    assert payload["operation"] == "ecos-registry-preflight"
    assert payload["activationRequired"] is True
    assert payload["canActivate"] is False
    assert payload["physicalAttemptCount"] == 4
    assert payload["observedAt"] == "2026-07-14T01:02:03Z"
    assert [entry["seriesId"] for entry in payload["series"]] == [
        "policy-rate",
        "krw-usd-rate",
    ]
    lowered = rendered.lower()
    assert "credential" not in lowered
    assert "raw" not in lowered
    assert "https://" not in lowered


def test_online_failure_is_not_retried_and_drops_provider_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _Client(fail_stage="table")
    monkeypatch.setattr(registry_preflight_cli, "_build_client", lambda: client)

    assert registry_preflight_cli.main(["--online"]) == 1

    assert client.calls == [("table", "policy-rate")]
    assert client.closed is True
    rendered = capsys.readouterr().out
    assert rendered == (
        "source=ecos operation=registry_preflight code=preflight_failed physicalAttemptCount=1\n"
    )
    assert "synthetic-ecos-key" not in rendered
    assert "https://" not in rendered
    assert "provider" not in rendered


def test_online_item_parse_failure_reports_only_safe_code_and_actual_attempt_count(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _Client(fail_stage="item")
    monkeypatch.setattr(registry_preflight_cli, "_build_client", lambda: client)

    assert registry_preflight_cli.main(["--online"]) == 1

    assert client.calls == [("table", "policy-rate"), ("item", "policy-rate")]
    assert client.closed is True
    assert capsys.readouterr().out == (
        "source=ecos operation=registry_preflight code=invalid_response physicalAttemptCount=2\n"
    )


def test_online_non_searchable_table_fails_before_item_call(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _Client(searchable=False)
    monkeypatch.setattr(registry_preflight_cli, "_build_client", lambda: client)

    assert registry_preflight_cli.main(["--online"]) == 1

    assert client.calls == [("table", "policy-rate")]
    assert client.closed is True
    assert capsys.readouterr().out == (
        "source=ecos operation=registry_preflight code=preflight_failed physicalAttemptCount=1\n"
    )


def test_online_extra_attempt_counter_preserves_the_observed_count(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class ExtraAttemptClient(_Client):
        @property
        def physical_attempt_count(self) -> int:
            return 5

    client = ExtraAttemptClient()
    monkeypatch.setattr(registry_preflight_cli, "_build_client", lambda: client)

    assert registry_preflight_cli.main(["--online"]) == 1

    assert client.closed is True
    assert capsys.readouterr().out == (
        "source=ecos operation=registry_preflight code=preflight_failed physicalAttemptCount=5\n"
    )


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (ECOSHttpError("response_invalid", status_code=200), "invalid_response"),
        (ECOSHttpError("http_429", status_code=429), "http_429"),
        (
            ECOSApplicationError("ERROR-602", retryable=False, cooldown_seconds=1_800),
            "ERROR-602",
        ),
        (QuotaUnavailableError("synthetic-secret-must-not-escape"), "quota_unavailable"),
    ],
)
def test_online_failure_classifies_only_allowlisted_operator_codes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: Exception,
    expected_code: str,
) -> None:
    client = _Client()
    monkeypatch.setattr(registry_preflight_cli, "_build_client", lambda: client)

    def fail_inspection(**_: object) -> None:
        raise failure

    monkeypatch.setattr(registry_preflight_cli, "inspect_registry_metadata", fail_inspection)

    assert registry_preflight_cli.main(["--online"]) == 1

    rendered = capsys.readouterr().out
    assert rendered == (
        f"source=ecos operation=registry_preflight code={expected_code} physicalAttemptCount=0\n"
    )
    assert "synthetic-secret" not in rendered
    assert client.closed is True


def test_invalid_argv_cannot_echo_a_mistaken_secret_or_url(
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret_argument = "--synthetic-key=https://ecos.bok.or.kr/private?key=synthetic-secret"

    assert registry_preflight_cli.main(["--online", secret_argument]) == 2

    captured = capsys.readouterr()
    assert captured.out == "source=ecos operation=registry_preflight code=invalid_arguments\n"
    assert captured.err == ""
    assert "synthetic-secret" not in f"{captured.out}{captured.err}"
    assert "https://" not in f"{captured.out}{captured.err}"


def test_unicode_escaped_credential_echo_cannot_reach_preflight_cli(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    marker = "synthetic-ecos-key"
    escaped = "".join(f"\\u{ord(character):04x}" for character in marker)
    body = (
        json.dumps(
            {
                "StatisticTableList": {
                    "list_total_count": 1,
                    "row": [
                        {
                            "STAT_CODE": "722Y001",
                            "STAT_NAME": marker,
                            "CYCLE": "D",
                            "SRCH_YN": "Y",
                        }
                    ],
                }
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        .replace(marker, escaped)
        .encode()
    )
    assert json.loads(body)["StatisticTableList"]["row"][0]["STAT_NAME"] == marker
    attempts = 0

    class Quota:
        def reserve(self, *, attempt_id: str) -> None:
            assert attempt_id

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=body,
        )

    client = ECOSHttpClient._for_tests(
        ECOSSettings(_env_file=None),
        transport=httpx.MockTransport(handler),
        quota=Quota(),
        credential=SecretStr(marker),
    )
    monkeypatch.setattr(registry_preflight_cli, "_build_client", lambda: client)

    assert registry_preflight_cli.main(["--online"]) == 1

    rendered = capsys.readouterr().out
    assert attempts == 1
    assert rendered == (
        "source=ecos operation=registry_preflight code=preflight_failed physicalAttemptCount=1\n"
    )
    assert marker not in rendered
