"""
Attendance Summary DocType Controller
"""
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, flt

from zkteco_attendance.zkteco_attendance.attendance_processor import (
    count_working_days,
    fetch_checkins,
    process_employee,
    save_manual_checkin_record,
)


class AttendanceSummary(Document):

    def validate(self):
        if self.from_date and self.to_date and getdate(self.from_date) > getdate(self.to_date):
            frappe.throw(_("From Date cannot be after To Date."))

    @frappe.whitelist()
    def fetch_employees(company=None, department=None, project=None, designation=None):
        """Fetch active employees matching optional filters for bulk-add to shift assignment."""
        filters = {"status": "Active"}
        if company:         filters["company"] = company
        if department:      filters["department"] = department
        if project:         filters["project"] = project
        if designation:     filters["designation"] = designation

        return frappe.get_all(
            "Employee",
            filters=filters,
            fields=["name as employee", "employee_name", "department", "designation"],
            order_by="employee_name asc"
        )
    

    @frappe.whitelist()
    def save_manual_checkin(self, employee, checkin_time, log_type,
                             checkin_name=None, is_overtime=0, remark=None):
        """
        Add a new Employee Checkin or update an existing one manually.
        Saves edited_by / edited_at / manually_edited flags, plus an optional remark.
        Called from the Daily Checkins dashboard (and optionally from here).
        Returns the created/updated checkin name.
        """
        if not employee or not checkin_time or not log_type:
            frappe.throw(_("Employee, Check-in Time, and Log Type are required."))

        # Validate employee belongs to this summary
        emp_names = [row.employee for row in self.details]
        if employee not in emp_names:
            frappe.throw(_("Employee {0} is not part of this Attendance Summary.").format(employee))

        return save_manual_checkin_record(
            employee=employee,
            checkin_time=checkin_time,
            log_type=log_type,
            checkin_name=checkin_name,
            is_overtime=is_overtime,
            remark=remark,
        )

    # ── called by JS "Process Attendance" ────────────────────────────────────
    @frappe.whitelist()
    def process_attendance(self):
        """
        Background-safe attendance processing.
        Enqueues a job so it doesn't block the UI.
        """
        if not self.details:
            frappe.throw(_("Please fetch employees first."))
        if not self.from_date or not self.to_date:
            frappe.throw(_("From Date and To Date are required."))

        frappe.db.set_value("Attendance Summary", self.name, "status", "Processing")
        frappe.db.commit()

        frappe.enqueue(
            "zkteco_attendance.zkteco_attendance.doctype.attendance_summary.attendance_summary._process_background",
            summary_name=self.name,
            queue="long",
            timeout=900,
            job_name="att_summary_{}".format(self.name),
        )

        return {"status": "queued", "message": _("Processing started. The page will update when complete.")}


def _process_background(summary_name):
    """Background job: calculate attendance for all employees in the summary.
    Employees with do_not_process checked are skipped."""
    doc = frappe.get_doc("Attendance Summary", summary_name)

    employee_list = [row.employee for row in doc.details if not row.do_not_process]

    checkins_by_employee = fetch_checkins(employee_list, doc.from_date, doc.to_date)

    doc_missing_action = doc.missing_checkin_action
    default_shift      = doc.shift_type

    for row in doc.details:
        if row.do_not_process:
            # Reset computed fields so reprocessing doesn't leave stale data
            row.working_days        = 0
            row.absent_days         = 0
            row.half_days           = 0
            row.total_working_hours = 0
            row.absent_hours        = 0
            row.overtime_hours      = 0
            row.day_ot_hours        = 0
            row.night_ot_hours      = 0
            row.weekend_ot_hours    = 0
            row.holiday_ot_hours    = 0
            row.overtime_days       = 0
            row.invalid_days        = 0
            row.manual_review_days  = 0
            row.remarks             = ""
            continue

        emp_checkins = checkins_by_employee.get(row.employee, [])
        result = process_employee(
            employee=row.employee,
            from_date=doc.from_date,
            to_date=doc.to_date,
            checkin_list=emp_checkins,
            default_shift_name=default_shift,
            doc_missing_action=doc_missing_action,
        )
        row.working_days        = result["working_days"]
        row.absent_days         = result["absent_days"]
        row.half_days           = result["half_days"]
        row.total_working_hours = result["total_working_hours"]
        row.absent_hours        = result["absent_hours"]
        row.overtime_hours      = result["overtime_hours"]
        row.day_ot_hours        = result.get("day_ot_hours", 0.0)
        row.night_ot_hours      = result.get("night_ot_hours", 0.0)
        row.weekend_ot_hours    = result.get("weekend_ot_hours", 0.0)
        row.holiday_ot_hours    = result.get("holiday_ot_hours", 0.0)
        row.overtime_days       = result["overtime_days"]
        row.invalid_days        = result["invalid_days"]
        row.manual_review_days  = result["manual_review_days"]
        if result["remarks"]:
            row.remarks = result["remarks"]

    processed_rows = [r for r in doc.details if not r.do_not_process]

    doc.status             = "Completed"
    doc.total_employees    = len(processed_rows)
    doc.total_working_days = count_working_days(doc.from_date, doc.to_date)
    doc.total_overtime_hours   = round(sum(flt(r.overtime_hours) for r in processed_rows), 2)
    doc.total_day_ot_hours     = round(sum(flt(r.day_ot_hours) for r in processed_rows), 2)
    doc.total_night_ot_hours   = round(sum(flt(r.night_ot_hours) for r in processed_rows), 2)
    doc.total_weekend_ot_hours = round(sum(flt(r.weekend_ot_hours) for r in processed_rows), 2)
    doc.total_holiday_ot_hours = round(sum(flt(r.holiday_ot_hours) for r in processed_rows), 2)
    doc.save(ignore_permissions=True)
    frappe.db.commit()

