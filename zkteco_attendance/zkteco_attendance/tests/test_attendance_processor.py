"""
Unit tests for the ZKTeco attendance processor overtime keys.

Regression guard: the OT calculation must expose the total under the
`overtime_hours` key (matching the Attendance Summary Detail DB field and
all consumers). A previous refactor renamed it to `ot_hours`, which made
Total Overtime Hours always 0 in Attendance Summary and on the Daily
Checkins page while the per-category chips still showed values.

Run with: bench run-tests --app zkteco_attendance
"""

import unittest
from datetime import date, datetime
from unittest.mock import patch, MagicMock

import frappe
from frappe.utils import get_datetime


def _mk_checkin(name, time_str, log_type):
    return {"name": name, "time": get_datetime(time_str), "log_type": log_type,
            "is_overtime": False, "manually_edited": False, "edited_by": "", "edited_at": ""}


def _day_shift(**overrides):
    shift = {
        "name": "TEST-SHIFT",
        "start_time": "08:00:00",
        "end_time": "17:00:00",
        "is_night_shift": 0,
        "full_day_hours": 8,
        "half_day_hours": 4,
        "standard_working_hours": 8,
        "working_hours_method": "First IN - Last OUT",
        "missing_checkin_action": "Mark as Invalid",
        "late_entry_grace": 0,
        "early_exit_grace": 0,
        "saturday_mode": "Full Day",
        "saturday_half_day_hours": 4,
        "enable_overtime": 1,
        "overtime_calculation_method": "Fixed Windows",
        "overtime_threshold_minutes": 0,
        "max_overtime_hours_per_day": 0,
        "standard_start_time": "08:00:00",
        "night_ot_start_time": "22:00:00",
        "night_ot_end_time": "06:00:00",
    }
    shift.update(overrides)
    return shift


class TestOvertimeKeys(unittest.TestCase):

    def test_calc_overtime_hours_returns_overtime_hours_key(self):
        """The total OT must be under `overtime_hours`, not `ot_hours`."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import calc_overtime_hours

        checkins = [
            _mk_checkin("C1", "2026-08-10 06:00:00", "IN"),
            _mk_checkin("C2", "2026-08-10 17:00:00", "OUT"),
        ]
        result = calc_overtime_hours(checkins, _day_shift(), total_hours=11.0)

        self.assertIn("overtime_hours", result)
        self.assertNotIn("ot_hours", result)
        # 11h worked, 8h standard -> 3h day OT, 0h night OT
        self.assertAlmostEqual(result["overtime_hours"], 3.0, places=2)
        self.assertAlmostEqual(result["day_ot_hours"], 3.0, places=2)
        self.assertAlmostEqual(result["night_ot_hours"], 0.0, places=2)

    def test_calc_overtime_hours_zero_dict_has_overtime_hours_key(self):
        """When overtime is disabled, the zero dict still carries the key."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import calc_overtime_hours

        shift = _day_shift(enable_overtime=0)
        result = calc_overtime_hours([], shift, total_hours=0.0)
        self.assertIn("overtime_hours", result)
        self.assertEqual(result["overtime_hours"], 0.0)

    def test_classify_day_returns_overtime_hours_key(self):
        """classify_day must surface the OT total as `overtime_hours`."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import classify_day

        checkins = [
            _mk_checkin("C1", "2026-08-10 06:00:00", "IN"),
            _mk_checkin("C2", "2026-08-10 17:00:00", "OUT"),
        ]
        result = classify_day(checkins, _day_shift(), "Mark as Invalid")

        self.assertEqual(result["status"], "Present")
        self.assertAlmostEqual(result["hours"], 11.0, places=2)
        self.assertAlmostEqual(result["overtime_hours"], 3.0, places=2)
        self.assertAlmostEqual(result["day_ot_hours"], 3.0, places=2)

    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_holidays_in_range")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_shift_for_employee")
    def test_process_employee_aggregates_overtime_hours(self, mock_shift, mock_holidays):
        """
        The headline regression: process_employee must roll the per-day
        `overtime_hours` up so Attendance Summary's Total Overtime Hours
        gets populated. Before the fix this stayed 0.0 forever.
        """
        from zkteco_attendance.zkteco_attendance.attendance_processor import process_employee

        mock_shift.return_value = _day_shift()
        mock_holidays.return_value = {}

        checkins = [
            _mk_checkin("C1", "2026-08-10 06:00:00", "IN"),
            _mk_checkin("C2", "2026-08-10 17:00:00", "OUT"),
        ]
        result = process_employee(
            employee="HR-EMP-00001",
            from_date="2026-08-10",
            to_date="2026-08-10",
            checkin_list=checkins,
            default_shift_name=None,
            doc_missing_action="Mark as Invalid",
        )

        self.assertEqual(result["working_days"], 1.0)
        self.assertAlmostEqual(result["total_working_hours"], 11.0, places=2)
        self.assertAlmostEqual(result["overtime_hours"], 3.0, places=2)
        self.assertAlmostEqual(result["day_ot_hours"], 3.0, places=2)
        self.assertAlmostEqual(result["night_ot_hours"], 0.0, places=2)
        self.assertEqual(result["overtime_days"], 1)

    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_holidays_in_range")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_shift_for_employee")
    def test_process_employee_night_overtime(self, mock_shift, mock_holidays):
        """Night-shift window hours are reported separately and in the total."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import process_employee

        mock_shift.return_value = _day_shift(is_night_shift=1, start_time="17:00:00", end_time="06:00:00")
        mock_holidays.return_value = {}

        # 17:00 -> next-day 06:00 : 8h of night-window (22:00-06:00) overtime
        checkins = [
            _mk_checkin("C1", "2026-08-10 17:00:00", "IN"),
            _mk_checkin("C2", "2026-08-11 06:00:00", "OUT"),
        ]
        result = process_employee(
            employee="HR-EMP-00001",
            from_date="2026-08-10",
            to_date="2026-08-10",
            checkin_list=checkins,
            default_shift_name=None,
            doc_missing_action="Mark as Invalid",
        )

        self.assertAlmostEqual(result["total_working_hours"], 13.0, places=2)
        self.assertAlmostEqual(result["night_ot_hours"], 8.0, places=2)
        self.assertAlmostEqual(result["overtime_hours"], 8.0, places=2)


