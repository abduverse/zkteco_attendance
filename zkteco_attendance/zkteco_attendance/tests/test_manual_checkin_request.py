"""
Unit tests for the Manual Checkin Request doctype.

A Manual Checkin Request is a submittable document: creating one never
touches the Employee Checkin table — the check-in is only created or
updated when the request is submitted (on_submit delegates to the
attendance processor's save_manual_checkin_record).

Cancelling a submitted request reverts the applied check-in: "Edit"
requests restore the original values snapshotted on submit, while "New"
requests delete the check-in they created.

"Make Present" requests create BOTH an IN and an OUT check-in for a day
that has no check-ins at all (validation throws otherwise). The dialog
sends Start Time / End Time prefilled from the employee's shift; the IN
check-in is created at Start Time and the OUT at End Time (a night-shift
End Time rolls into the next day). When no requested times were saved
(older requests), the times fall back to the employee's shift, then to
the entered time + 8h.

Run with: bench run-tests --app zkteco_attendance
"""

import json
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
    @patch("zkteco_attendance.zkteco_attendance.utils.has_column", return_value=False)
    @patch("zkteco_attendance.zkteco_attendance.doctype.manual_checkin_request.manual_checkin_request.frappe.db.exists",
           return_value=True)
    @patch("zkteco_attendance.zkteco_attendance.doctype.manual_checkin_request.manual_checkin_request.frappe.db.get_value")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.save_manual_checkin_record")
    def test_on_submit_updates_existing_checkin(self, mock_save, mock_get_value,
                                                mock_exists, mock_has_column, mock_set_value):
        """A request referencing an existing checkin updates it in place."""
        mock_save.return_value = {"name": "CHK-OLD-001", "action": "updated"}
        # Original values read from the check-in before it is overwritten
        mock_get_value.return_value = {"time": "2026-08-10 09:00:00", "log_type": "IN"}

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

        # The original values must be snapshotted for cancel-revert
        snapshot = json.loads(doc.original_checkin_data)
        self.assertEqual(snapshot["time"], "2026-08-10 09:00:00")
        self.assertEqual(snapshot["log_type"], "IN")

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


