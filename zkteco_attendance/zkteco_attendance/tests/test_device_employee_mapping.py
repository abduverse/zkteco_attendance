"""
Unit tests for the Biometric Device "Browse Employees On Device" feature:
- get_device_users endpoint (fetch + annotate device users)
- map_device_employees endpoint (persist attendance_device_id mapping)
- shift_type handling in both endpoints (ZK Shift Assignment updates)
Run with: bench run-tests --app zkteco_attendance
"""

import json
import unittest
from unittest.mock import patch, MagicMock, call

import frappe


class TestGetDeviceUsersEndpoint(unittest.TestCase):
    """get_device_users annotates device users with their mapped Employee."""

    @patch("zkteco_attendance.zkteco_attendance.zk_client.get_zk_connection")
    @patch("frappe.get_all")
    def test_annotates_users_with_mapped_employee(self, mock_get_all, mock_conn_fn):
        from zkteco_attendance.zkteco_attendance.api.endpoints import get_device_users

        mock_conn = MagicMock()
        user = MagicMock(uid=1, user_id="100", name="Abebe Kebede", privilege=0)
        mock_conn.get_users.return_value = [user]
        mock_conn_fn.return_value = (mock_conn, MagicMock())

        # frappe.get_all returns frappe._dict rows (attribute access).
        mock_get_all.return_value = [
            frappe._dict({"name": "HR-EMP-00001", "employee_name": "Abebe Kebede",
                          "attendance_device_id": "100"})
        ]

        with patch("frappe.get_doc", return_value=MagicMock()):
            result = get_device_users("Test-ZK-Device")

        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 1)
        user_row = result["users"][0]
        self.assertEqual(user_row["user_id"], "100")
        self.assertEqual(user_row["employee"], "HR-EMP-00001")
        self.assertEqual(user_row["employee_name"], "Abebe Kebede")

    @patch("zkteco_attendance.zkteco_attendance.zk_client.get_zk_connection")
    @patch("frappe.get_all")
    def test_unmapped_users_have_empty_employee(self, mock_get_all, mock_conn_fn):
        from zkteco_attendance.zkteco_attendance.api.endpoints import get_device_users

        mock_conn = MagicMock()
        mock_conn.get_users.return_value = [
            MagicMock(uid=2, user_id="200", name="Unmapped User", privilege=0)
        ]
        mock_conn_fn.return_value = (mock_conn, MagicMock())
        mock_get_all.return_value = []

        with patch("frappe.get_doc", return_value=MagicMock()):
            result = get_device_users("Test-ZK-Device")

        self.assertEqual(result["users"][0]["employee"], "")
        self.assertEqual(result["users"][0]["employee_name"], "")
        self.assertEqual(result["users"][0]["shift_type"], "")

    @patch("zkteco_attendance.zkteco_attendance.zk_client.get_zk_connection")
    @patch("zkteco_attendance.zkteco_attendance.api.endpoints._get_active_shift_types")
    @patch("frappe.get_all")
    def test_mapped_user_annotated_with_current_shift_type(
            self, mock_get_all, mock_shifts, mock_conn_fn):
        from zkteco_attendance.zkteco_attendance.api.endpoints import get_device_users

        mock_conn = MagicMock()
        mock_conn.get_users.return_value = [
            MagicMock(uid=1, user_id="100", name="Abebe Kebede", privilege=0)
        ]
        mock_conn_fn.return_value = (mock_conn, MagicMock())

        mock_get_all.return_value = [
            frappe._dict({"name": "HR-EMP-00001", "employee_name": "Abebe Kebede",
                          "attendance_device_id": "100"})
        ]
        mock_shifts.return_value = {
            "HR-EMP-00001": {"assignment": "ZK-SA-0001", "shift_type": "Morning"},
        }

        with patch("frappe.get_doc", return_value=MagicMock()):
            result = get_device_users("Test-ZK-Device")

        self.assertEqual(result["users"][0]["shift_type"], "Morning")