class TestGraceAndShiftOvertime(unittest.TestCase):
    """
    Grace periods (Late Entry / Early Exit) are deducted from working hours
    and the shift's Start/End Time drive overtime when the shift uses the
    'After Shift End Time' calculation method.
    """

    def test_classify_day_late_entry_grace_deducts_minutes(self):
        """First IN beyond Start Time + Late Entry Grace reduces hours + flags."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import classify_day

        shift = _day_shift(late_entry_grace=10)
        checkins = [
            _mk_checkin("C1", "2026-08-10 08:30:00", "IN"),
            _mk_checkin("C2", "2026-08-10 17:00:00", "OUT"),
        ]
        result = classify_day(checkins, shift, "Mark as Invalid")

        # raw 8.5h, 20 min beyond the 10 min grace -> 8.5 - 20/60
        self.assertTrue(result["is_late"])
        self.assertFalse(result["is_early_exit"])
        self.assertAlmostEqual(result["late_minutes"], 20.0, places=1)
        self.assertAlmostEqual(result["hours"], 8.5 - 20.0 / 60.0, places=2)
        self.assertEqual(result["status"], "Present")

    def test_classify_day_within_grace_is_not_late(self):
        """Arriving inside the grace window is not late and loses no hours."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import classify_day

        shift = _day_shift(late_entry_grace=15)
        checkins = [
            _mk_checkin("C1", "2026-08-10 08:10:00", "IN"),
            _mk_checkin("C2", "2026-08-10 17:00:00", "OUT"),
        ]
        result = classify_day(checkins, shift, "Mark as Invalid")

        self.assertFalse(result["is_late"])
        self.assertAlmostEqual(result["hours"], 8.8333, places=2)
        self.assertEqual(result["status"], "Present")

    def test_classify_day_late_entry_can_drop_to_half_day(self):
        """A big late arrival reduces effective hours to the half-day band."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import classify_day

        shift = _day_shift(late_entry_grace=0)  # 08:00 start, half day at 4h
        checkins = [
            _mk_checkin("C1", "2026-08-10 12:00:00", "IN"),
            _mk_checkin("C2", "2026-08-10 17:00:00", "OUT"),
        ]
        result = classify_day(checkins, shift, "Mark as Invalid")

        # raw 5h, 4h late -> 1h effective -> Half Day (0 < 1h <= 4h)
        self.assertTrue(result["is_late"])
        self.assertEqual(result["status"], "Half Day")
        self.assertAlmostEqual(result["hours"], 1.0, places=2)

    def test_classify_day_early_exit_grace_deducts_minutes(self):
        """Last OUT before End Time - Early Exit Grace reduces hours + flags."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import classify_day

        shift = _day_shift(early_exit_grace=15)
        checkins = [
            _mk_checkin("C1", "2026-08-10 08:00:00", "IN"),
            _mk_checkin("C2", "2026-08-10 16:30:00", "OUT"),
        ]
        result = classify_day(checkins, shift, "Mark as Invalid")

        # raw 8.5h, 30 min early beyond the 15 min grace -> 8.5 - 15/60
        self.assertTrue(result["is_early_exit"])
        self.assertFalse(result["is_late"])
        self.assertAlmostEqual(result["early_minutes"], 15.0, places=1)
        self.assertAlmostEqual(result["hours"], 8.5 - 15.0 / 60.0, places=2)
        self.assertEqual(result["status"], "Present")

    def test_calc_overtime_after_shift_end_day(self):
        """After Shift End Time: hours past the shift's End Time are Day OT."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import calc_overtime_hours

        shift = _day_shift(overtime_calculation_method="After Shift End Time")
        checkins = [
            _mk_checkin("C1", "2026-08-10 08:00:00", "IN"),
            _mk_checkin("C2", "2026-08-10 19:00:00", "OUT"),
        ]
        result = calc_overtime_hours(checkins, shift, total_hours=11.0)

        # 17:00-19:00 = 2h day OT, no night OT
        self.assertAlmostEqual(result["day_ot_hours"], 2.0, places=2)
        self.assertAlmostEqual(result["night_ot_hours"], 0.0, places=2)
        self.assertAlmostEqual(result["overtime_hours"], 2.0, places=2)

    def test_calc_overtime_after_shift_end_no_double_count(self):
        """Late-night hours must not be counted in both Day and Night OT."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import calc_overtime_hours

        shift = _day_shift(overtime_calculation_method="After Shift End Time")
        checkins = [
            _mk_checkin("C1", "2026-08-10 08:00:00", "IN"),
            _mk_checkin("C2", "2026-08-10 23:00:00", "OUT"),
        ]
        result = calc_overtime_hours(checkins, shift, total_hours=15.0)

        # 17:00-22:00 day OT = 5h; 22:00-23:00 night OT (default window) = 1h
        self.assertAlmostEqual(result["day_ot_hours"], 5.0, places=2)
        self.assertAlmostEqual(result["night_ot_hours"], 1.0, places=2)
        self.assertAlmostEqual(result["overtime_hours"], 6.0, places=2)

    def test_calc_overtime_after_shift_end_night_shift(self):
        """Night OT uses the shift's Night OT Start/End window after standard hours."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import calc_overtime_hours

        shift = _day_shift(
            is_night_shift=1,
            start_time="17:00:00",
            end_time="06:00:00",
            overtime_calculation_method="After Shift End Time",
            night_ot_start_time="01:00:00",
            night_ot_end_time="06:00:00",
        )
        checkins = [
            _mk_checkin("C1", "2026-08-10 17:00:00", "IN"),
            _mk_checkin("C2", "2026-08-11 06:00:00", "OUT"),
        ]
        result = calc_overtime_hours(checkins, shift, total_hours=13.0)

        # standard core 17:00-01:00; Night OT window 01:00-06:00 = 5h
        self.assertAlmostEqual(result["night_ot_hours"], 5.0, places=2)
        self.assertAlmostEqual(result["day_ot_hours"], 0.0, places=2)
        self.assertAlmostEqual(result["overtime_hours"], 5.0, places=2)

    def test_overtime_threshold_minutes(self):
        """Total OT at or below the shift's threshold is discarded."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import calc_overtime_hours

        checkins = [
            _mk_checkin("C1", "2026-08-10 08:00:00", "IN"),
            _mk_checkin("C2", "2026-08-10 19:00:00", "OUT"),
        ]
        shift = _day_shift(overtime_calculation_method="After Shift End Time",
                           overtime_threshold_minutes=150)  # 2.5h threshold > 2h OT
        result = calc_overtime_hours(checkins, shift, total_hours=11.0)
        self.assertEqual(result["overtime_hours"], 0.0)
        self.assertEqual(result["day_ot_hours"], 0.0)

        shift = _day_shift(overtime_calculation_method="After Shift End Time",
                           overtime_threshold_minutes=60)  # 1h threshold < 2h OT
        result = calc_overtime_hours(checkins, shift, total_hours=11.0)
        self.assertAlmostEqual(result["overtime_hours"], 2.0, places=2)

    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_holidays_in_range")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_shift_for_employee")
    def test_process_employee_late_entry_with_after_shift_end_ot(self, mock_shift, mock_holidays):
        """End-to-end: grace deduction + shift-end OT roll up into the summary."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import process_employee

        mock_shift.return_value = _day_shift(
            late_entry_grace=10,
            overtime_calculation_method="After Shift End Time",
        )
        mock_holidays.return_value = {}

        checkins = [
            _mk_checkin("C1", "2026-08-10 09:00:00", "IN"),
            _mk_checkin("C2", "2026-08-10 19:00:00", "OUT"),
        ]
        result = process_employee(
            employee="HR-EMP-00001",
            from_date="2026-08-10",
            to_date="2026-08-10",
            checkin_list=checkins,
            default_shift_name=None,
            doc_missing_action="Mark as Invalid",
        )

        # raw 10h, 50 min late (09:00 vs 08:10) -> 9.1667h; 2h day OT after 17:00
        self.assertEqual(result["working_days"], 1.0)
        self.assertAlmostEqual(result["total_working_hours"], 10.0 - 50.0 / 60.0, places=2)
        self.assertAlmostEqual(result["overtime_hours"], 2.0, places=2)
        self.assertAlmostEqual(result["day_ot_hours"], 2.0, places=2)
        self.assertIn("late entry", result["remarks"])


class TestNightShiftCrossMidnight(unittest.TestCase):
    """
    Night-shift checkins that cross midnight must be grouped as a single
    attendance period.  The OUT at shift-start is treated as the IN, and
    the IN at shift-end is treated as the OUT.
    """

    def test_night_shift_cross_midnight_grouping(self):
        """Checkins across midnight land in the same attendance day."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import (
            group_checkins_by_date,
        )

        shift = _day_shift(is_night_shift=1, start_time="17:00:00",
                           end_time="06:00:00")

        checkins = [
            _mk_checkin("C1", "2026-01-01 06:01:00", "IN"),
            _mk_checkin("C2", "2026-01-01 17:03:00", "OUT"),
            _mk_checkin("C3", "2026-01-02 06:02:00", "IN"),
            _mk_checkin("C4", "2026-01-02 17:04:00", "OUT"),
        ]

        daily = group_checkins_by_date(
            checkins, shift,
            from_date=date(2026, 1, 1), to_date=date(2026, 1, 2),
        )

        # 01/01 should contain the night-shift pair: 17:03 and next-day 06:02
        self.assertIn(date(2026, 1, 1), daily)
        day1 = sorted(daily[date(2026, 1, 1)], key=lambda c: c["time"])
        self.assertEqual(len(day1), 2)
        self.assertEqual(day1[0]["time"], datetime(2026, 1, 1, 17, 3))
        self.assertEqual(day1[1]["time"], datetime(2026, 1, 2, 6, 2))

        # Labels re-resolved: first punch = IN, last = OUT
        self.assertEqual(day1[0]["log_type"], "IN")
        self.assertEqual(day1[1]["log_type"], "OUT")

        # 02/01 should contain the next night-shift start
        self.assertIn(date(2026, 1, 2), daily)
        day2 = daily[date(2026, 1, 2)]
        self.assertEqual(len(day2), 1)
        self.assertEqual(day2[0]["time"], datetime(2026, 1, 2, 17, 4))

    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_holidays_in_range")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_shift_for_employee")
    def test_night_shift_hours_and_overtime(self, mock_shift, mock_holidays):
        """Cross-midnight hours and OT are calculated correctly."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import process_employee

        mock_shift.return_value = _day_shift(
            is_night_shift=1, start_time="17:00:00", end_time="06:00:00",
            overtime_calculation_method="After Shift End Time",
            night_ot_start_time="01:00:00",
            night_ot_end_time="06:00:00",
        )
        mock_holidays.return_value = {}

        checkins = [
            _mk_checkin("C1", "2026-01-01 06:01:00", "IN"),
            _mk_checkin("C2", "2026-01-01 17:03:00", "OUT"),
            _mk_checkin("C3", "2026-01-02 06:02:00", "IN"),
            _mk_checkin("C4", "2026-01-02 17:04:00", "OUT"),
        ]
        result = process_employee(
            employee="EMP-NIGHT",
            from_date="2026-01-01",
            to_date="2026-01-01",
            checkin_list=checkins,
            default_shift_name="Night Guard",
            doc_missing_action="Mark as Invalid",
        )

        # 17:03 -> 06:02 = 12h 59min ≈ 12.98h total
        self.assertAlmostEqual(result["total_working_hours"],
                               12 + 59 / 60.0, places=1)
        self.assertEqual(result["working_days"], 1.0)

        # After Shift End Time: 17:03->01:00 regular, 01:00->06:02 night OT
        self.assertAlmostEqual(result["night_ot_hours"], 5.0, places=1)

    def test_night_ot_respects_custom_window_after_standard_hours(self):
        """After Standard Hours method must honour the shift's configurable
        Night OT Start / End time instead of the fixed 22:00-06:00 default.

        Scenario: Guard shift 17:00-06:00, std 8 h.
          night_ot_start_time = 01:00, night_ot_end_time = 06:00.
          Employee works 17:00 -> 06:00 (13 h).
          Expected night OT = 5 h (01:00-06:00), not 8 h (22:00-06:00).
        """
        from zkteco_attendance.zkteco_attendance.attendance_processor import calc_overtime_hours

        shift = _day_shift(
            is_night_shift=1,
            start_time="17:00:00",
            end_time="06:00:00",
            overtime_calculation_method="After Standard Hours",
            night_ot_start_time="01:00:00",
            night_ot_end_time="06:00:00",
        )
        checkins = [
            _mk_checkin("C1", "2026-01-01 17:00:00", "IN"),
            _mk_checkin("C2", "2026-01-02 06:00:00", "OUT"),
        ]
        result = calc_overtime_hours(checkins, shift, total_hours=13.0,
                                     method="First IN - Last OUT",
                                     work_date=date(2026, 1, 1))

        # night OT window 01:00-06:00 = 5 h
        self.assertAlmostEqual(result["night_ot_hours"], 5.0, places=2)
        # day OT window 06:00-22:00: worked 17:00-22:00 = 5 h; 5 - 8 std < 0 → 0
        self.assertAlmostEqual(result["day_ot_hours"], 0.0, places=2)
        self.assertAlmostEqual(result["overtime_hours"], 5.0, places=2)

    def test_night_shift_early_arrival_attributed_correctly(self):
        """An employee arriving 5 minutes early is still in the same shift."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import (
            group_checkins_by_date,
        )

        shift = _day_shift(is_night_shift=1, start_time="17:00:00",
                           end_time="06:00:00")
        checkins = [
            _mk_checkin("C1", "2026-01-01 16:55:00", "IN"),
            _mk_checkin("C2", "2026-01-02 06:02:00", "OUT"),
        ]
        daily = group_checkins_by_date(
            checkins, shift,
            from_date=date(2026, 1, 1), to_date=date(2026, 1, 2),
        )

        self.assertIn(date(2026, 1, 1), daily)
        day1 = sorted(daily[date(2026, 1, 1)], key=lambda c: c["time"])
        self.assertEqual(len(day1), 2)
        self.assertEqual(day1[0]["time"], datetime(2026, 1, 1, 16, 55))
        self.assertEqual(day1[1]["time"], datetime(2026, 1, 2, 6, 2))

    def test_night_shift_late_exit_included(self):
        """An employee leaving 30 minutes past shift end is still in the same shift."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import (
            group_checkins_by_date,
        )

        shift = _day_shift(is_night_shift=1, start_time="17:00:00",
                           end_time="06:00:00")
        checkins = [
            _mk_checkin("C1", "2026-01-01 17:00:00", "IN"),
            _mk_checkin("C2", "2026-01-02 06:30:00", "OUT"),
        ]
        daily = group_checkins_by_date(
            checkins, shift,
            from_date=date(2026, 1, 1), to_date=date(2026, 1, 2),
        )

        self.assertIn(date(2026, 1, 1), daily)
        day1 = sorted(daily[date(2026, 1, 1)], key=lambda c: c["time"])
        self.assertEqual(len(day1), 2)
        self.assertEqual(day1[1]["time"], datetime(2026, 1, 2, 6, 30))

    def test_night_shift_grace_period(self):
        """Late entry and early exit grace are computed across midnight."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import classify_day

        shift = _day_shift(
            is_night_shift=1, start_time="17:00:00", end_time="06:00:00",
            late_entry_grace=10, early_exit_grace=10,
        )
        checkins = [
            _mk_checkin("C1", "2026-01-01 17:20:00", "IN"),   # 10 min late beyond grace
            _mk_checkin("C2", "2026-01-02 05:45:00", "OUT"),  # 15 min early beyond grace
        ]
        result = classify_day(checkins, shift, "Mark as Invalid",
                               work_date=date(2026, 1, 1))

        self.assertTrue(result["is_late"])
        self.assertTrue(result["is_early_exit"])
        self.assertAlmostEqual(result["late_minutes"], 10.0, places=1)
        self.assertAlmostEqual(result["early_minutes"], 5.0, places=1)

    def test_night_shift_does_not_pair_with_next_day(self):
        """The OUT punch at 17:04 on Day 2 is NOT consumed by Day 1's shift."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import (
            group_checkins_by_date,
        )

        shift = _day_shift(is_night_shift=1, start_time="17:00:00",
                           end_time="06:00:00")
        checkins = [
            _mk_checkin("C1", "2026-01-01 17:00:00", "IN"),
            _mk_checkin("C2", "2026-01-02 06:00:00", "OUT"),
            _mk_checkin("C3", "2026-01-02 17:00:00", "IN"),
            _mk_checkin("C4", "2026-01-03 06:00:00", "OUT"),
        ]
        daily = group_checkins_by_date(
            checkins, shift,
            from_date=date(2026, 1, 1), to_date=date(2026, 1, 3),
        )

        # Day 1: 17:00 on 01/01 + 06:00 on 01/02
        day1 = sorted(daily.get(date(2026, 1, 1), []), key=lambda c: c["time"])
        self.assertEqual(len(day1), 2)
        self.assertEqual(day1[0]["time"], datetime(2026, 1, 1, 17, 0))
        self.assertEqual(day1[1]["time"], datetime(2026, 1, 2, 6, 0))

        # Day 2: 17:00 on 01/02 + 06:00 on 01/03
        day2 = sorted(daily.get(date(2026, 1, 2), []), key=lambda c: c["time"])
        self.assertEqual(len(day2), 2)
        self.assertEqual(day2[0]["time"], datetime(2026, 1, 2, 17, 0))
        self.assertEqual(day2[1]["time"], datetime(2026, 1, 3, 6, 0))


class TestNightShiftAcceptance(unittest.TestCase):
    """
    Exact acceptance test from the user's specification.
    """

    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_holidays_in_range")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_shift_for_employee")
    def test_user_acceptance_test(self, mock_shift, mock_holidays):
        """
        Check-ins:
          01/01 06:01 IN, 01/01 17:03 OUT
          02/01 06:02 IN, 02/01 17:04 OUT
        Shift: 17:00 -> 06:00
        Expected for 01/01:
          IN  = 01/01 17:03
          OUT = 02/01 06:02
          Regular  = 17:03 -> 01:00 ≈ 8h
          Night OT = 01:00 -> 06:02 ≈ 5h
        """
        from zkteco_attendance.zkteco_attendance.attendance_processor import process_employee

        mock_shift.return_value = _day_shift(
            is_night_shift=1,
            start_time="17:00:00",
            end_time="06:00:00",
            standard_working_hours=8,
            overtime_calculation_method="After Shift End Time",
            night_ot_start_time="01:00:00",
            night_ot_end_time="06:00:00",
        )
        mock_holidays.return_value = {}

        checkins = [
            _mk_checkin("C1", "2026-01-01 06:01:00", "IN"),
            _mk_checkin("C2", "2026-01-01 17:03:00", "OUT"),
            _mk_checkin("C3", "2026-01-02 06:02:00", "IN"),
            _mk_checkin("C4", "2026-01-02 17:04:00", "OUT"),
        ]

        result = process_employee(
            employee="EMP-NIGHT",
            from_date="2026-01-01",
            to_date="2026-01-01",
            checkin_list=checkins,
            default_shift_name="Night Guard",
            doc_missing_action="Mark as Invalid",
        )

        # 17:03 -> 06:02 = 12h 59min ≈ 12.98h
        self.assertAlmostEqual(result["total_working_hours"],
                               12 + 59 / 60.0, places=1)
        self.assertEqual(result["working_days"], 1.0)

        # Night OT: 01:00 -> 06:02 = 5h 2min ≈ 5.03h
        self.assertAlmostEqual(result["night_ot_hours"], 5.0, places=0)


class TestLunchBreakDeduction(unittest.TestCase):
    """Lunch break hours should be deducted from the First IN - Last OUT
    total span so that e.g. 08:00-17:00 with 1h lunch = 8h, not 9h.
    """

    def test_lunch_break_deducts_from_first_last(self):
        """1h lunch on 08:00-17:00 → 8h working hours."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import calc_working_hours

        checkins = [
            _mk_checkin("C1", "2026-08-10 08:00:00", "IN"),
            _mk_checkin("C2", "2026-08-10 17:00:00", "OUT"),
        ]
        hours = calc_working_hours(checkins, "First IN - Last OUT",
                                    lunch_break_hours=1.0)
        self.assertAlmostEqual(hours, 8.0, places=2)

    def test_lunch_break_zero_has_no_effect(self):
        """lunch_break_hours=0 should behave like before (9h span)."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import calc_working_hours

        checkins = [
            _mk_checkin("C1", "2026-08-10 08:00:00", "IN"),
            _mk_checkin("C2", "2026-08-10 17:00:00", "OUT"),
        ]
        hours = calc_working_hours(checkins, "First IN - Last OUT",
                                    lunch_break_hours=0.0)
        self.assertAlmostEqual(hours, 9.0, places=2)

    def test_lunch_break_not_applied_to_actual_pairs(self):
        """Lunch break should only affect First IN - Last OUT, not Actual Pairs."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import calc_working_hours

        checkins = [
            _mk_checkin("C1", "2026-08-10 08:00:00", "IN"),
            _mk_checkin("C2", "2026-08-10 12:00:00", "OUT"),
            _mk_checkin("C3", "2026-08-10 13:00:00", "IN"),
            _mk_checkin("C4", "2026-08-10 17:00:00", "OUT"),
        ]
        hours = calc_working_hours(checkins, "Actual Pairs (IN-OUT)",
                                    lunch_break_hours=1.0)
        # 4h + 4h = 8h (lunch break not deducted for Actual Pairs)
        self.assertAlmostEqual(hours, 8.0, places=2)

    def test_lunch_break_reduces_overtime(self):
        """8:00-17:00 with 1h lunch = 8h → no overtime instead of 1h."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import classify_day

        shift = _day_shift(lunch_break_hours=1)
        checkins = [
            _mk_checkin("C1", "2026-08-10 08:00:00", "IN"),
            _mk_checkin("C2", "2026-08-10 17:00:00", "OUT"),
        ]
        result = classify_day(checkins, shift, "Mark as Invalid")

        self.assertEqual(result["status"], "Present")
        self.assertAlmostEqual(result["hours"], 8.0, places=2)
        self.assertAlmostEqual(result["overtime_hours"], 0.0, places=2)

    def test_lunch_break_zero_no_deduction(self):
        """Without lunch break: 8:00-17:00 = 9h → 1h OT."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import classify_day

        shift = _day_shift(lunch_break_hours=0)
        checkins = [
            _mk_checkin("C1", "2026-08-10 08:00:00", "IN"),
            _mk_checkin("C2", "2026-08-10 17:00:00", "OUT"),
        ]
        result = classify_day(checkins, shift, "Mark as Invalid")

        self.assertEqual(result["status"], "Present")
        self.assertAlmostEqual(result["hours"], 9.0, places=2)
        self.assertAlmostEqual(result["overtime_hours"], 1.0, places=2)


