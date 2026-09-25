"""
Unit tests for ZKTeco Attendance sync engine.
Run with: bench run-tests --app zkteco_attendance
"""

import unittest
from unittest.mock import patch, MagicMock
import frappe
from frappe.utils import now_datetime, get_datetime, getdate


class TestSyncEngine(unittest.TestCase):

    def setUp(self):
        """Create a test Biometric Device."""
        if not frappe.db.exists("Biometric Device", "Test-ZK-Device"):
            device = frappe.get_doc({
                "doctype": "Biometric Device",
                "device_name": "Test-ZK-Device",
                "device_ip": "192.168.1.100",
                "port": 4370,
                "company": frappe.defaults.get_global_default("company"),
                "status": "Active",
                "time_zone": "UTC",
                "fetch_mode": "All Records",
                "auto_sync_enabled": 1,
                "sync_frequency": "5 Min",
            })
            device.insert(ignore_permissions=True)
            frappe.db.commit()

    def tearDown(self):
        frappe.db.rollback()

    @patch("zkteco_attendance.zkteco_attendance.zk_client.ZK")
    def test_test_connection_success(self, mock_zk_class):
        """Test that test_connection returns correct structure on success."""
        mock_conn = MagicMock()
        mock_conn.get_serialnumber.return_value = "ABC123"
        mock_conn.get_firmware_version.return_value = "6.60"
        mock_conn.get_time.return_value = now_datetime()
        mock_conn.get_users.return_value = [MagicMock()] * 5
        mock_conn.get_attendance.return_value = [MagicMock()] * 20
        mock_zk_instance = MagicMock()
        mock_zk_instance.connect.return_value = mock_conn
        mock_zk_class.return_value = mock_zk_instance

        from zkteco_attendance.zkteco_attendance.zk_client import test_device_connection
        result = test_device_connection("Test-ZK-Device")

        self.assertTrue(result["success"])
        self.assertEqual(result["enrolled_users"], 5)
        self.assertEqual(result["attendance_logs"], 20)

    def test_get_employee_by_biometric_id_not_found(self):
        """Should return None when no employee matches."""
        from zkteco_attendance.zkteco_attendance.sync_engine import get_employee_by_biometric_id
        result = get_employee_by_biometric_id("NONEXISTENT_9999")
        self.assertIsNone(result)

    def test_checkin_duplicate_detection(self):
        """Duplicate checkin within 60s window should be detected."""
        from zkteco_attendance.zkteco_attendance.sync_engine import checkin_exists
        # Without any actual checkin record this should return falsy
        ts = now_datetime()
        result = checkin_exists("HR-EMP-00001", ts, "Test-ZK-Device")
        self.assertFalse(result)

    @patch("zkteco_attendance.zkteco_attendance.sync_engine._save_sync_log")
    @patch("zkteco_attendance.zkteco_attendance.sync_engine.create_employee_checkin")
    @patch("zkteco_attendance.zkteco_attendance.sync_engine.get_existing_checkins")
    @patch("zkteco_attendance.zkteco_attendance.sync_engine.get_shift_for_employee")
    @patch("zkteco_attendance.zkteco_attendance.sync_engine.get_employee_by_biometric_id")
    @patch("zkteco_attendance.zkteco_attendance.sync_engine.pull_attendance_from_device")
    def test_new_records_only_pulls_new_punch_when_uid_already_synced(self, mock_pull, mock_emp, mock_shift, mock_existing, mock_create, mock_log):
        """
        Regression test: on real ZKTeco devices (and in pyzk) an attendance
        log's `uid` is the user's device-internal id, REUSED by every punch
        of that user — it is not a unique per-log id. The old "New Records
        Only" pre-filter (raw_record_already_pulled by zk_uid) therefore
        skipped every new punch of any employee who already had one
        check-in, so new attendance stopped being pulled. Only the
        employee + time (±60s) duplicate check may skip a record.
        """
        from zkteco_attendance.zkteco_attendance.sync_engine import sync_device

        if not frappe.db.exists("Biometric Device", "Test-ZK-NewOnly"):
            device = frappe.get_doc({
                "doctype": "Biometric Device",
                "device_name": "Test-ZK-NewOnly",
                "device_ip": "192.168.1.101",
                "port": 4370,
                "company": frappe.defaults.get_global_default("company"),
                "status": "Active",
                "time_zone": "UTC",
                "fetch_mode": "New Records Only",
                "auto_sync_enabled": 1,
                "sync_frequency": "5 Min",
            })
            device.insert(ignore_permissions=True)
            frappe.db.commit()

        t1 = get_datetime("2026-01-05 09:00:00")
        t2 = get_datetime("2026-01-05 17:00:00")

        # Two punches of the SAME employee on the SAME device share uid=7
        # (device-internal user index), exactly like real device data.
        mock_pull.return_value = [
            {"uid": 7, "user_id": "100", "timestamp": t1, "punch": 0, "status": 0},
            {"uid": 7, "user_id": "100", "timestamp": t2, "punch": 0, "status": 0},
        ]
        mock_emp.return_value = {"name": "HR-EMP-00001", "employee_name": "Test", "company": "Acme"}
        mock_shift.return_value = {}

        # t1 already exists in the DB (duplicate); t2 is a brand-new punch.
        mock_existing.return_value = {("HR-EMP-00001", t1)}
        mock_create.return_value = "CHECKIN-1"

        result = sync_device("Test-ZK-NewOnly", triggered_by="Test", user="Administrator")

        self.assertTrue(result["success"])
        self.assertEqual(result["new_records"], 1)
        self.assertEqual(result["duplicates"], 1)
        self.assertEqual(result["failed"], 0)

        # The new punch (uid re-used from t1) must still be created.
        mock_create.assert_called_once()
        args, kwargs = mock_create.call_args
        self.assertEqual(args[:3], ("HR-EMP-00001", "Test", t2))
        self.assertEqual(kwargs.get("uid"), 7)

    @patch("zkteco_attendance.zkteco_attendance.sync_engine._save_sync_log")
    @patch("zkteco_attendance.zkteco_attendance.sync_engine.create_employee_checkin")
    @patch("zkteco_attendance.zkteco_attendance.sync_engine.get_existing_checkins")
    @patch("zkteco_attendance.zkteco_attendance.sync_engine.get_shift_for_employee")
    @patch("zkteco_attendance.zkteco_attendance.sync_engine.get_employee_by_biometric_id")
    @patch("zkteco_attendance.zkteco_attendance.sync_engine.pull_attendance_from_device")
    def test_sync_data_after_drops_punches_before_cutoff(self, mock_pull, mock_emp, mock_shift, mock_existing, mock_create, mock_log):
        """
        When the device has sync_data_after set, punches strictly before
        that date are dropped right after the pull and never become
        Employee Checkins. Punches ON the cutoff date are kept (inclusive).
        """
        from zkteco_attendance.zkteco_attendance.sync_engine import sync_device

        if not frappe.db.exists("Biometric Device", "Test-ZK-Cutoff"):
            device = frappe.get_doc({
                "doctype": "Biometric Device",
                "device_name": "Test-ZK-Cutoff",
                "device_ip": "192.168.1.102",
                "port": 4370,
                "company": frappe.defaults.get_global_default("company"),
                "status": "Active",
                "time_zone": "UTC",
                "fetch_mode": "All Records",
                "auto_sync_enabled": 1,
                "sync_frequency": "5 Min",
                "sync_data_after": getdate("2026-01-10"),
            })
            device.insert(ignore_permissions=True)
            frappe.db.commit()

        # Two punches BEFORE the cutoff date, one ON it, one after it.
        mock_pull.return_value = [
            {"uid": 7, "user_id": "100", "timestamp": get_datetime("2026-01-09 08:00:00"), "punch": 0, "status": 0},
            {"uid": 7, "user_id": "100", "timestamp": get_datetime("2026-01-09 17:00:00"), "punch": 0, "status": 0},
            {"uid": 7, "user_id": "100", "timestamp": get_datetime("2026-01-10 08:00:00"), "punch": 0, "status": 0},
            {"uid": 7, "user_id": "100", "timestamp": get_datetime("2026-01-11 17:00:00"), "punch": 0, "status": 0},
        ]
        mock_emp.return_value = {"name": "HR-EMP-00001", "employee_name": "Test", "company": "Acme"}
        mock_shift.return_value = {}
        mock_existing.return_value = set()
        mock_create.return_value = "CHECKIN-1"

        result = sync_device("Test-ZK-Cutoff", triggered_by="Test", user="Administrator")

        self.assertTrue(result["success"])
        # Only the punch ON the cutoff date and the one after it are created.
        self.assertEqual(result["new_records"], 2)
        self.assertEqual(result["total_records"], 2)
        self.assertEqual(result["failed"], 0)

        created_times = sorted(call[0][2] for call in mock_create.call_args_list)
        self.assertEqual(created_times, [
            get_datetime("2026-01-10 08:00:00"),
            get_datetime("2026-01-11 17:00:00"),
        ])

    @patch("zkteco_attendance.zkteco_attendance.sync_engine._save_sync_log")
    @patch("zkteco_attendance.zkteco_attendance.sync_engine.create_employee_checkin")
    @patch("zkteco_attendance.zkteco_attendance.sync_engine.get_existing_checkins")
    @patch("zkteco_attendance.zkteco_attendance.sync_engine.get_shift_for_employee")
    @patch("zkteco_attendance.zkteco_attendance.sync_engine.get_employee_by_biometric_id")
    @patch("zkteco_attendance.zkteco_attendance.sync_engine.pull_attendance_from_device")
    def test_sync_data_after_empty_keeps_all_punches(self, mock_pull, mock_emp, mock_shift, mock_existing, mock_create, mock_log):
        """With no sync_data_after set, every pulled punch is processed as before."""
        from zkteco_attendance.zkteco_attendance.sync_engine import sync_device

        if not frappe.db.exists("Biometric Device", "Test-ZK-NoCutoff"):
            device = frappe.get_doc({
                "doctype": "Biometric Device",
                "device_name": "Test-ZK-NoCutoff",
                "device_ip": "192.168.1.103",
                "port": 4370,
                "company": frappe.defaults.get_global_default("company"),
                "status": "Active",
                "time_zone": "UTC",
                "fetch_mode": "All Records",
                "auto_sync_enabled": 1,
                "sync_frequency": "5 Min",
            })
            device.insert(ignore_permissions=True)
            frappe.db.commit()

        mock_pull.return_value = [
            {"uid": 7, "user_id": "100", "timestamp": get_datetime("2025-06-01 08:00:00"), "punch": 0, "status": 0},
            {"uid": 7, "user_id": "100", "timestamp": get_datetime("2025-06-02 08:00:00"), "punch": 0, "status": 0},
        ]
        mock_emp.return_value = {"name": "HR-EMP-00001", "employee_name": "Test", "company": "Acme"}
        mock_shift.return_value = {}
        mock_existing.return_value = set()
        mock_create.return_value = "CHECKIN-1"

        result = sync_device("Test-ZK-NoCutoff", triggered_by="Test", user="Administrator")

        self.assertTrue(result["success"])
        self.assertEqual(result["total_records"], 2)
        self.assertEqual(result["new_records"], 2)

    def test_get_punch_type_mapping(self):
        """Punch codes should map correctly to IN/OUT."""
        from zkteco_attendance.zkteco_attendance.zk_client import get_punch_type
        self.assertEqual(get_punch_type(0), "IN")
        self.assertEqual(get_punch_type(1), "OUT")
        self.assertEqual(get_punch_type(4), "IN")
        self.assertEqual(get_punch_type(5), "OUT")
        self.assertEqual(get_punch_type(99), "IN")  # default

    def test_is_overtime_punch(self):
        from zkteco_attendance.zkteco_attendance.zk_client import is_overtime_punch
        self.assertTrue(is_overtime_punch(4))
        self.assertTrue(is_overtime_punch(5))
        self.assertFalse(is_overtime_punch(0))
        self.assertFalse(is_overtime_punch(1))

    def test_resolve_log_types_alternates_in_out(self):
        """
        Devices that send punch=0 for every punch should still get
        alternating IN/OUT for a two-punch shift.
        """
        from zkteco_attendance.zkteco_attendance.sync_engine import resolve_log_types_for_day

        morning = now_datetime().replace(hour=7, minute=55, second=0, microsecond=0)
        evening = morning.replace(hour=17, minute=10)

        day_records = [
            {"punch": 0, "timestamp": morning},
            {"punch": 0, "timestamp": evening},  # device also sends punch=0
        ]
        result = resolve_log_types_for_day(day_records)
        self.assertEqual(result, ["IN", "OUT"])

    def test_resolve_log_types_preserves_overtime_punches(self):
        """Explicit OT In/Out punches (4/5) keep their meaning and are not
        part of the regular IN/OUT alternation."""
        from zkteco_attendance.zkteco_attendance.sync_engine import resolve_log_types_for_day

        t1 = now_datetime().replace(hour=8, minute=0, second=0, microsecond=0)
        t2 = t1.replace(hour=17, minute=0)
        t3 = t1.replace(hour=18, minute=0)  # OT in
        t4 = t1.replace(hour=20, minute=0)  # OT out

        day_records = [
            {"punch": 0, "timestamp": t1},  # regular IN
            {"punch": 0, "timestamp": t2},  # regular OUT
            {"punch": 4, "timestamp": t3},  # OT in
            {"punch": 5, "timestamp": t4},  # OT out
        ]
        result = resolve_log_types_for_day(day_records)
        self.assertEqual(result, ["IN", "OUT", "IN", "OUT"])

    # ── Night-shift grouping across midnight ──────────────────────────────

    @patch("zkteco_attendance.zkteco_attendance.sync_engine.get_employee_by_biometric_id")
    @patch("zkteco_attendance.zkteco_attendance.sync_engine.get_shift_for_employee")
    def test_attendance_date_for_punch_night_shift(self, mock_shift, mock_emp):
        """Early-AM punches on a night shift belong to the previous day."""
        from zkteco_attendance.zkteco_attendance.sync_engine import get_attendance_date_for_punch

        mock_emp.return_value = {"name": "HR-EMP-00001", "employee_name": "Test", "company": "Acme"}
        mock_shift.return_value = {"is_night_shift": 1, "end_time": "06:00:00"}

        device = frappe.get_doc("Biometric Device", "Test-ZK-Device")
        emp_cache, shift_cache = {}, {}

        evening = get_attendance_date_for_punch(
            "100", get_datetime("2026-01-01 17:03:00"), device, emp_cache, shift_cache)
        morning = get_attendance_date_for_punch(
            "100", get_datetime("2026-01-02 05:58:00"), device, emp_cache, shift_cache)

        self.assertEqual(evening, getdate("2026-01-01"))
        self.assertEqual(morning, getdate("2026-01-01"))

    @patch("zkteco_attendance.zkteco_attendance.sync_engine.get_employee_by_biometric_id")
    @patch("zkteco_attendance.zkteco_attendance.sync_engine.get_shift_for_employee")
    def test_attendance_date_for_punch_day_shift(self, mock_shift, mock_emp):
        """Early-AM punches on a day shift stay on their own calendar day."""
        from zkteco_attendance.zkteco_attendance.sync_engine import get_attendance_date_for_punch

        mock_emp.return_value = {"name": "HR-EMP-00001", "employee_name": "Test", "company": "Acme"}
        mock_shift.return_value = {"is_night_shift": 0, "end_time": "17:00:00"}

        device = frappe.get_doc("Biometric Device", "Test-ZK-Device")
        emp_cache, shift_cache = {}, {}

        d = get_attendance_date_for_punch(
            "100", get_datetime("2026-01-05 05:30:00"), device, emp_cache, shift_cache)
        self.assertEqual(d, getdate("2026-01-05"))

    @patch("zkteco_attendance.zkteco_attendance.sync_engine.get_employee_by_biometric_id")
    def test_attendance_date_for_punch_no_employee(self, mock_emp):
        """Unmatched employee -> raw calendar date (no shift rollback)."""
        from zkteco_attendance.zkteco_attendance.sync_engine import get_attendance_date_for_punch

        mock_emp.return_value = None
        device = frappe.get_doc("Biometric Device", "Test-ZK-Device")
        emp_cache, shift_cache = {}, {}

        d = get_attendance_date_for_punch(
            "9999", get_datetime("2026-01-02 05:58:00"), device, emp_cache, shift_cache)
        self.assertEqual(d, getdate("2026-01-02"))

    @patch("zkteco_attendance.zkteco_attendance.sync_engine.get_employee_by_biometric_id")
    @patch("zkteco_attendance.zkteco_attendance.sync_engine.get_shift_for_employee")
    def test_night_shift_grouping_across_midnight(self, mock_shift, mock_emp):
        """
        Night shift: IN 01/01 17:03 + OUT 02/01 05:58 must land in the same
        (user, attendance_date) group and alternate IN -> OUT, instead of
        each becoming the first (IN) punch of their own calendar day.
        """
        from zkteco_attendance.zkteco_attendance.sync_engine import (
            group_records_by_attendance_date,
            resolve_log_types_for_day,
        )

        mock_emp.return_value = {"name": "HR-EMP-00001", "employee_name": "Test", "company": "Acme"}
        mock_shift.return_value = {"is_night_shift": 1, "end_time": "06:00:00"}

        device = frappe.get_doc("Biometric Device", "Test-ZK-Device")
        records = [
            {"user_id": "100", "timestamp": get_datetime("2026-01-01 17:03:00"), "punch": 0, "uid": 1},
            {"user_id": "100", "timestamp": get_datetime("2026-01-02 05:58:00"), "punch": 0, "uid": 2},
        ]

        grouped, _ = group_records_by_attendance_date(records, device)

        self.assertIn(("100", getdate("2026-01-01")), grouped)
        self.assertNotIn(("100", getdate("2026-01-02")), grouped)
        self.assertEqual(len(grouped[("100", getdate("2026-01-01"))]), 2)

        day_recs = sorted(grouped[("100", getdate("2026-01-01"))], key=lambda r: r["timestamp"])
        self.assertEqual(resolve_log_types_for_day(day_recs), ["IN", "OUT"])

    @patch("zkteco_attendance.zkteco_attendance.sync_engine.get_employee_by_biometric_id")
    @patch("zkteco_attendance.zkteco_attendance.sync_engine.get_shift_for_employee")
    def test_consecutive_night_shifts_alternate_correctly(self, mock_shift, mock_emp):
        """
        Two consecutive night shifts (17:03->05:58, 17:10->05:58) must each
        alternate IN->OUT within their own attendance-day group.
        """
        from zkteco_attendance.zkteco_attendance.sync_engine import (
            group_records_by_attendance_date,
            resolve_log_types_for_day,
        )

        mock_emp.return_value = {"name": "HR-EMP-00001", "employee_name": "Test", "company": "Acme"}
        mock_shift.return_value = {"is_night_shift": 1, "end_time": "06:00:00"}

        device = frappe.get_doc("Biometric Device", "Test-ZK-Device")
        records = [
            {"user_id": "100", "timestamp": get_datetime("2026-01-01 17:03:00"), "punch": 0, "uid": 1},
            {"user_id": "100", "timestamp": get_datetime("2026-01-02 05:58:00"), "punch": 0, "uid": 2},
            {"user_id": "100", "timestamp": get_datetime("2026-01-02 17:10:00"), "punch": 0, "uid": 3},
            {"user_id": "100", "timestamp": get_datetime("2026-01-03 05:58:00"), "punch": 0, "uid": 4},
        ]

        grouped, _ = group_records_by_attendance_date(records, device)

        self.assertEqual(len(grouped), 2)
        for att_date, expected in [("2026-01-01", ["IN", "OUT"]), ("2026-01-02", ["IN", "OUT"])]:
            day_recs = sorted(grouped[("100", getdate(att_date))], key=lambda r: r["timestamp"])
            self.assertEqual(resolve_log_types_for_day(day_recs), expected)


class TestBiometricDeviceValidation(unittest.TestCase):

    def test_invalid_ip_rejected(self):
        """Device with invalid IP should fail validation."""
        device = frappe.get_doc({
            "doctype": "Biometric Device",
            "device_name": "Bad-IP-Device",
            "device_ip": "not_an_ip!!!",
            "port": 4370,
            "company": frappe.defaults.get_global_default("company"),
        })
        self.assertRaises(frappe.ValidationError, device.validate)

    def test_invalid_port_rejected(self):
        device = frappe.get_doc({
            "doctype": "Biometric Device",
            "device_name": "Bad-Port-Device",
            "device_ip": "192.168.1.1",
            "port": 99999,
            "company": frappe.defaults.get_global_default("company"),
        })
        self.assertRaises(frappe.ValidationError, device.validate)