class TestMapDeviceEmployeesEndpoint(unittest.TestCase):
    """map_device_employees persists / clears device-to-employee mappings."""

    def _device(self, name="Test-ZK-Device", company="Acme"):
        device = MagicMock()
        device.name = name
        device.company = company
        return device

    def test_rejects_non_list_mappings(self):
        from zkteco_attendance.zkteco_attendance.api.endpoints import map_device_employees

        with patch("frappe.get_doc", return_value=self._device()), \
                self.assertRaises(frappe.ValidationError):
            map_device_employees("Test-ZK-Device", mappings="not-a-list")

    def test_rejects_invalid_json(self):
        from zkteco_attendance.zkteco_attendance.api.endpoints import map_device_employees

        with patch("frappe.get_doc", return_value=self._device()), \
                self.assertRaises(frappe.ValidationError):
            map_device_employees("Test-ZK-Device", mappings="{bad json")

    def test_rejects_mapping_without_user_id(self):
        from zkteco_attendance.zkteco_attendance.api.endpoints import map_device_employees

        with patch("frappe.get_doc", return_value=self._device()), \
                self.assertRaises(frappe.ValidationError):
            map_device_employees("Test-ZK-Device", mappings=[{"employee": "HR-EMP-00001"}])

    def test_rejects_duplicate_user_ids(self):
        from zkteco_attendance.zkteco_attendance.api.endpoints import map_device_employees

        mappings = [
            {"user_id": "100", "employee": "HR-EMP-00001"},
            {"user_id": "100", "employee": "HR-EMP-00002"},
        ]
        with patch("frappe.get_doc", return_value=self._device()), \
                patch("frappe.db.exists", return_value=True), \
                patch("frappe.db.get_value", return_value="Acme"), \
                self.assertRaises(frappe.ValidationError):
            map_device_employees("Test-ZK-Device", mappings=mappings)

    def test_rejects_nonexistent_employee(self):
        from zkteco_attendance.zkteco_attendance.api.endpoints import map_device_employees

        with patch("frappe.get_doc", return_value=self._device()), \
                patch("frappe.db.exists", return_value=False), \
                self.assertRaises(frappe.ValidationError):
            map_device_employees(
                "Test-ZK-Device", mappings=[{"user_id": "100", "employee": "HR-EMP-404"}])

    def test_rejects_cross_company_employee(self):
        from zkteco_attendance.zkteco_attendance.api.endpoints import map_device_employees

        with patch("frappe.get_doc", return_value=self._device(company="Acme")), \
                patch("frappe.db.exists", return_value=True), \
                patch("frappe.db.get_value", return_value="Other Co"), \
                self.assertRaises(frappe.ValidationError):
            map_device_employees(
                "Test-ZK-Device", mappings=[{"user_id": "100", "employee": "HR-EMP-00001"}])

    @patch("zkteco_attendance.zkteco_attendance.api.endpoints.frappe.db.set_value")
    def test_sets_device_id_and_device_on_employee(self, mock_set_value):
        from zkteco_attendance.zkteco_attendance.api.endpoints import map_device_employees

        mappings = [{"user_id": "100", "employee": "HR-EMP-00001"}]
        with patch("frappe.get_doc", return_value=self._device()), \
                patch("frappe.db.exists", return_value=True), \
                patch("frappe.db.get_value", return_value="Acme"), \
                patch("frappe.get_all", return_value=[]), \
                patch("frappe.db.commit") as mock_commit:
            result = map_device_employees("Test-ZK-Device", mappings=mappings)

        self.assertTrue(result["success"])
        self.assertEqual(result["mapped"], 1)
        self.assertEqual(result["unmapped"], 0)

        args, kwargs = mock_set_value.call_args
        self.assertEqual(args[0], "Employee")
        self.assertEqual(args[1], "HR-EMP-00001")
        self.assertEqual(args[2], {
            "attendance_device_id": "100",
            "zk_biometric_device": "Test-ZK-Device",
        })
        mock_commit.assert_called_once()

    @patch("zkteco_attendance.zkteco_attendance.api.endpoints.frappe.db.set_value")
    def test_empty_employee_clears_mapping(self, mock_set_value):
        from zkteco_attendance.zkteco_attendance.api.endpoints import map_device_employees

        mappings = [{"user_id": "100", "employee": ""}]
        with patch("frappe.get_doc", return_value=self._device()), \
                patch("frappe.get_all", return_value=[]), \
                patch("frappe.db.commit"):
            result = map_device_employees("Test-ZK-Device", mappings=mappings)

        self.assertTrue(result["success"])
        self.assertEqual(result["mapped"], 0)
        self.assertEqual(result["unmapped"], 1)
        mock_set_value.assert_not_called()

    @patch("zkteco_attendance.zkteco_attendance.api.endpoints.frappe.db.set_value")
    def test_stale_mapping_is_cleared(self, mock_set_value):
        """
        An Employee previously mapped to a device user_id that is no longer
        mapped in the submitted payload gets its attendance_device_id cleared.
        """
        from zkteco_attendance.zkteco_attendance.api.endpoints import map_device_employees

        mappings = [{"user_id": "100", "employee": "HR-EMP-00001"}]
        with patch("frappe.get_doc", return_value=self._device()), \
                patch("frappe.db.exists", return_value=True), \
                patch("frappe.db.get_value", return_value="Acme"), \
                patch("frappe.get_all",
                      return_value=[frappe._dict({"name": "HR-EMP-00009"})]), \
                patch("frappe.db.commit"):
            result = map_device_employees("Test-ZK-Device", mappings=mappings)

        self.assertTrue(result["success"])
        # One stale-clear (None) and one mapping (dict) write
        calls = mock_set_value.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].args[1], "HR-EMP-00009")
        # set_value(doctype, name, key, value) — the stale clear writes None
        self.assertEqual(calls[0].args[2], "attendance_device_id")
        self.assertIsNone(calls[0].args[3])
        self.assertEqual(calls[1].args[1], "HR-EMP-00001")
        self.assertEqual(calls[1].args[2]["attendance_device_id"], "100")

    def test_accepts_json_string_payload(self):
        from zkteco_attendance.zkteco_attendance.api.endpoints import map_device_employees

        mappings = json.dumps([{"user_id": "100", "employee": "HR-EMP-00001"}])
        with patch("frappe.get_doc", return_value=self._device()), \
                patch("frappe.db.exists", return_value=True), \
                patch("frappe.db.get_value", return_value="Acme"), \
                patch("frappe.get_all", return_value=[]), \
                patch("frappe.db.set_value"), \
                patch("frappe.db.commit"):
            result = map_device_employees("Test-ZK-Device", mappings=mappings)

        self.assertTrue(result["success"])
        self.assertEqual(result["mapped"], 1)


