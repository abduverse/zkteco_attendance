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

        A punch belongs to the day the shift STARTED.  For a night shift that
        crosses midnight (start_time > end_time) only the early-AM hours before
        the shift start — minus a 60-minute early-arrival buffer — roll back to
        the previous calendar day.  A punch after midnight stays on the start
        day even when it is past the shift's end time, so a 22:00-01:00 shift
        keeps a 01:11 checkout on the day the shift began instead of treating
        it as the first punch of the next day.

        Mirrors attendance_processor.group_checkins_by_date and
        sync_engine.get_attendance_date_for_punch.
        """
        start = datetime.strptime(str(self.start_time), "%H:%M:%S").time()
        if not self.is_night_shift:
            return checkin_dt.date()

        end = datetime.strptime(str(self.end_time), "%H:%M:%S").time()
        if start > end:
            shift_start = datetime.combine(checkin_dt.date(), start)
            if checkin_dt < shift_start - timedelta(minutes=60):
                return (checkin_dt - timedelta(days=1)).date()
            return checkin_dt.date()

        # Night shift that does not cross midnight (unusual)
        if checkin_dt.time() <= end:
            return (checkin_dt - timedelta(days=1)).date()
        return checkin_dt.date()
