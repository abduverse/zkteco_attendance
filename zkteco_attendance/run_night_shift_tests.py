"""
Standalone harness: runs night-shift attribution tests WITHOUT frappe.

The real test file (tests/test_sync_engine.py) needs a bench to run.
This harness stubs only the frappe-facing pieces, then imports the REAL
logic functions and executes the same cases offline.

Run:  python run_night_shift_tests.py
"""
import sys, types
from datetime import datetime, timedelta
import unittest

# ---------------------------------------------------------------- stubs
_cache_mod = types.ModuleType("frappe.cache")
_core = types.ModuleType("frappe")
_utils = types.ModuleType("frappe.utils")


def get_datetime(v):
    if isinstance(v, datetime):
        return v
    return datetime.strptime(str(v)[:19], "%Y-%m-%d %H:%M:%S")


def getdate(v):
    s = str(v)
    if len(s) == 10:  # plain YYYY-MM-DD
        return datetime.strptime(s, "%Y-%m-%d").date()
    return get_datetime(v).date()


_core._ = lambda s: s
_core.session = type("S", (), {"user": "tester"})()
_core.db = type("DB", (), {"exists": staticmethod(lambda *a, **k: None),
                           "set_value": staticmethod(lambda *a, **k: None),
                           "sql": staticmethod(lambda *a, **k: [])})()
_core.cache = staticmethod(lambda: type("C", (), {
    "get_value": staticmethod(lambda *a, **k: None),
    "set_value": staticmethod(lambda *a, **k: None),
    "delete_value": staticmethod(lambda *a, **k: None)})())
_core.publish_realtime = lambda *a, **k: None
_core.log_error = lambda *a, **k: None
_core.get_doc = lambda *a, **k: None
_core.generate_hash = lambda *a, **k: "x"
_utils.now_datetime = get_datetime
_utils.get_datetime = get_datetime
_utils.getdate = getdate
_utils.cint = lambda v: int(v or 0)
_utils.time_diff_in_seconds = lambda a, b: timedelta(0)
_utils.nowdate = lambda: get_datetime("2026-01-01 00:00:00").strftime("%Y-%m-%d")
_utils.flt = lambda v, p=None: round(float(v), p) if v is not None else 0.0
_utils.add_days = lambda d, n=None: d
_utils.add_to_date = lambda d, **k: d

sys.modules["frappe"] = _core
sys.modules["frappe.utils"] = _utils
sys.modules["frappe.cache"] = _cache_mod

sys.path.insert(0, ".")
sys.path.insert(0, "zkteco_attendance")
# lightweight stand-ins for third-party imports pulled in by submodules
for name in ("pytz", "zk"):
    if name not in sys.modules:
        stub = types.ModuleType(name)
        if name == "pytz":
            stub.timezone = lambda *a, **k: None
            stub.UnknownTimeZoneError = Exception
        sys.modules[name] = stub

# frappe.zkteco_attendance package alias so relative imports resolve.
# This checkout is the innermost app folder (…/zkteco_attendance/zkteco_attendance),
# so code does absolute `from zkteco_attendance.zkteco_attendance.utils import …`.
# Pre-register an alias package whose __path__ points at this folder, so those
# imports resolve, and import the submodules THROUGH the alias so that patch
# targets and imports share one module object.
import importlib
pkg = importlib.import_module("zkteco_attendance")
sys.modules["frappe.zkteco_attendance"] = pkg
pkg2 = types.ModuleType("zkteco_attendance.zkteco_attendance")
pkg2.__path__ = ["zkteco_attendance"]
sys.modules["zkteco_attendance.zkteco_attendance"] = pkg2
setattr(pkg, "zkteco_attendance", pkg2)
for name in ("utils", "zk_client", "attendance_processor", "sync_engine"):
    mod = importlib.import_module("zkteco_attendance.zkteco_attendance." + name)
    sys.modules["frappe.zkteco_attendance." + name] = mod
    setattr(pkg, name, mod)
    sys.modules["frappe.zkteco_attendance.zkteco_attendance." + name] = mod
    setattr(pkg2, name, mod)