class TestSaturdayHalfDayMode(unittest.TestCase):
    """Saturday Mode = Half Day: any worked hours → Present,
    no checkins (or zero hours) → Absent.
    """

    def test_saturday_half_day_4h_worked_is_present(self):
        """4h worked meets saturday_half_day_hours=4 → Present, 0 absent."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import classify_day

        shift = _day_shift(
            saturday_mode="Half Day",
            saturday_half_day_hours=4,
        )
        checkins = [
            _mk_checkin("C1", "2026-08-15 08:00:00", "IN"),
            _mk_checkin("C2", "2026-08-15 12:00:00", "OUT"),
        ]
        result = classify_day(checkins, shift, "Mark as Invalid",
                               is_saturday=True,
                               work_date=date(2026, 8, 15))

        self.assertEqual(result["status"], "Present")
        self.assertAlmostEqual(result["hours"], 4.0, places=2)
        self.assertAlmostEqual(result["absent_hours"], 0.0, places=2)
        self.assertAlmostEqual(result["overtime_hours"], 0.0, places=2)

    def test_saturday_half_day_6h_worked_has_2h_ot(self):
        """6h worked, standard = 4h → Present, 2h OT (6 - 4)."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import classify_day

        shift = _day_shift(
            saturday_mode="Half Day",
            saturday_half_day_hours=4,
        )
        checkins = [
            _mk_checkin("C1", "2026-08-15 08:00:00", "IN"),
            _mk_checkin("C2", "2026-08-15 14:00:00", "OUT"),
        ]
        result = classify_day(checkins, shift, "Mark as Invalid",
                               is_saturday=True,
                               work_date=date(2026, 8, 15))

        self.assertEqual(result["status"], "Present")
        self.assertAlmostEqual(result["hours"], 6.0, places=2)
        self.assertAlmostEqual(result["absent_hours"], 0.0, places=2)
        self.assertAlmostEqual(result["overtime_hours"], 2.0, places=2)  # 6 - 4 = 2
        self.assertAlmostEqual(result["day_ot_hours"], 2.0, places=2)

    def test_saturday_half_day_10h_worked_has_6h_ot(self):
        """10h worked, standard = 4h → Present, 6h OT (10 - 4)."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import classify_day

        shift = _day_shift(
            saturday_mode="Half Day",
            saturday_half_day_hours=4,
        )
        checkins = [
            _mk_checkin("C1", "2026-08-15 08:00:00", "IN"),
            _mk_checkin("C2", "2026-08-15 18:00:00", "OUT"),
        ]
        result = classify_day(checkins, shift, "Mark as Invalid",
                               is_saturday=True,
                               work_date=date(2026, 8, 15))

        self.assertEqual(result["status"], "Present")
        self.assertAlmostEqual(result["hours"], 10.0, places=2)
        self.assertAlmostEqual(result["overtime_hours"], 6.0, places=2)  # 10 - 4 = 6
        self.assertAlmostEqual(result["day_ot_hours"], 6.0, places=2)
        self.assertAlmostEqual(result["absent_hours"], 0.0, places=2)

    def test_saturday_half_day_short_checkin_is_present(self):
        """1h worked on Saturday Half Day → Present (any hours count), 0 absent."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import classify_day

        shift = _day_shift(
            saturday_mode="Half Day",
            saturday_half_day_hours=4,
            saturday_end_time="09:00:00",
        )
        checkins = [
            _mk_checkin("C1", "2026-08-15 08:00:00", "IN"),
            _mk_checkin("C2", "2026-08-15 09:00:00", "OUT"),
        ]
        result = classify_day(checkins, shift, "Mark as Invalid",
                               is_saturday=True,
                               work_date=date(2026, 8, 15))

        self.assertEqual(result["status"], "Present")
        self.assertAlmostEqual(result["hours"], 1.0, places=2)
        self.assertAlmostEqual(result["absent_hours"], 0.0, places=2)

    def test_saturday_half_day_no_checkins_is_absent(self):
        """No checkins on Saturday Half Day → Absent with full absent hours."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import classify_day

        shift = _day_shift(
            saturday_mode="Half Day",
            saturday_half_day_hours=4,
        )
        result = classify_day([], shift, "Mark as Invalid",
                               is_saturday=True,
                               work_date=date(2026, 8, 15))

        self.assertEqual(result["status"], "Absent")
        self.assertAlmostEqual(result["absent_hours"], 8.0, places=2)

    def test_saturday_half_day_late_entry_deducts_hours(self):
        """Late entry beyond grace on Saturday Half Day reduces effective hours."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import classify_day

        shift = _day_shift(
            saturday_mode="Half Day",
            saturday_half_day_hours=4,
            late_entry_grace=10,
        )
        # 08:00-13:00 = 5h, but 15min late → 5 - 0.25 = 4.75h
        checkins = [
            _mk_checkin("C1", "2026-08-15 08:15:00", "IN"),
            _mk_checkin("C2", "2026-08-15 13:00:00", "OUT"),
        ]
        result = classify_day(checkins, shift, "Mark as Invalid",
                               is_saturday=True,
                               work_date=date(2026, 8, 15))

        self.assertEqual(result["status"], "Present")
        # 08:15 -> 13:00 = 4h45m span; 15 min late minus 10 min grace = 5 min deducted
        self.assertAlmostEqual(result["hours"], 4.75 - 5.0 / 60.0, places=2)  # 4.6667
        self.assertAlmostEqual(result["absent_hours"], 0.0, places=2)

    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_holidays_in_range")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_shift_for_employee")
    def test_saturday_half_day_present_in_process_employee(self, mock_shift, mock_holidays):
        """process_employee counts Saturday as Present (1.0) when
        effective hours meet saturday_half_day_hours.
        """
        from zkteco_attendance.zkteco_attendance.attendance_processor import process_employee

        mock_shift.return_value = _day_shift(
            saturday_mode="Half Day",
            saturday_half_day_hours=4,
        )
        mock_holidays.return_value = {}

        # 2026-08-15 is Saturday — employee works 5h (08:00-13:00)
        checkins = [
            _mk_checkin("C1", "2026-08-15 08:00:00", "IN"),
            _mk_checkin("C2", "2026-08-15 13:00:00", "OUT"),
        ]
        result = process_employee(
            employee="HR-EMP-00001",
            from_date="2026-08-15",
            to_date="2026-08-15",
            checkin_list=checkins,
            default_shift_name=None,
            doc_missing_action="Mark as Invalid",
        )

        # 5h worked ≥ 4h saturday_half_day_hours → Present
        self.assertEqual(result["working_days"], 1.0)
        self.assertAlmostEqual(result["total_working_hours"], 5.0, places=2)


