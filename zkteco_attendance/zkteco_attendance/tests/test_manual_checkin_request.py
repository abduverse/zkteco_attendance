"""
Unit tests for the Manual Checkin Request doctype.

A Manual Checkin Request is a submittable document: creating one never
touches the Employee Checkin table — the check-in is only created or
updated when the request is submitted (on_submit delegates to the
attendance processor's save_manual_checkin_record).

Run with: bench run-tests --app zkteco_attendance
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe


def _request_dict(**overrides):
    data = {
        "doctype": "Manual Checkin Request",
        "employee": "HR-EMP-00001",
        "employee_name": "Test Employee",
        "request_type": "New",
        "checkin_date": "2026-08-10",
        "checkin_time": "08:00:00",
        "log_type": "IN",
        "is_overtime": 0,
    }
    data.update(overrides)
    return data


class TestManualCheckinRequestOnSubmit(unittest.TestCase):
    """Submitting the request must create/update the Employee Checkin."""

    @patch("frappe.db.set_value")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.save_manual_checkin_record")
    def test_on_submit_creates_new_checkin(self, mock_save, mock_set_value):
        """A request without an existing checkin creates a new one."""
        mock_save.return_value = {"name": "CHK-NEW-001", "action": "created"}

        doc = frappe.get_doc(_request_dict())
        doc.on_submit()

        mock_save.assert_called_once_with(
            employee="HR-EMP-00001",
            checkin_time="2026-08-10 08:00:00",
            log_type="IN",
            checkin_name=None,
            is_overtime=0,
            remark=None,
        )
        args = mock_set_value.call_args[0]
        self.assertEqual(args[0], "Manual Checkin Request")
        self.assertEqual(args[2], "applied_checkin")
        self.assertEqual(args[3], "CHK-NEW-001")

    @patch("frappe.db.set_value")
    @patch("zkteco_attendance.zkteco_attendance.doctype.manual_checkin_request.manual_checkin_request.frappe.db.exists",
           return_value=True)
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.save_manual_checkin_record")
    def test_on_submit_updates_existing_checkin(self, mock_save, mock_exists, mock_set_value):
        """A request referencing an existing checkin updates it in place."""
        mock_save.return_value = {"name": "CHK-OLD-001", "action": "updated"}

        doc = frappe.get_doc(_request_dict(
            request_type="Edit",
            checkin_name="CHK-OLD-001",
            log_type="OUT",
            checkin_time="17:00:00",
            is_overtime=1,
        ))
        doc.on_submit()

        mock_save.assert_called_once_with(
            employee="HR-EMP-00001",
            checkin_time="2026-08-10 17:00:00",
            log_type="OUT",
            checkin_name="CHK-OLD-001",
            is_overtime=1,
            remark=None,
        )

    @patch("frappe.db.set_value")
    @patch("zkteco_attendance.zkteco_attendance.doctype.manual_checkin_request.manual_checkin_request.frappe.db.exists",
           return_value=False)
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.save_manual_checkin_record")
    def test_on_submit_passes_remarks_to_checkin(self, mock_save, mock_exists, mock_set_value):
        """request_remarks must be forwarded as the checkin's remark."""
        mock_save.return_value = {"name": "CHK-NEW-003", "action": "created"}

        doc = frappe.get_doc(_request_dict(request_remarks="Forgotten punch"))
        doc.on_submit()

        self.assertEqual(mock_save.call_args[1]["remark"], "Forgotten punch")

    @patch("frappe.db.set_value")
    @patch("zkteco_attendance.zkteco_attendance.doctype.manual_checkin_request.manual_checkin_request.frappe.db.exists",
           return_value=False)
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.save_manual_checkin_record")
    def test_on_submit_stale_checkin_falls_back_to_create(self, mock_save, mock_exists, mock_set_value):
        """If the referenced checkin no longer exists, a new one is created."""
        mock_save.return_value = {"name": "CHK-NEW-002", "action": "created"}

        doc = frappe.get_doc(_request_dict(checkin_name="CHK-GONE-001"))
        doc.on_submit()

        self.assertEqual(mock_save.call_args[1]["checkin_name"], None)