class TestMapDeviceEmployeesShifts(unittest.TestCase):
    """shift_type in mappings drives ZK Shift Assignment changes."""

    def _device(self, name="Test-ZK-Device", company="Acme"):
        device = MagicMock()
        device.name = name
        device.company = company
        return device

    @patch("zkteco_attendance.zkteco_attendance.api.endpoints._apply_shift_assignment")
    def test_shift_type_passed_to_apply(self, mock_apply):
        from zkteco_attendance.zkteco_attendance.api.endpoints import map_device_employees

        mappings = [{"user_id": "100", "employee": "HR-EMP-00001",
                     "shift_type": "Morning"}]

        with patch("frappe.get_doc", return_value=self._device()), \
                patch("frappe.db.exists", return_value=True), \
                patch("frappe.db.get_value", return_value="Acme"), \
                patch("frappe.get_all", return_value=[]), \
                patch("frappe.db.set_value"), \
                patch("frappe.db.commit"):
            result = map_device_employees("Test-ZK-Device", mappings=mappings)

        mock_apply.assert_called_once_with("HR-EMP-00001", "Morning", "Acme")
        self.assertEqual(result["shifts_assigned"], 1)

    @patch("zkteco_attendance.zkteco_attendance.api.endpoints._apply_shift_assignment",
           return_value=False)
    def test_unchanged_shift_not_counted(self, mock_apply):
        from zkteco_attendance.zkteco_attendance.api.endpoints import map_device_employees

        mappings = [{"user_id": "100", "employee": "HR-EMP-00001",
                     "shift_type": "Morning"}]
        with patch("frappe.get_doc", return_value=self._device()), \
                patch("frappe.db.exists", return_value=True), \
                patch("frappe.db.get_value", return_value="Acme"), \
                patch("frappe.get_all", return_value=[]), \
                patch("frappe.db.set_value"), \
                patch("frappe.db.commit"):
            result = map_device_employees("Test-ZK-Device", mappings=mappings)

        self.assertEqual(result["shifts_assigned"], 0)

    def test_unknown_shift_type_rejected(self):
        from zkteco_attendance.zkteco_attendance.api.endpoints import map_device_employees

        mappings = [{"user_id": "100", "employee": "HR-EMP-00001",
                     "shift_type": "Ghost Shift"}]
        with patch("frappe.get_doc", return_value=self._device()), \
                patch("frappe.db.exists") as mock_exists, \
                patch("frappe.db.get_value", return_value="Acme"), \
                patch("frappe.get_all", return_value=[]), \
                patch("frappe.db.set_value"), \
                patch("frappe.db.commit"), \
                self.assertRaises(frappe.ValidationError):
            # Employee exists, but the ZK Shift Type does not.
            mock_exists.side_effect = lambda doctype, name=None: doctype == "Employee"
            map_device_employees("Test-ZK-Device", mappings=mappings)

        # The validation failed on the unknown ZK Shift Type.
        self.assertIn(call("ZK Shift Type", "Ghost Shift"), mock_exists.call_args_list)

    @patch("zkteco_attendance.zkteco_attendance.api.endpoints._apply_shift_assignment")
    def test_empty_shift_still_passed_for_unassign(self, mock_apply):
        """An explicit empty shift_type key unassigns the employee."""
        from zkteco_attendance.zkteco_attendance.api.endpoints import map_device_employees

        mappings = [{"user_id": "100", "employee": "HR-EMP-00001", "shift_type": ""}]
        with patch("frappe.get_doc", return_value=self._device()), \
                patch("frappe.db.exists", return_value=True), \
                patch("frappe.db.get_value", return_value="Acme"), \
                patch("frappe.get_all", return_value=[]), \
                patch("frappe.db.set_value"), \
                patch("frappe.db.commit"):
            result = map_device_employees("Test-ZK-Device", mappings=mappings)

        mock_apply.assert_called_once_with("HR-EMP-00001", "", "Acme")
        self.assertEqual(result["shifts_assigned"], 1)


if __name__ == "__main__":
    unittest.main()