class TestStatusClassification(unittest.TestCase):
    """Working-day status rules:
      Present  — worked hours > half_day_hours
      Half Day — 0 < worked hours <= half_day_hours
      Absent   — worked hours = 0 or no checkins
      Invalid  — unpaired checkins (missing IN or OUT)
    """

    def test_hours_above_half_day_is_present(self):
        """5h worked with half_day_hours=4 → Present (no full-day minimum)."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import classify_day

        shift = _day_shift(end_time="13:00:00")
        checkins = [
            _mk_checkin("C1", "2026-08-10 08:00:00", "IN"),
            _mk_checkin("C2", "2026-08-10 13:00:00", "OUT"),
        ]
        result = classify_day(checkins, shift, "Mark as Invalid")

        self.assertEqual(result["status"], "Present")
        self.assertAlmostEqual(result["absent_hours"], 0.0, places=2)

    def test_hours_equal_half_day_is_half_day(self):
        """Exactly half_day_hours=4 worked → Half Day, not Present."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import classify_day

        shift = _day_shift(end_time="12:00:00")
        checkins = [
            _mk_checkin("C1", "2026-08-10 08:00:00", "IN"),
            _mk_checkin("C2", "2026-08-10 12:00:00", "OUT"),
        ]
        result = classify_day(checkins, shift, "Mark as Invalid")

        self.assertEqual(result["status"], "Half Day")
        self.assertAlmostEqual(result["hours"], 4.0, places=2)
        self.assertAlmostEqual(result["absent_hours"], 4.0, places=2)  # std/2

    def test_hours_below_half_day_is_half_day(self):
        """2h worked with half_day_hours=4 → Half Day (not Absent)."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import classify_day

        shift = _day_shift(end_time="10:00:00")
        checkins = [
            _mk_checkin("C1", "2026-08-10 08:00:00", "IN"),
            _mk_checkin("C2", "2026-08-10 10:00:00", "OUT"),
        ]
        result = classify_day(checkins, shift, "Mark as Invalid")

        self.assertEqual(result["status"], "Half Day")
        self.assertAlmostEqual(result["hours"], 2.0, places=2)

    def test_no_checkins_is_absent(self):
        """No checkins on a working day → Absent with full absent hours."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import classify_day

        shift = _day_shift()
        result = classify_day([], shift, "Mark as Invalid")

        self.assertEqual(result["status"], "Absent")
        self.assertAlmostEqual(result["hours"], 0.0, places=2)
        self.assertAlmostEqual(result["absent_hours"], 8.0, places=2)  # std hours

    def test_unpaired_checkin_is_always_invalid(self):
        """Only an IN punch → Invalid, even with 'Mark as Present' configured."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import classify_day

        shift = _day_shift()
        checkins = [
            _mk_checkin("C1", "2026-08-10 08:00:00", "IN"),
        ]
        for action in ("Mark as Invalid", "Mark as Present", "Require Manual Review"):
            result = classify_day(checkins, shift, action)
            self.assertEqual(result["status"], "Invalid", msg=action)
            self.assertAlmostEqual(result["hours"], 0.0, places=2)

    def test_zero_hours_checkins_is_absent(self):
        """Paired checkins spanning zero time → Absent."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import classify_day

        shift = _day_shift()
        checkins = [
            _mk_checkin("C1", "2026-08-10 08:00:00", "IN"),
            _mk_checkin("C2", "2026-08-10 08:00:00", "OUT"),
        ]
        result = classify_day(checkins, shift, "Mark as Invalid")

        self.assertEqual(result["status"], "Absent")
        self.assertAlmostEqual(result["hours"], 0.0, places=2)


