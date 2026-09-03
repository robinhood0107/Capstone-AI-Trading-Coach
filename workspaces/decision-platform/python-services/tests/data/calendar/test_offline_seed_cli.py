"""오프라인 XKRX seeding CLI 의 입력 판정과 연도 관용을 고정한다.

DB 쓰기 경로는 test_collector / test_merger 가 이미 덮는다. 여기서는 이 CLI 가 새로 들고 온
두 가지만 잠근다.

1. 연도 목록 파싱 경계 - 상한을 넘거나 숫자가 아니면 거부한다.
2. **연도별 관용** - pinned XKRX 달력은 오늘부터 약 1년 뒤에서 끝나므로(실측 상한
   2027-09-03) "올해와 내년"의 마지막 연도가 범위를 벗어나는 것이 정상이다. 그 한 해 때문에
   쓸 수 있는 세션까지 버리면 arm 이 영구히 막힌다. 이 관용이 회귀하지 않게 잠근다.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest import mock

from app.data.calendar import offline_seed_cli
from app.data.calendar.errors import AdapterValidationError


class OfflineSeedYearParsingTest(unittest.TestCase):
    def test_blank_defaults_to_this_year_and_next(self) -> None:
        current = datetime.now(UTC).date().year
        self.assertEqual(offline_seed_cli._parse_years(""), (current, current + 1))

    def test_explicit_years_are_sorted_and_deduplicated(self) -> None:
        self.assertEqual(offline_seed_cli._parse_years("2027, 2026 ,2026"), (2026, 2027))

    def test_non_numeric_year_is_rejected(self) -> None:
        with self.assertRaises(AdapterValidationError):
            offline_seed_cli._parse_years("2026,olleh")

    def test_year_list_beyond_the_bound_is_rejected(self) -> None:
        with self.assertRaises(AdapterValidationError):
            offline_seed_cli._parse_years("2024,2025,2026,2027")

    def test_empty_token_list_is_rejected(self) -> None:
        with self.assertRaises(AdapterValidationError):
            offline_seed_cli._parse_years(" , ,")


class OfflineSeedYearToleranceTest(unittest.TestCase):
    """달력 경계를 넘는 연도 하나가 전체 적재를 막지 않아야 한다."""

    def _run_with(self, build: object) -> tuple[int, str]:
        with (
            mock.patch.dict(
                "os.environ",
                {
                    "P1_CALENDAR_OFFLINE_SEED_DSN": "postgresql://ignored",
                    "P1_CALENDAR_OFFLINE_SEED_YEARS": "2026,2027",
                },
                clear=False,
            ),
            mock.patch.object(offline_seed_cli, "psycopg") as fake_psycopg,
            mock.patch.object(offline_seed_cli, "_attest_collector_authority"),
            mock.patch.object(offline_seed_cli, "build_xkrx_sessions", build),
            mock.patch.object(offline_seed_cli, "merge_trading_session") as fake_merge,
            mock.patch.object(offline_seed_cli, "CalendarRepository") as fake_repository,
            mock.patch("builtins.print") as fake_print,
        ):
            fake_psycopg.Error = RuntimeError
            fake_merge.side_effect = lambda session, **_: session
            code = offline_seed_cli.main()
            lines = " ".join(str(call.args[0]) for call in fake_print.call_args_list)
        self.assertTrue(fake_repository.called or code != 0)
        return code, lines

    def test_out_of_bounds_year_is_skipped_and_the_rest_is_seeded(self) -> None:
        session = mock.Mock()
        session.is_open = True
        session.session_date = datetime.now(UTC).date()

        def build(year: int) -> list[object]:
            if year == 2027:
                raise ValueError("DateOutOfBounds")
            return [session]

        code, lines = self._run_with(build)
        self.assertEqual(code, 0)
        self.assertIn("P1_CALENDAR_OFFLINE_SEED=SEEDED", lines)
        self.assertIn("seeded=1", lines)
        self.assertIn("skippedYears=2027:ValueError", lines)

    def test_every_year_out_of_bounds_is_reported_as_an_input_error(self) -> None:
        def build(year: int) -> list[object]:
            raise ValueError("DateOutOfBounds")

        code, lines = self._run_with(build)
        self.assertEqual(code, 2)
        self.assertIn("P1_CALENDAR_OFFLINE_SEED=NO_SESSIONS", lines)


if __name__ == "__main__":
    unittest.main()
