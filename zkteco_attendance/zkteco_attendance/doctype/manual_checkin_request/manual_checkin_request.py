"""
Manual Checkin Request DocType Controller.

A submittable request that applies a manual Employee Checkin (create,
update, or Make Present) when the document is submitted. Requests are
created from the Daily Checkins page (Add / Edit / Make Present buttons)
and the Attendance Summary "Add Check-in" button; the check-in itself only
happens on submit, so requests can be reviewed before they take effect.

Request types:
- "New": create one Employee Checkin (log_type IN/OUT) at the given time.
- "Edit": update the existing check-in referenced by `checkin_name`.
- "Make Present": create BOTH an IN and an OUT check-in for a day that has
  no check-ins at all. The dialog asks for Start Time and End Time, both
  prefilled from the employee's shift (start / end). The IN check-in is
  created at Start Time and the OUT check-in at End Time; either time may
  be left blank (kept for requests created before this field existed), in
  which case the employee's shift is used as before - IN at shift start,
  OUT at shift end (a night shift's OUT rolls into the next day), and
  without a shift IN is the entered time and OUT is entered time + 8
  standard working hours. Log Type is not used and hidden.
"""
import json
from datetime import timedelta

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_datetime, getdate

# Employee Checkin fields captured before an "Edit" request is applied, so
# cancelling the request can restore the original values. Columns that may
# not exist (pre-patch databases) are skipped via has_column.
SNAPSHOT_FIELDS = ("is_overtime", "zk_remark", "manually_edited", "edited_by", "edited_at")

REQUEST_TYPES = ("New", "Edit", "Make Present")


