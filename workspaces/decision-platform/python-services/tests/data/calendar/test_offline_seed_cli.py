"""오프라인 XKRX seeding CLI 의 입력 판정과 경계 처리를 고정한다.

DB 쓰기 경로는 test_collector / test_merger 가 이미 덮는다. 여기서는 이 CLI 가 새로 들고 온
것만 잠근다.

1. 연도 목록 파싱 경계 - 상한을 넘거나 숫자가 아니면 거부한다.
2. **달력 경계까지 잘라서 채운다** - pinned 달력은 유한하다(실측 2006-09-04 ~ 2027-09-03).
   연도 전체를 요구하면 마지막 해가 통째로 거부되어 캘린더가 연말에서 끊기고 그 뒤 arm 이
   다음 세션을 못 찾는다. 부분 연도도 유효한 base 이므로 잘라서 채운다.
3. **이미 있는 세션은 다시 쓰지 않는다** - 날짜로 자르면 LSTM 의 20세션 lookback 구간이
   비어 일일 추론이 DAILY_INFERENCE_HISTORY_INCOMPLETE 로 막힌다. 그래서 날짜가 아니라
   존재 여부로 가른다. 확정 이력 보존이라는 목적은 그대로다.
"""

from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from unittest import mock

from app.data.calendar import offline_seed_cli
from app.data.calendar.errors import AdapterValidationError


def _session(day: date) -> mock.Mock:
    item = mock.Mock()
    item.is_open = True
    item.session_date = day
    return item


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


class _SeedRunner(unittest.TestCase):
    """CLI 를 외부 의존 없이 돌리는 공통 하네스."""

    def _run(
        self,
        *,
        years: str,
        bounds: tuple[date, date],
        existing: set[date],
        built: dict[tuple[date, date], list[mock.Mock]],
    ) -> tuple[int, str, list[date]]:
        def build(start: date, end: date) -> list[mock.Mock]:
            return built.get((start, end), [])

        with (
            mock.patch.dict(
                "os.environ",
                {
                    "P1_CALENDAR_OFFLINE_SEED_DSN": "postgresql://ignored",
                    "P1_CALENDAR_OFFLINE_SEED_YEARS": years,
                },
                clear=False,
            ),
            mock.patch.object(offline_seed_cli, "psycopg") as fake_psycopg,
            mock.patch.object(offline_seed_cli, "_attest_collector_authority"),
            mock.patch.object(offline_seed_cli, "_existing_sessions", return_value=existing),
            mock.patch.object(offline_seed_cli, "xkrx_calendar_bounds", return_value=bounds),
            mock.patch.object(offline_seed_cli, "build_xkrx_sessions_in_range", build),
            mock.patch.object(offline_seed_cli, "merge_trading_session") as fake_merge,
            mock.patch.object(offline_seed_cli, "CalendarRepository") as fake_repository,
            mock.patch("builtins.print") as fake_print,
        ):
            fake_psycopg.Error = RuntimeError
            fake_merge.side_effect = lambda item, **_: item
            code = offline_seed_cli.main()
            lines = " ".join(str(call.args[0]) for call in fake_print.call_args_list)
            written = [
                call.args[0].session_date
                for call in fake_repository.return_value.upsert_trading_session.call_args_list
            ]
        return code, lines, written


class OfflineSeedCalendarBoundTest(_SeedRunner):
    """달력 경계를 넘는 요청이 통째로 버려지지 않는지 잠근다."""

    def test_partial_year_is_clamped_to_the_calendar_bound_and_still_seeded(self) -> None:
        # 달력이 2027-09-03 에서 끝나면 2027 요청은 1월 1일 ~ 9월 3일로 잘려야 한다.
        bound_last = date(2027, 9, 3)
        clamped_range = (date(2027, 1, 1), bound_last)
        code, lines, written = self._run(
            years="2027",
            bounds=(date(2006, 9, 4), bound_last),
            existing=set(),
            built={clamped_range: [_session(date(2027, 1, 4)), _session(bound_last)]},
        )

        self.assertEqual(code, 0)
        self.assertIn("P1_CALENDAR_OFFLINE_SEED=SEEDED", lines)
        self.assertIn("seeded=2", lines)
        self.assertIn("clampedYears=2027:2027-01-01~2027-09-03", lines)
        self.assertEqual(written, [date(2027, 1, 4), bound_last])

    def test_year_entirely_outside_the_calendar_is_an_input_error(self) -> None:
        code, lines, written = self._run(
            years="2030",
            bounds=(date(2006, 9, 4), date(2027, 9, 3)),
            existing=set(),
            built={},
        )

        self.assertEqual(code, 2)
        self.assertIn("P1_CALENDAR_OFFLINE_SEED=NO_SESSIONS", lines)
        self.assertIn("clampedYears=2030:outside", lines)
        self.assertEqual(written, [])

    def test_fully_covered_year_is_not_reported_as_clamped(self) -> None:
        full = (date(2026, 1, 1), date(2026, 12, 31))
        code, lines, _ = self._run(
            years="2026",
            bounds=(date(2006, 9, 4), date(2027, 9, 3)),
            existing=set(),
            built={full: [_session(date(2026, 1, 2))]},
        )

        self.assertEqual(code, 0)
        self.assertIn("clampedYears=none", lines)


class OfflineSeedExistingSessionTest(_SeedRunner):
    """날짜로 자르지 않고 존재 여부로 가르는지 잠근다."""

    def test_past_gaps_are_filled_and_existing_rows_are_left_alone(self) -> None:
        past = date(2026, 4, 3)
        already = date(2026, 6, 23)
        future = date(2026, 12, 30)
        full = (date(2026, 1, 1), date(2026, 12, 31))

        code, lines, written = self._run(
            years="2026",
            bounds=(date(2006, 9, 4), date(2027, 9, 3)),
            existing={already},
            built={full: [_session(past), _session(already), _session(future)]},
        )

        self.assertEqual(code, 0)
        # 과거 공백과 미래 세션 둘 다 적재되고, 이미 있는 하루는 빠진다.
        self.assertIn("seeded=2", lines)
        self.assertIn(f"firstSession={past.isoformat()}", lines)
        self.assertIn(f"lastSession={future.isoformat()}", lines)
        self.assertIn("alreadyPresent=1", lines)
        self.assertEqual(written, [past, future])
        self.assertNotIn(already, written)


if __name__ == "__main__":
    unittest.main()