class TestManualCheckinRequestValidate(unittest.TestCase):
    """Validation ties the request to its Attendance Summary."""

    def test_validate_rejects_employee_not_in_summary(self):
        doc = frappe.get_doc(_request_dict(attendance_summary="ATT-SUM-2026-00001"))
        summary = MagicMock()
        summary.details = [MagicMock(employee="HR-EMP-00002")]
        with patch("frappe.get_doc", return_value=summary):
            with self.assertRaises(frappe.ValidationError):
                doc.validate()

    def test_validate_accepts_employee_in_summary(self):
        doc = frappe.get_doc(_request_dict(attendance_summary="ATT-SUM-2026-00001"))
        summary = MagicMock()
        summary.details = [MagicMock(employee="HR-EMP-00001"), MagicMock(employee="HR-EMP-00002")]
        with patch("frappe.get_doc", return_value=summary), \
                patch("frappe.db.get_value", return_value="Acme"):
            doc.validate()
        self.assertEqual(doc.company, "Acme")


class TestCreateManualCheckinRequestEndpoint(unittest.TestCase):
    """The whitelisted endpoint creates a Draft request — never a checkin."""

    @patch("frappe.db.commit")
    @patch("frappe.get_doc")
    def test_endpoint_creates_draft_request(self, mock_get_doc, mock_commit):
        from zkteco_attendance.zkteco_attendance.api.endpoints import create_manual_checkin_request

        fake_doc = MagicMock()
        fake_doc.name = "MAN-CHK-2026-00001"
        mock_get_doc.return_value = fake_doc

        result = create_manual_checkin_request(
            employee="HR-EMP-00001",
            checkin_date="2026-08-10",
            checkin_time="08:00:00",
            log_type="IN",
            is_overtime=1,
            attendance_summary="ATT-SUM-2026-00001",
        )

        self.assertEqual(result, {"name": "MAN-CHK-2026-00001", "action": "created", "status": "Draft"})
        inserted = mock_get_doc.call_args[0][0]
        self.assertEqual(inserted["doctype"], "Manual Checkin Request")
        self.assertEqual(inserted["employee"], "HR-EMP-00001")
        self.assertEqual(inserted["checkin_date"], "2026-08-10")
        self.assertEqual(inserted["checkin_time"], "08:00:00")
        self.assertEqual(inserted["log_type"], "IN")
        self.assertEqual(inserted["is_overtime"], 1)
        self.assertEqual(inserted["attendance_summary"], "ATT-SUM-2026-00001")
        self.assertEqual(inserted["checkin_name"], None)
        self.assertEqual(inserted["request_remarks"], None)
        fake_doc.insert.assert_called_once_with(ignore_permissions=True)

    @patch("frappe.db.commit")
    @patch("frappe.get_doc")
    def test_endpoint_stores_remarks(self, mock_get_doc, mock_commit):
        from zkteco_attendance.zkteco_attendance.api.endpoints import create_manual_checkin_request

        fake_doc = MagicMock()
        fake_doc.name = "MAN-CHK-2026-00002"
        mock_get_doc.return_value = fake_doc

        create_manual_checkin_request(
            employee="HR-EMP-00001",
            checkin_date="2026-08-10",
            checkin_time="08:00:00",
            log_type="IN",
            remarks="Gate was locked",
        )

        inserted = mock_get_doc.call_args[0][0]
        self.assertEqual(inserted["request_remarks"], "Gate was locked")

    def test_endpoint_requires_employee_date_time(self):
        from zkteco_attendance.zkteco_attendance.api.endpoints import create_manual_checkin_request

        with self.assertRaises(frappe.ValidationError):
            create_manual_checkin_request(
                employee="HR-EMP-00001", checkin_date=None, checkin_time=None, log_type="IN")

    def test_endpoint_rejects_bad_log_type(self):
        from zkteco_attendance.zkteco_attendance.api.endpoints import create_manual_checkin_request

        with self.assertRaises(frappe.ValidationError):
            create_manual_checkin_request(
                employee="HR-EMP-00001", checkin_date="2026-08-10",
                checkin_time="08:00:00", log_type="FOO")


if __name__ == "__main__":
    unittest.main()