from zkteco_attendance.zkteco_attendance.sync_engine import (  # noqa: E402
    get_attendance_date_for_punch,
    group_records_by_attendance_date,
    resolve_log_types_for_day,
)

emp = {"name": "HR-EMP-00001", "employee_name": "Test", "company": "Acme"}
DEVICE = type("D", (), {"name": "Dev", "company": "Acme"})()


class Harness(unittest.TestCase):
    def patched(self, shift):
        import unittest.mock as m
        pm = m.patch("zkteco_attendance.zkteco_attendance.sync_engine.get_shift_for_employee",
                     return_value=shift)
        pe = m.patch("zkteco_attendance.zkteco_attendance.sync_engine.get_employee_by_biometric_id",
                     return_value=emp)
        return pe, pm

    def check_punch_date(self, shift, ts, expected):
        pe, pm = self.patched(shift)
        with pe, pm:
            d = get_attendance_date_for_punch("100", get_datetime(ts), DEVICE, {}, {})
        self.assertEqual(d, getdate(expected), ts)

    # ---- cross-midnight 22:00-01:00 -----------------------------------
    def test_22_01_attribution(self):
        NS = {"is_night_shift": 1, "start_time": "22:00:00", "end_time": "01:00:00"}
        for ts, exp in [
            ("2026-02-01 22:12:01", "2026-02-01"),   # IN at start
            ("2026-02-02 00:00:00", "2026-02-01"),  # exact midnight
            ("2026-02-02 01:11:00", "2026-02-01"),  # late OUT past end
            ("2026-02-02 05:00:00", "2026-02-01"),  # way past end -> still start day
            ("2026-02-01 21:20:00", "2026-02-01"),  # early arrival in buffer
            ("2026-02-01 21:00:00", "2026-02-01"),  # buffer edge (60 min)
            ("2026-02-01 20:59:00", "2026-01-31"),  # beyond buffer -> prev day
            ("2026-02-02 10:00:00", "2026-02-01"),  # gap between nights
            ("2026-02-01 00:30:00", "2026-01-31"),  # early AM -> prior day
        ]:
            self.check_punch_date(NS, ts, exp)

    # ---- long cross-midnight 22:00-06:00 ------------------------------
    def test_22_06_attribution(self):
        NS = {"is_night_shift": 1, "start_time": "22:00:00", "end_time": "06:00:00"}
        for ts, exp in [
            ("2026-02-02 05:58:00", "2026-02-01"),  # OUT before 06:00
            ("2026-02-02 07:30:00", "2026-02-01"),  # late checkout
            ("2026-02-02 13:00:00", "2026-02-01"),  # gap day duty
            ("2026-02-01 16:00:00", "2026-01-31"),  # afternoon -> prev day
        ]:
            self.check_punch_date(NS, ts, exp)

    # ---- 17:00-06:00 (17h) -------------------------------------------
    def test_17_06_attribution(self):
        NS = {"is_night_shift": 1, "start_time": "17:00:00", "end_time": "06:00:00"}
        for ts, exp in [
            ("2026-01-01 17:03:00", "2026-01-01"),  # IN
            ("2026-01-02 05:58:00", "2026-01-01"),  # OUT same group
            # 16:00 is 60 min BEFORE the 17:00 start -> buffer boundary: dt >=
            # start-60min means it stays on the SAME day (start-day rule).
            ("2026-01-01 16:00:00", "2026-01-01"),
            ("2026-01-01 15:59:00", "2025-12-31"),  # beyond buffer -> prev day
        ]:
            self.check_punch_date(NS, ts, exp)

    # ---- day shift untouched -----------------------------------------
    def test_day_shift(self):
        DS = {"is_night_shift": 0, "start_time": "08:00:00", "end_time": "17:00:00"}
        for ts in ["2026-01-05 05:30:00", "2026-01-05 08:00:00",
                   "2026-01-05 17:00:00", "2026-01-05 20:00:00"]:
            self.check_punch_date(DS, ts, "2026-01-05")

    # ---- non-cross-midnight "night" flag (13:00-23:00) -----------------
    # Non-crossing branch: any punch with time <= end_t rolls back a day
    # (dt.time() <= end_t -> previous day). This means punches DURING a
    # non-crossing non-midnight-crossing night shift (22:00 <= 23:00) are
    # attributed to the PREVIOUS day — which is the same behavior
    # attendance_processor.group_checkins_by_date applies, so the two
    # implementations agree. Only punches past end_t (23:01+) keep their
    # own day via the buffered-start rule.
    def test_night_flag_but_not_crossing_midnight(self):
        NS = {"is_night_shift": 1, "start_time": "13:00:00", "end_time": "23:00:00"}
        for ts, exp in [
            ("2026-01-04 22:00:00", "2026-01-03"),  # 22:00 <= 23:00 end -> prev day
            ("2026-01-05 13:00:00", "2026-01-04"),  # 13:00 <= 23:00 -> prev day
            ("2026-01-05 23:30:00", "2026-01-05"),  # past end -> own day
        ]:
            self.check_punch_date(NS, ts, exp)

    # ---- grouping: 4 consecutive 22:00-06:00 swings --------------------
    def test_grouping_four_nights(self):
        NS = {"is_night_shift": 1, "start_time": "22:00:00", "end_time": "06:00:00"}
        pe, pm = self.patched(NS)
        records = []
        for day in range(1, 5):
            records.append({"user_id": "100", "uid": day * 2 - 1,
                            "timestamp": get_datetime("2026-02-{:02d} 21:58:00".format(day)),
                            "punch": 0})
            records.append({"user_id": "100", "uid": day * 2,
                            "timestamp": get_datetime("2026-02-{:02d} 06:02:00".format(day + 1)),
                            "punch": 0})
        with pe, pm:
            grouped, _ = group_records_by_attendance_date(records, DEVICE)
        self.assertEqual(len(grouped), 4)
        for day in range(1, 5):
            day_recs = sorted(grouped[("100", getdate("2026-02-{:02d}".format(day)))],
                              key=lambda r: r["timestamp"])
            self.assertEqual(len(day_recs), 2)
            self.assertEqual(day_recs[0]["timestamp"].day, day)
            self.assertEqual(resolve_log_types_for_day(day_recs), ["IN", "OUT"])

    # ---- grouping: missing OUT then next-day afternoon punch -----------
    def test_grouping_missing_out(self):
        NS = {"is_night_shift": 1, "start_time": "22:00:00", "end_time": "01:00:00"}
        pe, pm = self.patched(NS)
        records = [
            {"user_id": "100", "timestamp": get_datetime("2026-02-01 22:10:00"), "punch": 0, "uid": 1},
            {"user_id": "100", "timestamp": get_datetime("2026-02-02 18:00:00"), "punch": 0, "uid": 2},
        ]
        with pe, pm:
            grouped, _ = group_records_by_attendance_date(records, DEVICE)
        self.assertEqual(len(grouped), 1)
        self.assertEqual(len(grouped[("100", getdate("2026-02-01"))]), 2)

    # ---- consecutive shifts 17:00 -> 06:00 -----------------------------
    def test_grouping_consecutive_17_06(self):
        NS = {"is_night_shift": 1, "start_time": "17:00:00", "end_time": "06:00:00"}
        pe, pm = self.patched(NS)
        records = [
            {"user_id": "100", "uid": 1, "punch": 0, "timestamp": get_datetime("2026-01-01 17:03:00")},
            {"user_id": "100", "uid": 2, "punch": 0, "timestamp": get_datetime("2026-01-02 05:58:00")},
            {"user_id": "100", "uid": 3, "punch": 0, "timestamp": get_datetime("2026-01-02 17:10:00")},
            {"user_id": "100", "uid": 4, "punch": 0, "timestamp": get_datetime("2026-01-03 05:58:00")},
        ]
        with pe, pm:
            grouped, _ = group_records_by_attendance_date(records, DEVICE)
        self.assertEqual(len(grouped), 2)
        for d, expected in [("{\"2026-01-01\"", None)]:
            pass
        for d, expected in [("2026-01-01", ["IN", "OUT"]), ("2026-01-02", ["IN", "OUT"])]:
            day_recs = sorted(grouped[("100", getdate(d))], key=lambda r: r["timestamp"])
            self.assertEqual(resolve_log_types_for_day(day_recs), expected)

    # ---- shift cache re-lookup on a later date -------------------------
    def test_shift_cache_per_date(self):
        NS = {"is_night_shift": 1, "start_time": "22:00:00", "end_time": "01:00:00"}
        pe, pm = self.patched(NS)
        emp_cache, shift_cache = {}, {}
        with pe as mock_emp, pm as mock_shift:
            get_attendance_date_for_punch("100", get_datetime("2026-02-02 00:30:00"), DEVICE,
                                          emp_cache, shift_cache)
            get_attendance_date_for_punch("100", get_datetime("2026-03-01 00:30:00"), DEVICE,
                                          emp_cache, shift_cache)
        self.assertEqual(mock_shift.call_count, 2)  # looked up again for the new date

    # ---- no employee fallback ------------------------------------------
    def test_no_employee_fallback(self):
        import unittest.mock as m
        with m.patch("zkteco_attendance.zkteco_attendance.sync_engine.get_employee_by_biometric_id",
                     return_value=None):
            d = get_attendance_date_for_punch("9999", get_datetime("2026-01-02 05:58:00"),
                                              DEVICE, {}, {})
        self.assertEqual(d, getdate("2026-01-02"))

    # ---- round-the-clock 07:00 -> 07:00 (24 h) ------------------------
    def test_round_the_clock_attribution(self):
        RS = {"is_night_shift": 1, "start_time": "07:00:00", "end_time": "07:00:00"}
        for ts, exp in [
            ("2026-01-01 06:35:00", "2026-01-01"),  # early check-in
            ("2026-01-01 07:00:00", "2026-01-01"),  # exact handover
            ("2026-01-01 07:25:00", "2026-01-01"),  # late check-in
            ("2026-01-02 02:00:00", "2026-01-01"),  # overnight duty
            ("2026-01-02 09:30:00", "2026-01-02"),  # well past handover
        ]:
            self.check_punch_date(RS, ts, exp)

    def test_round_the_clock_grouping_acceptance(self):
        """IN 01/01 07:00, OUT 02/01 07:00, rest 03/01, IN 04/01, OUT 05/01."""
        RS = {"is_night_shift": 1, "start_time": "07:00:00", "end_time": "07:00:00"}
        pe, pm = self.patched(RS)
        records = [
            {"user_id": "100", "uid": 1, "punch": 0,
             "timestamp": get_datetime("2026-01-01 07:00:00")},
            {"user_id": "100", "uid": 2, "punch": 0,
             "timestamp": get_datetime("2026-01-02 07:00:00")},
            {"user_id": "100", "uid": 3, "punch": 0,
             "timestamp": get_datetime("2026-01-04 07:00:00")},
            {"user_id": "100", "uid": 4, "punch": 0,
             "timestamp": get_datetime("2026-01-05 07:00:00")},
        ]
        with pe, pm:
            grouped, _ = group_records_by_attendance_date(records, DEVICE)
        self.assertEqual(sorted(k[1] for k in grouped),
                         [getdate("2026-01-01"), getdate("2026-01-04")])
        for d, expected_uids in [("2026-01-01", [1, 2]), ("2026-01-04", [3, 4])]:
            day_recs = sorted(grouped[("100", getdate(d))], key=lambda r: r["timestamp"])
            self.assertEqual([r["uid"] for r in day_recs], expected_uids)
            self.assertEqual(resolve_log_types_for_day(day_recs), ["IN", "OUT"])

    def test_round_the_clock_punches_around_handover(self):
        """Punches anywhere in 06:30-07:30 keep the 24 h pairing intact."""
        RS = {"is_night_shift": 1, "start_time": "07:00:00", "end_time": "07:00:00"}
        pe, pm = self.patched(RS)
        records = [
            {"user_id": "100", "uid": 1, "punch": 0,
             "timestamp": get_datetime("2026-01-01 06:31:00")},
            {"user_id": "100", "uid": 2, "punch": 0,
             "timestamp": get_datetime("2026-01-02 07:29:00")},
            {"user_id": "100", "uid": 3, "punch": 0,
             "timestamp": get_datetime("2026-01-02 06:32:00")},
            {"user_id": "100", "uid": 4, "punch": 0,
             "timestamp": get_datetime("2026-01-03 07:28:00")},
        ]
        with pe, pm:
            grouped, _ = group_records_by_attendance_date(records, DEVICE)
        self.assertEqual(sorted(k[1] for k in grouped),
                         [getdate("2026-01-01"), getdate("2026-01-02")])
        self.assertEqual(len(grouped[("100", getdate("2026-01-01"))]), 2)
        self.assertEqual(len(grouped[("100", getdate("2026-01-02"))]), 2)

    def test_round_the_clock_mid_shift_punch_stays_in_cycle(self):
        RS = {"is_night_shift": 1, "start_time": "07:00:00", "end_time": "07:00:00"}
        pe, pm = self.patched(RS)
        records = [
            {"user_id": "100", "uid": 1, "punch": 0,
             "timestamp": get_datetime("2026-01-01 07:05:00")},
            {"user_id": "100", "uid": 2, "punch": 0,
             "timestamp": get_datetime("2026-01-01 19:00:00")},  # patrol
            {"user_id": "100", "uid": 3, "punch": 0,
             "timestamp": get_datetime("2026-01-02 07:10:00")},
        ]
        with pe, pm:
            grouped, _ = group_records_by_attendance_date(records, DEVICE)
        self.assertEqual(len(grouped), 1)
        self.assertEqual(len(grouped[("100", getdate("2026-01-01"))]), 3)

    # ---- OT punches inside night window --------------------------------
    def test_ot_punch_types(self):
        recs = [
            {"punch": 0, "timestamp": get_datetime("2026-02-01 22:12:01")},
            {"punch": 0, "timestamp": get_datetime("2026-02-02 01:11:00")},
            {"punch": 4, "timestamp": get_datetime("2026-02-02 01:30:00")},
            {"punch": 5, "timestamp": get_datetime("2026-02-02 03:00:00")},
        ]
        self.assertEqual(resolve_log_types_for_day(recs), ["IN", "OUT", "IN", "OUT"])


