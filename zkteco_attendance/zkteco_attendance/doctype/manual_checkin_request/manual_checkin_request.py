"""
Manual Checkin Request DocType Controller.

A submittable request that applies a manual Employee Checkin (create or
update) when the document is submitted. Requests are created from the
Daily Checkins page (Add / Edit buttons) and the Attendance Summary
"Add Check-in" button; the check-in itself only happens on submit, so
requests can be reviewed before they take effect.
"""
import json

import frappe
from frappe import _
from frappe.model.document import Document

# Employee Checkin fields captured before an "Edit" request is applied, so
# cancelling the request can restore the original values. Columns that may
# not exist (pre-patch databases) are skipped via has_column.
SNAPSHOT_FIELDS = ("is_overtime", "zk_remark", "manually_edited", "edited_by", "edited_at")


class ManualCheckinRequest(Document):

    def validate(self):
        # Request Type is required: "New" adds a check-in, "Edit" modifies
        # the existing check-in referenced by `checkin_name`.
        if not self.request_type:
            self.request_type = "New"
        if self.request_type not in ("New", "Edit"):
            frappe.throw(_("Request Type must be New or Edit."))

        if self.request_type == "Edit" and not self.checkin_name:
            frappe.throw(_("An Existing Check-in must be set when Request Type is Edit."))

        # Keep the request tied to a real summary: the employee must be part
        # of it (mirrors AttendanceSummary.save_manual_checkin).
        if self.attendance_summary:
            summary = frappe.get_doc("Attendance Summary", self.attendance_summary)
            employee_names = [row.employee for row in summary.details]
            if self.employee not in employee_names:
                frappe.throw(_(
                    "Employee {0} is not part of Attendance Summary {1}."
                ).format(self.employee, self.attendance_summary))

        if not self.company:
            if self.attendance_summary:
                self.company = frappe.db.get_value(
                    "Attendance Summary", self.attendance_summary, "company")
            if not self.company:
                self.company = frappe.db.get_value("Employee", self.employee, "company")

    def on_submit(self):
        """Apply the request: create or update the Employee Checkin."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import save_manual_checkin_record

        checkin_time = "{0} {1}".format(self.checkin_date, self.checkin_time)

        # Only update the referenced check-in if it still exists
        existing_name = None
        if self.request_type == "Edit" and self.checkin_name \
                and frappe.db.exists("Employee Checkin", self.checkin_name):
            existing_name = self.checkin_name

        # Capture the original values before they are overwritten, so that
        # cancelling this request can restore the check-in exactly as it was.
        snapshot = None
        if existing_name:
            snapshot = self._snapshot_original_checkin(existing_name)

        result = save_manual_checkin_record(
            employee=self.employee,
            checkin_time=checkin_time,
            log_type=self.log_type,
            checkin_name=existing_name,
            is_overtime=self.is_overtime,
            remark=self.request_remarks,
        )

        self.db_set("applied_checkin", result["name"], update_modified=False)
        if snapshot:
            self.db_set("original_checkin_data", json.dumps(snapshot), update_modified=False)

    def _snapshot_original_checkin(self, checkin_name):
        """Return the current values of the check-in being edited.

        Returns a plain dict (JSON-safe) or None if the row cannot be read.
        """
        from zkteco_attendance.zkteco_attendance.utils import has_column

        fields = ["time", "log_type"] + [
            f for f in SNAPSHOT_FIELDS if has_column("Employee Checkin", f)
        ]
        values = frappe.db.get_value(
            "Employee Checkin", checkin_name, fields, as_dict=True)
        if not values:
            return None

        snapshot = {}
        for field in fields:
            value = values.get(field)
            if value is None:
                snapshot[field] = None
            else:
                # Datetimes / times are not JSON serializable; store strings.
                snapshot[field] = str(value)
        return snapshot

    def on_cancel(self):
        """Revert the check-in applied by this request.

        - "Edit" requests: restore the original values captured on submit.
        - "New" requests (and "Edit" requests whose target check-in had
          disappeared at submit time): the created Employee Checkin is
          deleted.
        """
        if not self.applied_checkin:
            return

        if not frappe.db.exists("Employee Checkin", self.applied_checkin):
            return

        if self.original_checkin_data:
            self._restore_original_checkin()
        else:
            # Nothing to restore: the applied check-in was created by this
            # request, so remove it entirely.
            frappe.delete_doc(
                "Employee Checkin", self.applied_checkin,
                ignore_permissions=True, force=1)

        frappe.db.commit()

    def _restore_original_checkin(self):
        """Write the snapshotted pre-edit values back onto the check-in."""
        from zkteco_attendance.zkteco_attendance.utils import has_column

        try:
            snapshot = json.loads(self.original_checkin_data)
        except (TypeError, ValueError):
            return

        if not isinstance(snapshot, dict):
            return

        for field, value in snapshot.items():
            if field not in ("time", "log_type") and not has_column("Employee Checkin", field):
                continue
            frappe.db.set_value("Employee Checkin", self.applied_checkin, field, value)