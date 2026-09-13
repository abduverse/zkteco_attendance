"""
Manual Checkin Request DocType Controller.

A submittable request that applies a manual Employee Checkin (create or
update) when the document is submitted. Requests are created from the
Daily Checkins page (Add / Edit buttons) and the Attendance Summary
"Add Check-in" button; the check-in itself only happens on submit, so
requests can be reviewed before they take effect.
"""
import frappe
from frappe import _
from frappe.model.document import Document


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

        result = save_manual_checkin_record(
            employee=self.employee,
            checkin_time=checkin_time,
            log_type=self.log_type,
            checkin_name=existing_name,
            is_overtime=self.is_overtime,
            remark=self.request_remarks,
        )

        self.db_set("applied_checkin", result["name"], update_modified=False)