class TestHolidayAsPresent(unittest.TestCase):
    """Public holidays (from Holiday List) should count as Present
    (paid day off), just like Sunday Weekly Off.
    """

    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_holidays_in_range")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_shift_for_employee")
    def test_holiday_no_checkins_counts_as_present(self, mock_shift, mock_holidays):
        """A holiday with no checkins should count as Present (1.0), not Absent."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import process_employee

        mock_shift.return_value = _day_shift()
        mock_holidays.return_value = {date(2026, 8, 15): "Independence Day"}  # Friday is a holiday

        result = process_employee(
            employee="HR-EMP-00001",
            from_date="2026-08-15",
            to_date="2026-08-15",
            checkin_list=[],
            default_shift_name=None,
            doc_missing_action="Mark as Invalid",
        )

        # Holiday with no checkins → Present (paid day off), no absent hours
        self.assertEqual(result["working_days"], 1.0)
        self.assertAlmostEqual(result["absent_hours"], 0.0, places=2)
        self.assertAlmostEqual(result["total_working_hours"], 0.0, places=2)

    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_holidays_in_range")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_shift_for_employee")
    def test_holiday_with_checkins_counts_as_present(self, mock_shift, mock_holidays):
        """A holiday with checkins should count as Present and all hours as Holiday OT."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import process_employee

        mock_shift.return_value = _day_shift()
        mock_holidays.return_value = {date(2026, 8, 15): "Independence Day"}  # Friday is a holiday

        checkins = [
            _mk_checkin("C1", "2026-08-15 08:00:00", "IN"),
            _mk_checkin("C2", "2026-08-15 16:00:00", "OUT"),
        ]
        result = process_employee(
            employee="HR-EMP-00001",
            from_date="2026-08-15",
            to_date="2026-08-15",
            checkin_list=checkins,
            default_shift_name=None,
            doc_missing_action="Mark as Invalid",
        )

        # Holiday with checkins → Present, all hours = Holiday OT
        self.assertEqual(result["working_days"], 1.0)
        self.assertAlmostEqual(result["total_working_hours"], 8.0, places=2)
        self.assertAlmostEqual(result["holiday_ot_hours"], 8.0, places=2)
        self.assertAlmostEqual(result["absent_hours"], 0.0, places=2)