class ManualCheckinRequest(Document):

    def validate(self):
        # Request Type is required: "New" adds a check-in, "Edit" modifies
        # the existing check-in referenced by `checkin_name`, "Make Present"
        # creates IN and OUT check-ins for a day that has none.
        if not self.request_type:
            self.request_type = "New"
        if self.request_type not in REQUEST_TYPES:
            frappe.throw(_("Request Type must be New, Edit, or Make Present."))

        if self.request_type == "Edit" and not self.checkin_name:
            frappe.throw(_("An Existing Check-in must be set when Request Type is Edit."))

        if self.request_type == "Make Present":
            # Log Type / Is Overtime do not apply - the IN check-in is
            # created at Start Time and the OUT at End Time.
            self.log_type = None
            self.is_overtime = 0
            if self.get("requested_start_time") and self.get("requested_end_time") \
                    and self.get("requested_end_time") == self.get("requested_start_time"):
                frappe.throw(_(
                    "Start Time and End Time must be different."
                ))
            if self.checkin_name:
                frappe.throw(_("Existing Check-in applies only to Edit requests."))
            # Make Present only works for a day that has no check-ins.
            self._validate_day_has_no_checkins()

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

    def _get_checkins_for_day(self):
        """Return the Employee Checkins of this employee on checkin_date."""
        day = str(self.checkin_date)
        return frappe.get_all(
            "Employee Checkin",
            filters=[
                ["employee", "=", self.employee],
                ["time", ">=", "{0} 00:00:00".format(day)],
                ["time", "<=", "{0} 23:59:59".format(day)],
            ],
            fields=["name", "log_type", "time"],
            order_by="time asc",
        )

    def _validate_day_has_no_checkins(self):
        existing = self._get_checkins_for_day()
        if existing:
            frappe.throw(_(
                "A check-in already exists for employee {0} on {1} ({2} check-in(s)). "
                "Make Present only works for a day that has no check-ins."
            ).format(self.employee, self.checkin_date, len(existing)))

    def on_submit(self):
        """Apply the request: create or update the Employee Checkin(s)."""
        from zkteco_attendance.zkteco_attendance.attendance_processor import save_manual_checkin_record

        if self.request_type == "Make Present":
            self._apply_make_present()
            return

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

    def _apply_make_present(self):
        """Create the IN and OUT check-ins for a day that has none.

        The day is re-checked on submit: a sync may have pulled check-ins
        between draft creation and submission.
        """
        from zkteco_attendance.zkteco_attendance.attendance_processor import save_manual_checkin_record

        existing = self._get_checkins_for_day()
        if existing:
            frappe.throw(_(
                "A check-in already exists for employee {0} on {1} ({2} check-in(s)). "
                "Make Present only works for a day that has no check-ins."
            ).format(self.employee, self.checkin_date, len(existing)))

        in_time, out_time = self._get_make_present_times()

        checkin_in = save_manual_checkin_record(
            employee=self.employee,
            checkin_time=in_time,
            log_type="IN",
            is_overtime=0,
            remark=self.request_remarks,
        )
        checkin_out = save_manual_checkin_record(
            employee=self.employee,
            checkin_time=out_time,
            log_type="OUT",
            is_overtime=0,
            remark=self.request_remarks,
        )

        self.db_set("applied_checkin", checkin_in["name"], update_modified=False)
        self.db_set("applied_checkin_out", checkin_out["name"], update_modified=False)

    def _get_make_present_times(self):
        """Return the (in_time, out_time) strings for a Make Present request.

        The dialog sends Start Time and End Time prefilled from the
        employee's shift, so the requested times win whenever they are set.
        A night-shift End Time that is not later than Start Time rolls into
        the next day. When no requested times were saved (older requests,
        or direct API calls) the times fall back to the employee's shift
        for that date: IN at shift start, OUT at shift end. Without a
        shift, IN is the request's own time and OUT is that time plus 8
        standard working hours.
        """
        from zkteco_attendance.zkteco_attendance.attendance_processor import get_shift_for_employee

        date_str = str(self.checkin_date)
        entered_time = str(self.checkin_time or "08:00:00")

        req_start = self.get("requested_start_time")
        req_end = self.get("requested_end_time")
        if req_start and req_end:
            in_dt = get_datetime("{0} {1}".format(date_str, req_start))
            out_dt = get_datetime("{0} {1}".format(date_str, req_end))
            # End not later than Start (e.g. night shift 22:00 -> 06:00):
            # the OUT lands on the next day.
            if out_dt <= in_dt:
                out_dt += timedelta(days=1)
            return str(in_dt), str(out_dt)

        try:
            shift = get_shift_for_employee(self.employee, self.checkin_date) or None
        except Exception:
            shift = None

        if not shift:
            in_dt = get_datetime("{0} {1}".format(date_str, entered_time))
            return str(in_dt), str(in_dt + timedelta(hours=8))

        start = str(shift.get("start_time") or entered_time)
        in_dt = get_datetime("{0} {1}".format(date_str, start))

        # Saturday Half Day: OUT after the half-day hours instead of shift end.
        if getdate(date_str).weekday() == 5 \
                and (shift.get("saturday_mode") or "") == "Half Day":
            hours = flt(shift.get("saturday_half_day_hours")
                        or shift.get("half_day_hours") or 4)
            return str(in_dt), str(in_dt + timedelta(hours=hours))

        end = str(shift.get("end_time") or "")
        if not end:
            hours = flt(shift.get("full_day_hours") or 8)
            return str(in_dt), str(in_dt + timedelta(hours=hours))

        out_dt = get_datetime("{0} {1}".format(date_str, end))
        # Night shifts cross midnight: OUT lands on the next day.
        if out_dt <= in_dt:
            out_dt += timedelta(days=1)
        return str(in_dt), str(out_dt)

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
        """Revert the checkins applied by this request.

        - "Edit" requests: restore the original values captured on submit.
        - "New" requests (and "Edit" requests whose target check-in had
          disappeared at submit time): the created Employee Checkin is
          deleted.
        - "Make Present" requests: both the IN and the OUT check-in are
          deleted.
        """
        removed = False
        for name in (self.applied_checkin, self.applied_checkin_out):
            if not name or not frappe.db.exists("Employee Checkin", name):
                continue
            if name == self.applied_checkin and self.original_checkin_data:
                self._restore_original_checkin()
            else:
                frappe.delete_doc(
                    "Employee Checkin", name,
                    ignore_permissions=True, force=1)
            removed = True

        if removed:
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