class TestManualCheckinRequestOnCancel(unittest.TestCase):
    """Cancelling the request must revert the applied Employee Checkin."""

    def _cancelled_doc(self, request_type="New", applied_checkin="CHK-NEW-001",
                       original_checkin_data=None):
        doc = frappe.get_doc(_request_dict(request_type=request_type))
        doc.applied_checkin = applied_checkin
        if original_checkin_data is not None:
            doc.original_checkin_data = json.dumps(original_checkin_data)
        return doc

    @patch("frappe.db.commit")
    @patch("zkteco_attendance.zkteco_attendance.doctype.manual_checkin_request.manual_checkin_request.frappe.delete_doc")
    @patch("zkteco_attendance.zkteco_attendance.doctype.manual_checkin_request.manual_checkin_request.frappe.db.exists",
           return_value=True)
    def test_on_cancel_deletes_created_checkin(self, mock_exists, mock_delete, mock_commit):
        """Cancelling a "New" request deletes the check-in it created."""
        doc = self._cancelled_doc(request_type="New")
        doc.on_cancel()

        mock_delete.assert_called_once_with(
            "Employee Checkin", "CHK-NEW-001",
            ignore_permissions=True, force=1)

    @patch("frappe.db.commit")
    @patch("frappe.db.set_value")
    @patch("zkteco_attendance.zkteco_attendance.utils.has_column", return_value=False)
    @patch("zkteco_attendance.zkteco_attendance.doctype.manual_checkin_request.manual_checkin_request.frappe.db.exists",
           return_value=True)
    def test_on_cancel_restores_original_values(self, mock_exists, mock_has_column,
                                                mock_set_value, mock_commit):
        """Cancelling an "Edit" request restores the snapshotted values."""
        original = {"time": "2026-08-10 09:00:00", "log_type": "IN"}
        doc = self._cancelled_doc(
            request_type="Edit", applied_checkin="CHK-OLD-001",
            original_checkin_data=original)
        doc.on_cancel()

        calls = {call[0][2]: call[0][3] for call in mock_set_value.call_args_list}
        self.assertEqual(calls.get("time"), "2026-08-10 09:00:00")
        self.assertEqual(calls.get("log_type"), "IN")
        self.assertNotIn("manually_edited", calls)  # column absent -> skipped

    @patch("frappe.db.commit")
    @patch("frappe.db.set_value")
    @patch("zkteco_attendance.zkteco_attendance.utils.has_column", return_value=True)
    @patch("zkteco_attendance.zkteco_attendance.doctype.manual_checkin_request.manual_checkin_request.frappe.db.exists",
           return_value=True)
    def test_on_cancel_restores_optional_columns(self, mock_exists, mock_has_column,
                                                 mock_set_value, mock_commit):
        """Optional columns present in the snapshot are restored when they exist."""
        original = {"time": "2026-08-10 09:00:00", "log_type": "IN",
                    "is_overtime": 0, "zk_remark": "Original remark",
                    "manually_edited": 1, "edited_by": "user@example.com",
                    "edited_at": "2026-08-10 10:00:00"}
        doc = self._cancelled_doc(
            request_type="Edit", applied_checkin="CHK-OLD-001",
            original_checkin_data=original)
        doc.on_cancel()

        calls = {call[0][2]: call[0][3] for call in mock_set_value.call_args_list}
        self.assertEqual(calls.get("zk_remark"), "Original remark")
        self.assertEqual(calls.get("edited_by"), "user@example.com")
        self.assertEqual(calls.get("manually_edited"), 1)

    @patch("frappe.db.commit")
    @patch("zkteco_attendance.zkteco_attendance.doctype.manual_checkin_request.manual_checkin_request.frappe.delete_doc")
    @patch("zkteco_attendance.zkteco_attendance.doctype.manual_checkin_request.manual_checkin_request.frappe.db.exists",
           return_value=True)
    def test_on_cancel_without_snapshot_deletes(self, mock_exists, mock_delete, mock_commit):
        """An "Edit" request with no snapshot falls back to deleting the checkin."""
        doc = self._cancelled_doc(request_type="Edit", applied_checkin="CHK-OLD-001")
        doc.on_cancel()

        mock_delete.assert_called_once_with(
            "Employee Checkin", "CHK-OLD-001",
            ignore_permissions=True, force=1)

    @patch("frappe.db.commit")
    @patch("zkteco_attendance.zkteco_attendance.doctype.manual_checkin_request.manual_checkin_request.frappe.delete_doc")
    @patch("zkteco_attendance.zkteco_attendance.doctype.manual_checkin_request.manual_checkin_request.frappe.db.exists",
           return_value=False)
    def test_on_cancel_without_applied_checkin_is_noop(self, mock_exists, mock_delete, mock_commit):
        """Cancelling a request with no applied check-in does nothing."""
        doc = self._cancelled_doc(applied_checkin=None)
        doc.on_cancel()

        mock_delete.assert_not_called()

    @patch("frappe.db.commit")
    @patch("zkteco_attendance.zkteco_attendance.doctype.manual_checkin_request.manual_checkin_request.frappe.delete_doc")
    @patch("zkteco_attendance.zkteco_attendance.doctype.manual_checkin_request.manual_checkin_request.frappe.db.exists",
           return_value=False)
    def test_on_cancel_missing_checkin_is_noop(self, mock_exists, mock_delete, mock_commit):
        """Cancelling when the applied check-in was already deleted does nothing."""
        doc = self._cancelled_doc(applied_checkin="CHK-GONE-001")
        doc.on_cancel()

        mock_delete.assert_not_called()

    @patch("zkteco_attendance.zkteco_attendance.doctype.manual_checkin_request.manual_checkin_request.frappe.delete_doc")
    @patch("frappe.db.commit")
    @patch("frappe.db.set_value")
    @patch("zkteco_attendance.zkteco_attendance.utils.has_column", return_value=False)
    @patch("zkteco_attendance.zkteco_attendance.doctype.manual_checkin_request.manual_checkin_request.frappe.db.exists",
           return_value=True)
    def test_on_cancel_corrupt_snapshot_does_nothing(self, mock_exists, mock_has_column,
                                                     mock_set_value, mock_commit, mock_delete):
        """A corrupt snapshot string reverts nothing and deletes nothing."""
        doc = self._cancelled_doc(request_type="Edit", applied_checkin="CHK-OLD-001")
        doc.original_checkin_data = "not-json{{"
        doc.on_cancel()

        mock_set_value.assert_not_called()
        mock_delete.assert_not_called()


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

    @patch("frappe.db.commit")
    @patch("frappe.get_doc")
    def test_endpoint_stores_request_type_edit(self, mock_get_doc, mock_commit):
        from zkteco_attendance.zkteco_attendance.api.endpoints import create_manual_checkin_request

        fake_doc = MagicMock()
        fake_doc.name = "MAN-CHK-2026-00003"
        mock_get_doc.return_value = fake_doc

        create_manual_checkin_request(
            employee="HR-EMP-00001",
            checkin_date="2026-08-10",
            checkin_time="08:00:00",
            log_type="IN",
            request_type="Edit",
            checkin_name="CHK-00001",
        )

        inserted = mock_get_doc.call_args[0][0]
        self.assertEqual(inserted["request_type"], "Edit")
        self.assertEqual(inserted["checkin_name"], "CHK-00001")

    @patch("frappe.db.commit")
    @patch("frappe.get_doc")
    def test_endpoint_defaults_missing_request_type_to_new(self, mock_get_doc, mock_commit):
        """Callers that omit request_type (e.g. the Attendance Summary dialog)
        must still get a "New" request."""
        from zkteco_attendance.zkteco_attendance.api.endpoints import create_manual_checkin_request

        fake_doc = MagicMock()
        fake_doc.name = "MAN-CHK-2026-00004"
        mock_get_doc.return_value = fake_doc

        create_manual_checkin_request(
            employee="HR-EMP-00001",
            checkin_date="2026-08-10",
            checkin_time="08:00:00",
            log_type="IN",
            request_type=None,
        )

        inserted = mock_get_doc.call_args[0][0]
        self.assertEqual(inserted["request_type"], "New")

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



