import frappe
from frappe import _
from frappe.model.document import Document
from datetime import datetime, timedelta


class ZKShiftType(Document):

    def validate(self):
        self._validate_company()
        self._validate_hours()
        self._validate_times()

    def _validate_company(self):
        # Company is optional when 'Ignore Company Restriction' is enabled.
        if not self.ignore_company_restriction and not self.company:
            frappe.throw(
                _("Company is required unless 'Ignore Company Restriction' is enabled.")
            )

    def _validate_hours(self):
        if self.half_day_hours >= self.full_day_hours:
            frappe.throw(_("Half Day Hours must be less than Full Day Hours."))
        if self.standard_working_hours <= 0:
            frappe.throw(_("Standard Working Hours must be greater than 0."))

    def _validate_times(self):
        if not self.is_night_shift:
            start = datetime.strptime(str(self.start_time), "%H:%M:%S")
            end   = datetime.strptime(str(self.end_time),   "%H:%M:%S")
            if end <= start:
                frappe.throw(
                    _("End Time must be after Start Time for non-night shifts. "
                      "For shifts crossing midnight, enable 'Night Shift'.")
                )

    def get_attendance_date(self, checkin_dt):
        """
        Given a checkin datetime, return the 'attendance date' it belongs to.
        For night shifts, a checkin before midnight belongs to the previous day's shift.
        """
        from datetime import time as dtime
        start = datetime.strptime(str(self.start_time), "%H:%M:%S").time()
        if self.is_night_shift:
            # If checkin hour is before the shift end (i.e. in the AM of next day),
            # the attendance date is the previous calendar day.
            end = datetime.strptime(str(self.end_time), "%H:%M:%S").time()
            if checkin_dt.time() <= end:
                return (checkin_dt - timedelta(days=1)).date()
        return checkin_dt.date()