class TestManualCheckinRemark(unittest.TestCase):
    """
    save_manual_checkin_record must store the remark on the Employee Checkin
    (zk_remark) when the field exists, and stay silent when it doesn't
    (pre-patch databases).
    """

    def _run(self, remark_value, has_remark_col=True):
        """Returns (inserted_doc, get_doc_data, result)."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import save_manual_checkin_record

        emp_doc = MagicMock()
        emp_doc.employee_name = "Test Employee"

        doc = MagicMock()
        doc.name = "CHK-REMARK-001"
        captured_data = {}

        def fake_get_doc(data):
            if isinstance(data, dict):
                captured_data.update(data)
                return doc
            return doc

        with patch("zkteco_attendance.zkteco_attendance.attendance_processor.has_column",
                   side_effect=lambda doctype, fieldname: has_remark_col if fieldname == "zk_remark" else False), \
             patch("frappe.db.exists", return_value=False), \
             patch("frappe.db.get_value", return_value=emp_doc), \
             patch("frappe.get_doc", side_effect=fake_get_doc), \
             patch("frappe.db.commit"):
            result = save_manual_checkin_record(
                employee="HR-EMP-00001",
                checkin_time="2026-08-10 08:00:00",
                log_type="IN",
                remark=remark_value,
            )
        return doc, captured_data, result

    def test_remark_is_stored_on_created_checkin(self):
        """The remark lands in the new checkin's zk_remark field."""
        doc, data, result = self._run("Gate was locked")

        self.assertEqual(result["action"], "created")
        self.assertEqual(data.get("zk_remark"), "Gate was locked")
        doc.insert.assert_called_once_with(ignore_permissions=True)

    def test_none_remark_is_normalized_to_empty_string(self):
        """A missing remark is stored as an empty string, not None."""
        doc, data, result = self._run(None)

        self.assertEqual(result["action"], "created")
        self.assertEqual(data.get("zk_remark"), "")

    def test_remark_skipped_without_column(self):
        """Pre-patch DB: no zk_remark column -> no error, no remark set."""
        doc, data, result = self._run("Gate was locked", has_remark_col=False)

        self.assertEqual(result["action"], "created")
        self.assertNotIn("zk_remark", data)
        doc.insert.assert_called_once()