class TestManualCheckinRequestMakePresent(unittest.TestCase):
    """"Make Present" creates IN + OUT checkins for a day with none."""

    def _mp_doc(self, **overrides):
        data = _request_dict(request_type="Make Present", log_type=None, is_overtime=0)
        data.update(overrides)
        return frappe.get_doc(data)

    def _shift(self, **overrides):
        shift = frappe._dict({
            "name": "Morning", "start_time": "08:00:00", "end_time": "17:00:00",
            "is_night_shift": 0, "saturday_mode": "Full Day",
            "full_day_hours": 8, "half_day_hours": 4, "saturday_half_day_hours": 4,
        })
        shift.update(overrides)
        return shift

    @patch("frappe.db.get_value", return_value="Acme")
    @patch("zkteco_attendance.zkteco_attendance.doctype.manual_checkin_request.manual_checkin_request.frappe.get_all",
           return_value=[frappe._dict({"name": "CHK-001", "log_type": "IN"})])
    def test_validate_throws_when_day_has_checkins(self, mock_get_all, mock_get_value):
        """Make Present is rejected for a day that already has check-ins."""
        doc = self._mp_doc()
        with self.assertRaises(frappe.ValidationError):
            doc.validate()

    @patch("frappe.db.get_value", return_value="Acme")
    @patch("zkteco_attendance.zkteco_attendance.doctype.manual_checkin_request.manual_checkin_request.frappe.get_all",
           return_value=[])
    def test_validate_clears_log_type_when_day_is_free(self, mock_get_all, mock_get_value):
        """A free day validates and Log Type / Is Overtime are cleared."""
        doc = self._mp_doc(log_type="IN", is_overtime=1)
        doc.validate()
        self.assertIsNone(doc.log_type)
        self.assertEqual(doc.is_overtime, 0)

    @patch("frappe.db.set_value")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_shift_for_employee")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.save_manual_checkin_record")
    @patch("zkteco_attendance.zkteco_attendance.doctype.manual_checkin_request.manual_checkin_request.frappe.get_all",
           return_value=[])
    def test_on_submit_creates_in_and_out_from_shift(self, mock_get_all, mock_save,
                                                     mock_shift, mock_set_value):
        """IN at shift start and OUT at shift end when a shift is assigned."""
        mock_shift.return_value = self._shift()
        mock_save.side_effect = [{"name": "CHK-IN-001"}, {"name": "CHK-OUT-001"}]

        doc = self._mp_doc()
        doc.on_submit()

        first, second = mock_save.call_args_list
        self.assertEqual(first.kwargs["log_type"], "IN")
        self.assertEqual(first.kwargs["checkin_time"], "2026-08-10 08:00:00")
        self.assertEqual(second.kwargs["log_type"], "OUT")
        self.assertEqual(second.kwargs["checkin_time"], "2026-08-10 17:00:00")

        db_sets = {c.args[2]: c.args[3] for c in mock_set_value.call_args_list}
        self.assertEqual(db_sets.get("applied_checkin"), "CHK-IN-001")
        self.assertEqual(db_sets.get("applied_checkin_out"), "CHK-OUT-001")

    @patch("frappe.db.set_value")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_shift_for_employee")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.save_manual_checkin_record")
    @patch("zkteco_attendance.zkteco_attendance.doctype.manual_checkin_request.manual_checkin_request.frappe.get_all",
           return_value=[])
    def test_on_submit_requested_times_override_shift(self, mock_get_all, mock_save,
                                                       mock_shift, mock_set_value):
        """Requested Start/End Times win over the employee's shift times."""
        mock_shift.return_value = self._shift()
        mock_save.side_effect = [{"name": "CHK-IN-010"}, {"name": "CHK-OUT-010"}]

        doc = self._mp_doc(requested_start_time="07:15:00",
                           requested_end_time="15:45:00")
        doc.on_submit()

        first, second = mock_save.call_args_list
        self.assertEqual(first.kwargs["log_type"], "IN")
        self.assertEqual(first.kwargs["checkin_time"], "2026-08-10 07:15:00")
        self.assertEqual(second.kwargs["log_type"], "OUT")
        self.assertEqual(second.kwargs["checkin_time"], "2026-08-10 15:45:00")
        # The shift lookup is skipped entirely when times were requested.
        mock_shift.assert_not_called()

    @patch("frappe.db.set_value")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.save_manual_checkin_record")
    @patch("zkteco_attendance.zkteco_attendance.doctype.manual_checkin_request.manual_checkin_request.frappe.get_all",
           return_value=[])
    def test_on_submit_requested_night_end_rolls_to_next_day(self, mock_get_all, mock_save,
                                                              mock_set_value):
        """An End Time not later than Start Time lands on the next day."""
        mock_save.side_effect = [{"name": "CHK-IN-011"}, {"name": "CHK-OUT-011"}]

        doc = self._mp_doc(requested_start_time="22:00:00",
                           requested_end_time="06:00:00")
        doc.on_submit()

        first, second = mock_save.call_args_list
        self.assertEqual(first.kwargs["checkin_time"], "2026-08-10 22:00:00")
        self.assertEqual(second.kwargs["checkin_time"], "2026-08-11 06:00:00")

    @patch("zkteco_attendance.zkteco_attendance.doctype.manual_checkin_request.manual_checkin_request.frappe.get_all",
           return_value=[])
    @patch("frappe.db.get_value", return_value="Acme")
    def test_validate_rejects_equal_start_and_end(self, mock_get_value, mock_get_all):
        """Start Time and End Time must differ for Make Present."""
        doc = self._mp_doc(requested_start_time="08:00:00",
                           requested_end_time="08:00:00")
        with self.assertRaises(frappe.ValidationError):
            doc.validate()

    @patch("frappe.db.set_value")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_shift_for_employee")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.save_manual_checkin_record")
    @patch("zkteco_attendance.zkteco_attendance.doctype.manual_checkin_request.manual_checkin_request.frappe.get_all",
           return_value=[])
    def test_on_submit_falls_back_to_shift_without_requested_times(self, mock_get_all,
                                                                    mock_save, mock_shift,
                                                                    mock_set_value):
        """Older requests without Start/End Times keep shift-derived times."""
        mock_shift.return_value = self._shift()
        mock_save.side_effect = [{"name": "CHK-IN-012"}, {"name": "CHK-OUT-012"}]

        doc = self._mp_doc()
        doc.on_submit()

        first, second = mock_save.call_args_list
        self.assertEqual(first.kwargs["checkin_time"], "2026-08-10 08:00:00")
        self.assertEqual(second.kwargs["checkin_time"], "2026-08-10 17:00:00")

    @patch("frappe.db.set_value")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_shift_for_employee",
           return_value=None)
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.save_manual_checkin_record")
    @patch("zkteco_attendance.zkteco_attendance.doctype.manual_checkin_request.manual_checkin_request.frappe.get_all",
           return_value=[])
    def test_on_submit_no_shift_uses_entered_time_plus_8h(self, mock_get_all, mock_save,
                                                           mock_shift, mock_set_value):
        """Without a shift, OUT is the entered time + 8 standard hours."""
        mock_save.side_effect = [{"name": "CHK-IN-002"}, {"name": "CHK-OUT-002"}]

        doc = self._mp_doc(checkin_time="09:30:00")
        doc.on_submit()

        first, second = mock_save.call_args_list
        self.assertEqual(first.kwargs["checkin_time"], "2026-08-10 09:30:00")
        self.assertEqual(second.kwargs["checkin_time"], "2026-08-10 17:30:00")

    @patch("frappe.db.set_value")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.get_shift_for_employee")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.save_manual_checkin_record")
    @patch("zkteco_attendance.zkteco_attendance.doctype.manual_checkin_request.manual_checkin_request.frappe.get_all",
           return_value=[])
    def test_on_submit_night_shift_out_is_next_day(self, mock_get_all, mock_save,
                                                    mock_shift, mock_set_value):
        """A night shift's OUT check-in lands on the next calendar day."""
        mock_shift.return_value = self._shift(
            start_time="22:00:00", end_time="06:00:00", is_night_shift=1)
        mock_save.side_effect = [{"name": "CHK-IN-003"}, {"name": "CHK-OUT-003"}]

        doc = self._mp_doc()
        doc.on_submit()

        first, second = mock_save.call_args_list
        self.assertEqual(first.kwargs["checkin_time"], "2026-08-10 22:00:00")
        self.assertEqual(second.kwargs["checkin_time"], "2026-08-11 06:00:00")

    @patch("frappe.db.set_value")
    @patch("zkteco_attendance.zkteco_attendance.attendance_processor.save_manual_checkin_record")
    @patch("zkteco_attendance.zkteco_attendance.doctype.manual_checkin_request.manual_checkin_request.frappe.get_all",
           return_value=[frappe._dict({"name": "CHK-001"})])
    def test_on_submit_throws_when_day_has_checkins(self, mock_get_all, mock_save,
                                                     mock_set_value):
        """Submitting is re-validated: a day with check-ins must not change."""
        doc = self._mp_doc()
        with self.assertRaises(frappe.ValidationError):
            doc.on_submit()
        mock_save.assert_not_called()

    @patch("frappe.db.commit")
    @patch("zkteco_attendance.zkteco_attendance.doctype.manual_checkin_request.manual_checkin_request.frappe.delete_doc")
    @patch("zkteco_attendance.zkteco_attendance.doctype.manual_checkin_request.manual_checkin_request.frappe.db.exists",
           return_value=True)
    def test_on_cancel_deletes_both_checkins(self, mock_exists, mock_delete, mock_commit):
        """Cancelling a Make Present request deletes IN and OUT checkins."""
        doc = self._mp_doc()
        doc.applied_checkin = "CHK-IN-001"
        doc.applied_checkin_out = "CHK-OUT-001"
        doc.on_cancel()

        deleted = [c.args[1] for c in mock_delete.call_args_list]
        self.assertEqual(deleted, ["CHK-IN-001", "CHK-OUT-001"])