class ProcessorHarness(unittest.TestCase):
    """24-hour shift end-to-end through the real attendance processor."""

    SHIFT = {
        "name": "GUARD-24H",
        "start_time": "07:00:00",
        "end_time": "07:00:00",
        "is_night_shift": 1,
        "full_day_hours": 8,
        "half_day_hours": 4,
        "standard_working_hours": 8,
        "working_hours_method": "First IN - Last OUT",
        "saturday_mode": "Off",
        "saturday_half_day_hours": 4,
        "late_entry_grace": 10,
        "early_exit_grace": 10,
        "enable_overtime": 0,
        "overtime_calculation_method": "After Standard Hours",
        "overtime_threshold_minutes": 0,
        "max_overtime_hours_per_day": 0,
        "night_ot_start_time": "22:00:00",
        "night_ot_end_time": "06:00:00",
    }

    @staticmethod
    def _mk(name, ts, log_type):
        return {"name": name, "time": get_datetime(ts), "log_type": log_type,
                "is_overtime": False, "manually_edited": False,
                "edited_by": "", "edited_at": ""}

    def _checkins(self, punches):
        return [self._mk("C{}".format(i), ts, lt)
                for i, (ts, lt) in enumerate(punches, start=1)]

    def test_grouping_and_classification(self):
        from zkteco_attendance.zkteco_attendance.attendance_processor import (
            group_checkins_by_date, classify_day,
        )

        checkins = self._checkins([
            ("2026-01-01 07:00:00", "IN"),
            ("2026-01-02 07:00:00", "OUT"),
            ("2026-01-04 07:00:00", "IN"),
            ("2026-01-05 07:00:00", "OUT"),
        ])
        daily = group_checkins_by_date(checkins, self.SHIFT,
                                       from_date=getdate("2026-01-01"),
                                       to_date=getdate("2026-01-05"))
        self.assertEqual(sorted(daily), [getdate("2026-01-01"), getdate("2026-01-04")])
        for d in ("2026-01-01", "2026-01-04"):
            day = daily[getdate(d)]
            self.assertEqual([c["log_type"] for c in day], ["IN", "OUT"])
            result = classify_day(day, self.SHIFT, "Mark as Invalid",
                                  work_date=getdate(d))
            self.assertEqual(result["status"], "Present")
            self.assertAlmostEqual(result["hours"], 24.0, places=2)
            self.assertFalse(result["is_late"])
            self.assertFalse(result["is_early_exit"])

    def test_handover_window_and_grace(self):
        from zkteco_attendance.zkteco_attendance.attendance_processor import (
            group_checkins_by_date, classify_day,
        )

        checkins = self._checkins([
            ("2026-01-01 06:35:00", "IN"),   # 25 min early
            ("2026-01-02 07:20:00", "OUT"),  # 20 min past handover
        ])
        daily = group_checkins_by_date(checkins, self.SHIFT,
                                       from_date=getdate("2026-01-01"),
                                       to_date=getdate("2026-01-02"))
        self.assertEqual(sorted(daily), [getdate("2026-01-01")])
        result = classify_day(daily[getdate("2026-01-01")], self.SHIFT,
                              "Mark as Invalid", work_date=getdate("2026-01-01"))
        self.assertEqual(result["status"], "Present")
        self.assertAlmostEqual(result["hours"], 24 + 45 / 60.0, places=2)
        self.assertFalse(result["is_late"])
        self.assertFalse(result["is_early_exit"])

    def test_process_employee_no_invalid_days(self):
        import unittest.mock as m
        from zkteco_attendance.zkteco_attendance.attendance_processor import process_employee

        checkins = self._checkins([
            ("2026-01-01 07:00:00", "IN"),
            ("2026-01-02 07:00:00", "OUT"),
            ("2026-01-04 07:00:00", "IN"),
            ("2026-01-05 07:00:00", "OUT"),
        ])
        with m.patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_shift_for_employee",
                     return_value=self.SHIFT), \
             m.patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_holidays_in_range",
                     return_value={}):
            result = process_employee(
                employee="EMP-GUARD",
                from_date="2026-01-01",
                to_date="2026-01-05",
                checkin_list=checkins,
                default_shift_name="Guard 24h",
                doc_missing_action="Mark as Invalid",
            )
        self.assertEqual(result["invalid_days"], 0)
        self.assertNotIn("invalid checkin", result["remarks"])
        self.assertAlmostEqual(result["total_working_hours"], 48.0, places=2)