class TestDailyCheckinsBiometricDeviceFilter(unittest.TestCase):
    """
    The Biometric Device dropdown on the Daily Checkins page must actually
    narrow the employee list (and therefore the check-in rows shown), whether
    the page was opened from an Attendance Summary or standalone.
    Previously the filter was ignored in summary mode, so all summary
    employees (and their rows) were shown regardless of the selected device.
    """

    def _make_summary_doc(self, rows):
        """Fake Attendance Summary doc whose details mimic child-table rows."""
        from types import SimpleNamespace

        details = []
        for emp, device, att_id in rows:
            details.append(SimpleNamespace(
                employee=emp,
                employee_name=emp.replace("-", " "),
                department="Dept",
                designation="",
                zk_biometric_device=device,
                attendance_device_id=att_id,
            ))
        return SimpleNamespace(
            name="SUM-0001",
            from_date=date(2026, 8, 1),
            to_date=date(2026, 8, 31),
            company="Acme",
            details=details,
            missing_checkin_action="Mark as Invalid",
            shift_type=None,
        )

    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_shift_for_employee")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_employee_daily_breakdown")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.fetch_checkins")
    @patch("frappe.get_all")
    @patch("frappe.get_doc")
    def test_summary_mode_filters_employees_by_device(
            self, mock_get_doc, mock_get_all, mock_fetch, mock_breakdown, mock_shift):
        """From an Attendance Summary, only employees on the selected device remain."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import get_daily_checkins_data

        mock_get_doc.return_value = self._make_summary_doc([
            ("EMP-A", "DEV-A", "101"),
            ("EMP-B", "DEV-B", "202"),   # different device -> must be filtered out
            ("EMP-C", "DEV-A", "103"),
        ])
        mock_fetch.return_value = {}  # no check-ins needed for the filter assertions
        mock_breakdown.return_value = []
        mock_shift.return_value = None
        mock_get_all.return_value = [
            {"name": "EMP-A", "employee_name": "EMP A", "department": "Dept",
             "designation": "", "zk_biometric_device": "DEV-A",
             "attendance_device_id": "101"},
            {"name": "EMP-C", "employee_name": "EMP C", "department": "Dept",
             "designation": "", "zk_biometric_device": "DEV-A",
             "attendance_device_id": "103"},
        ]

        result = get_daily_checkins_data(
            attendance_summary="SUM-0001",
            from_date="2026-08-01",
            to_date="2026-08-31",
            biometric_device="DEV-A",
        )

        emp_ids = [e["employee"] for e in result["employees"]]
        self.assertEqual(emp_ids, ["EMP-A", "EMP-C"])
        # Check-in rows are only fetched for the filtered employees
        mock_fetch.assert_called_once()
        fetched_list = mock_fetch.call_args[0][0]
        self.assertEqual(fetched_list, ["EMP-A", "EMP-C"])
        # Page header name field: `fullname` must be populated for every employee
        by_emp = {e["employee"]: e for e in result["employees"]}
        self.assertEqual(by_emp["EMP-A"]["fullname"], "EMP A")
        self.assertEqual(by_emp["EMP-C"]["fullname"], "EMP C")

    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_shift_for_employee")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_employee_daily_breakdown")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.fetch_checkins")
    @patch("frappe.get_all")
    def test_standalone_explicit_list_filters_employees_by_device(
            self, mock_get_all, mock_fetch, mock_breakdown, mock_shift):
        """Standalone with an explicit employee list: device filter intersects the list."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import get_daily_checkins_data

        # Employee master: EMP-A is on DEV-A, EMP-B is on DEV-B
        def fake_get_all(doctype, filters=None, fields=None, **kwargs):
            if doctype != "Employee":
                return []
            names = filters["name"][1]
            device = filters["zk_biometric_device"]
            by_name = {"EMP-A": "DEV-A", "EMP-B": "DEV-B"}
            selected = [n for n in names if by_name.get(n) == device]
            if fields == ["name"]:
                return [{"name": n} for n in selected]
            return [{"name": n, "employee_name": n.replace("-", " "),
                     "department": "Dept", "designation": "",
                     "zk_biometric_device": by_name[n],
                     "attendance_device_id": "1"} for n in selected]

        mock_get_all.side_effect = fake_get_all
        mock_fetch.return_value = {}
        mock_breakdown.return_value = []
        mock_shift.return_value = None

        result = get_daily_checkins_data(
            attendance_summary=None,
            from_date="2026-08-01",
            to_date="2026-08-31",
            employee_list=["EMP-A", "EMP-B"],
            biometric_device="DEV-A",
        )

        emp_ids = [e["employee"] for e in result["employees"]]
        self.assertEqual(emp_ids, ["EMP-A"])
        self.assertEqual(result["employees"][0]["fullname"], "EMP A")

    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_shift_for_employee")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_employee_daily_breakdown")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.fetch_checkins")
    @patch("frappe.get_all")
    @patch("frappe.get_doc")
    def test_summary_mode_no_match_returns_empty_payload(
            self, mock_get_doc, mock_get_all, mock_fetch, mock_breakdown, mock_shift):
        """No employee on the selected device -> empty employees, not an error."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import get_daily_checkins_data

        mock_get_doc.return_value = self._make_summary_doc([("EMP-B", "DEV-B", "202")])
        mock_fetch.return_value = {}
        mock_breakdown.return_value = []
        mock_shift.return_value = None
        mock_get_all.return_value = []

        result = get_daily_checkins_data(
            attendance_summary="SUM-0001",
            from_date="2026-08-01",
            to_date="2026-08-31",
            biometric_device="DEV-A",
        )

        self.assertEqual(result["employees"], [])
        mock_fetch.assert_not_called()


class TestDailyCheckinsProjectFilter(unittest.TestCase):
    """
    The Project dropdown on the Daily Checkins page must narrow the results
    to employees whose Project (Employee master) matches — the same field the
    Attendance Summary "Fetch Employees" dialog filters on — whether the page
    was opened from a summary or standalone.
    """

    def _make_summary_doc(self, employees):
        """Fake Attendance Summary doc whose details mimic child-table rows."""
        from types import SimpleNamespace

        details = []
        for emp in employees:
            details.append(SimpleNamespace(
                employee=emp,
                employee_name=emp.replace("-", " "),
                department="Dept",
                designation="",
                zk_biometric_device="",
                attendance_device_id="",
            ))
        return SimpleNamespace(
            name="SUM-0001",
            from_date=date(2026, 8, 1),
            to_date=date(2026, 8, 31),
            company="Acme",
            details=details,
            missing_checkin_action="Mark as Invalid",
            shift_type=None,
        )

    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_shift_for_employee")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_employee_daily_breakdown")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.fetch_checkins")
    @patch("frappe.get_all")
    @patch("frappe.get_doc")
    def test_summary_mode_filters_employees_by_project(
            self, mock_get_doc, mock_get_all, mock_fetch, mock_breakdown, mock_shift):
        """From an Attendance Summary, only employees on the selected project remain."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import get_daily_checkins_data

        mock_get_doc.return_value = self._make_summary_doc(["EMP-A", "EMP-B", "EMP-C"])
        mock_fetch.return_value = {}  # no check-ins needed for the filter assertions
        mock_breakdown.return_value = []
        mock_shift.return_value = None
        # Serves both the project lookup and the emp_info lookup
        mock_get_all.return_value = [
            {"name": "EMP-A", "employee_name": "EMP A", "department": "Dept",
             "designation": "", "zk_biometric_device": "", "attendance_device_id": ""},
            {"name": "EMP-C", "employee_name": "EMP C", "department": "Dept",
             "designation": "", "zk_biometric_device": "", "attendance_device_id": ""},
        ]

        result = get_daily_checkins_data(
            attendance_summary="SUM-0001",
            from_date="2026-08-01",
            to_date="2026-08-31",
            project="PRJ-1",
        )

        # The project lookup must query Employee with the project filter
        project_call = next(
            c for c in mock_get_all.call_args_list
            if c.kwargs.get("filters", {}).get("project") == "PRJ-1"
        )
        self.assertEqual(project_call.kwargs["filters"]["name"], ["in", ["EMP-A", "EMP-B", "EMP-C"]])

        emp_ids = [e["employee"] for e in result["employees"]]
        self.assertEqual(emp_ids, ["EMP-A", "EMP-C"])
        # Check-in rows are only fetched for the filtered employees
        mock_fetch.assert_called_once()
        self.assertEqual(mock_fetch.call_args[0][0], ["EMP-A", "EMP-C"])
        by_emp = {e["employee"]: e for e in result["employees"]}
        self.assertEqual(by_emp["EMP-A"]["fullname"], "EMP A")
        self.assertEqual(by_emp["EMP-C"]["fullname"], "EMP C")

    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_shift_for_employee")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_employee_daily_breakdown")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.fetch_checkins")
    @patch("frappe.get_all")
    def test_standalone_explicit_list_filters_employees_by_project(
            self, mock_get_all, mock_fetch, mock_breakdown, mock_shift):
        """Standalone with an explicit employee list: project filter intersects the list."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import get_daily_checkins_data

        emp_projects = {"EMP-A": "PRJ-1", "EMP-B": "PRJ-2"}

        def fake_get_all(doctype, filters=None, fields=None, **kwargs):
            if doctype != "Employee":
                return []
            names = filters["name"][1]
            if "project" in filters:
                selected = [n for n in names if emp_projects.get(n) == filters["project"]]
            else:
                selected = list(names)
            if fields == ["name"]:
                return [{"name": n} for n in selected]
            return [{"name": n, "employee_name": n.replace("-", " "),
                     "department": "Dept", "designation": "",
                     "zk_biometric_device": "", "attendance_device_id": "1"}
                    for n in selected]

        mock_get_all.side_effect = fake_get_all
        mock_fetch.return_value = {}
        mock_breakdown.return_value = []
        mock_shift.return_value = None

        result = get_daily_checkins_data(
            attendance_summary=None,
            from_date="2026-08-01",
            to_date="2026-08-31",
            employee_list=["EMP-A", "EMP-B"],
            project="PRJ-1",
        )

        emp_ids = [e["employee"] for e in result["employees"]]
        self.assertEqual(emp_ids, ["EMP-A"])
        self.assertEqual(result["employees"][0]["fullname"], "EMP A")

    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_shift_for_employee")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_employee_daily_breakdown")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.fetch_checkins")
    @patch("frappe.get_all")
    def test_standalone_autofetch_applies_project_filter(
            self, mock_get_all, mock_fetch, mock_breakdown, mock_shift):
        """Standalone without an explicit list: the auto-fetch query includes the project."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import get_daily_checkins_data

        emp_projects = {"EMP-A": "PRJ-1", "EMP-B": "PRJ-2", "EMP-C": "PRJ-1"}

        def fake_get_all(doctype, filters=None, fields=None, **kwargs):
            if doctype != "Employee":
                return []
            if "name" in filters:
                names = filters["name"][1]
                if "project" in filters:
                    selected = [n for n in names if emp_projects.get(n) == filters["project"]]
                else:
                    selected = list(names)
            else:
                # Auto-fetch query — the project filter must be included
                self.assertIn("project", filters)
                selected = [n for n, p in emp_projects.items() if p == filters["project"]]
            if fields == ["name"]:
                return [{"name": n} for n in selected]
            return [{"name": n, "employee_name": n.replace("-", " "),
                     "department": "Dept", "designation": "",
                     "zk_biometric_device": "", "attendance_device_id": "1"}
                    for n in selected]

        mock_get_all.side_effect = fake_get_all
        mock_fetch.return_value = {}
        mock_breakdown.return_value = []
        mock_shift.return_value = None

        result = get_daily_checkins_data(
            attendance_summary=None,
            from_date="2026-08-01",
            to_date="2026-08-31",
            project="PRJ-1",
        )

        emp_ids = [e["employee"] for e in result["employees"]]
        self.assertEqual(emp_ids, ["EMP-A", "EMP-C"])

    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_shift_for_employee")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_employee_daily_breakdown")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.fetch_checkins")
    @patch("frappe.get_all")
    @patch("frappe.get_doc")
    def test_summary_mode_no_project_match_returns_empty_payload(
            self, mock_get_doc, mock_get_all, mock_fetch, mock_breakdown, mock_shift):
        """No employee on the selected project -> empty employees, not an error."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import get_daily_checkins_data

        mock_get_doc.return_value = self._make_summary_doc(["EMP-B"])
        mock_fetch.return_value = {}
        mock_breakdown.return_value = []
        mock_shift.return_value = None
        mock_get_all.return_value = []

        result = get_daily_checkins_data(
            attendance_summary="SUM-0001",
            from_date="2026-08-01",
            to_date="2026-08-31",
            project="PRJ-1",
        )

        self.assertEqual(result["employees"], [])
        mock_fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