class TestMakePresentEndpoint(unittest.TestCase):
    """The endpoint accepts Make Present and neutralises log_type."""

    @patch("frappe.db.commit")
    @patch("frappe.get_doc")
    def test_endpoint_make_present_ignores_log_type(self, mock_get_doc, mock_commit):
        from zkteco_attendance.zkteco_attendance.api.endpoints import create_manual_checkin_request

        fake_doc = MagicMock()
        fake_doc.name = "MAN-CHK-2026-00010"
        mock_get_doc.return_value = fake_doc

        create_manual_checkin_request(
            employee="HR-EMP-00001",
            checkin_date="2026-08-10",
            checkin_time="08:00:00",
            log_type="IN",
            request_type="Make Present",
        )

        inserted = mock_get_doc.call_args[0][0]
        self.assertEqual(inserted["request_type"], "Make Present")
        self.assertIsNone(inserted["log_type"])
        self.assertEqual(inserted["is_overtime"], 0)

    @patch("frappe.db.commit")
    @patch("frappe.get_doc")
    def test_endpoint_make_present_stores_requested_times(self, mock_get_doc, mock_commit):
        """Start Time / End Time are persisted on the request document."""
        from zkteco_attendance.zkteco_attendance.api.endpoints import create_manual_checkin_request

        fake_doc = MagicMock()
        fake_doc.name = "MAN-CHK-2026-00011"
        mock_get_doc.return_value = fake_doc

        create_manual_checkin_request(
            employee="HR-EMP-00001",
            checkin_date="2026-08-10",
            checkin_time="08:00:00",
            log_type="IN",
            request_type="Make Present",
            start_time="07:15:00",
            end_time="15:45:00",
        )

        inserted = mock_get_doc.call_args[0][0]
        self.assertEqual(inserted["requested_start_time"], "07:15:00")
        self.assertEqual(inserted["requested_end_time"], "15:45:00")
        self.assertIsNone(inserted["log_type"])

    def test_endpoint_rejects_bad_request_type(self):
        from zkteco_attendance.zkteco_attendance.api.endpoints import create_manual_checkin_request

        with self.assertRaises(frappe.ValidationError):
            create_manual_checkin_request(
                employee="HR-EMP-00001", checkin_date="2026-08-10",
                checkin_time="08:00:00", log_type="IN", request_type="Bogus")


if __name__ == "__main__":
    unittest.main()