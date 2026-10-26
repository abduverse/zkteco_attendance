import frappe
from frappe import _
from frappe.model.document import Document
from datetime import datetime, timedelta


class ZKShiftType(Document):

    def validate(self):
        self._validate_company()
        self._validate_hours()
        self._validate_times()
        self._validate_lunch()

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
                      "For shifts crossing midnight, or running a full 24 hours "
                      "(the same Start and End Time), enable 'Night Shift'.")
                )

    def _validate_lunch(self):
        # Nothing is measured while lunch deviation is disabled, so the lunch
        # window and its allowance/threshold are not required either.
        if not self.enable_lunch_deviation:
            return
        # Lunch deviation tracking needs both ends of the lunch window; the
        # allowance/threshold alone do nothing without them.
        if not (self.lunch_start_time and self.lunch_end_time):
            frappe.throw(_("Lunch Start Time and Lunch End Time are required "
                           "when Enable Lunch Deviation is checked."))
        start = datetime.strptime(str(self.lunch_start_time), "%H:%M:%S")
        end = datetime.strptime(str(self.lunch_end_time), "%H:%M:%S")
        if end <= start:
            frappe.throw(_("Lunch End Time must be after Lunch Start Time."))

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

        A 24-hour shift (start_time == end_time) is attributed by punch order
        in the sync engine and attendance processor: its check-out falls at
        the same clock time as the next check-in, so this single-punch helper
        can only return the punch's own date, or the previous date when the
        punch is well before the shift start.

        Mirrors attendance_processor.group_checkins_by_date and
        sync_engine.get_attendance_date_for_punch.
        """
        start = datetime.strptime(str(self.start_time), "%H:%M:%S").time()
        end = datetime.strptime(str(self.end_time), "%H:%M:%S").time()

        if start == end:
            boundary = datetime.combine(checkin_dt.date(), start)
            if checkin_dt < boundary - timedelta(minutes=60):
                return (checkin_dt - timedelta(days=1)).date()
            return checkin_dt.date()

        if not self.is_night_shift:
            return checkin_dt.date()
        if start > end:
            shift_start = datetime.combine(checkin_dt.date(), start)
            if checkin_dt < shift_start - timedelta(minutes=60):
                return (checkin_dt - timedelta(days=1)).date()
            return checkin_dt.date()

        # Night shift that does not cross midnight (unusual)
        if checkin_dt.time() <= end:
            return (checkin_dt - timedelta(days=1)).date()
        return checkin_dt.date()