class RealCheckinsHarness(unittest.TestCase):
    """The Daily Checkins page path on real guard punches (Oct 2026)."""

    SHIFT = dict(ProcessorHarness.SHIFT, saturday_mode="Full Day")
    PUNCHES = [
        "2026-10-10 06:58:01",
        "2026-10-11 07:29:25",
        "2026-10-13 07:17:55",
        "2026-10-14 07:28:29",
        "2026-10-16 07:01:06",
        "2026-10-17 07:37:57",
        "2026-10-19 07:23:01",
        "2026-10-20 07:40:04",
    ]

    def _breakdown(self, shift):
        import unittest.mock as m
        from zkteco_attendance.zkteco_attendance.attendance_processor import (
            get_employee_daily_breakdown,
        )

        checkins = [
            {"name": "C{}".format(i), "time": get_datetime(ts), "log_type": "IN",
             "is_overtime": False, "manually_edited": False,
             "edited_by": "", "edited_at": ""}
            for i, ts in enumerate(self.PUNCHES, start=1)
        ]
        with m.patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_shift_for_employee",
                     return_value=shift), \
             m.patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_holidays_in_range",
                     return_value={}):
            return get_employee_daily_breakdown(
                employee="EMP-GUARD",
                from_date="2026-10-09",
                to_date="2026-10-20",
                checkin_list=checkins,
                default_shift_name=None,
                doc_missing_action="Mark as Invalid",
            )

    def test_real_punches_pair_into_24h_days(self):
        """Every 07:00 punch pairs with the previous day's check-in."""
        days = {str(d["date"]): d for d in self._breakdown(self.SHIFT)}

        # No day may be Invalid: each check-out is paired with its check-in.
        invalid = [d for d, row in days.items() if row["status"] == "Invalid"]
        self.assertEqual(invalid, [])

        expected = {
            "2026-10-10": ("06:58:01", "07:29:25"),
            "2026-10-13": ("07:17:55", "07:28:29"),
            "2026-10-16": ("07:01:06", "07:37:57"),
            "2026-10-19": ("07:23:01", "07:40:04"),
        }
        for day, (checkin, checkout) in expected.items():
            row = days[day]
            self.assertEqual(row["status"], "Present", day)
            self.assertEqual(len(row["checkins"]), 2, day)
            self.assertEqual([c["log_type"] for c in row["checkins"]], ["IN", "OUT"], day)
            self.assertEqual(row["checkins"][0]["time"], checkin, day)
            self.assertEqual(row["checkins"][1]["time"], checkout, day)
            self.assertGreater(row["hours"], 23.0, day)

        # The day a 24 h shift ends carries no punch of its own.
        for day in ("2026-10-11", "2026-10-14", "2026-10-17", "2026-10-20"):
            self.assertEqual(days[day]["checkins"], [], day)

    def test_real_punches_pair_without_night_flag(self):
        """Pairing keys off Start == End, not the Night Shift checkbox."""
        shift = dict(self.SHIFT, is_night_shift=0)
        days = {str(d["date"]): d for d in self._breakdown(shift)}
        self.assertEqual([d for d, row in days.items() if row["status"] == "Invalid"], [])
        for day in ("2026-10-10", "2026-10-13", "2026-10-16", "2026-10-19"):
            self.assertEqual(days[day]["status"], "Present", day)

    def test_blank_end_time_is_a_24h_shift(self):
        """A shift with no End Time set runs the full day."""
        shift = dict(self.SHIFT, end_time=None)
        days = {str(d["date"]): d for d in self._breakdown(shift)}
        self.assertEqual([d for d, row in days.items() if row["status"] == "Invalid"], [])
        for day in ("2026-10-10", "2026-10-13", "2026-10-16", "2026-10-19"):
            self.assertEqual(days[day]["status"], "Present", day)

    def test_midnight_end_time_is_not_a_24h_shift(self):
        """07:00 -> 00:00 is 17 hours and must keep the crossing rule."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import (
            is_round_the_clock_shift,
        )

        self.assertFalse(is_round_the_clock_shift(
            {"start_time": "07:00:00", "end_time": "00:00:00"}))
        self.assertFalse(is_round_the_clock_shift(
            {"start_time": "07:00:00", "end_time": timedelta(0)}))
        self.assertTrue(is_round_the_clock_shift(
            {"start_time": "07:00:00", "end_time": None}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
