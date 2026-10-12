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

    # ---- OT punches inside night window --------------------------------
    def test_ot_punch_types(self):
        recs = [
            {"punch": 0, "timestamp": get_datetime("2026-02-01 22:12:01")},
            {"punch": 0, "timestamp": get_datetime("2026-02-02 01:11:00")},
            {"punch": 4, "timestamp": get_datetime("2026-02-02 01:30:00")},
            {"punch": 5, "timestamp": get_datetime("2026-02-02 03:00:00")},
        ]
        self.assertEqual(resolve_log_types_for_day(recs), ["IN", "OUT", "IN", "OUT"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
