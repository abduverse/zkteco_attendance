"""
Unit tests for the Daily Check-ins page PDF report renderer.

The PDF HTML builder runs server-side when the page's "Download PDF"
button is clicked. These tests keep the builder deterministic and safe:
H:MM formatting, HTML escaping of user data, presence of status/OT/
check-in markers, per-employee totals, and the empty-data state.

Run with: bench run-tests --app zkteco_attendance
"""

import unittest

from zkteco_attendance.zkteco_attendance.page.zk_daily_checkins.zk_daily_checkins import (
    _hhmm,
    _render_pdf_html,
)


def _day(date_str, status="Present", hours=7.6, **overrides):
    day = {
        "date": date_str,
        "weekday": "Monday",
        "is_saturday": False,
        "is_weekend": False,
        "is_holiday": False,
        "holiday_name": "",
        "status": status,
        "hours": hours,
        "effective_hours": hours,
        "is_late": False,
        "is_early_exit": False,
        "late_minutes": 0.0,
        "early_minutes": 0.0,
        "overtime_hours": 0.0,
        "day_ot_hours": 0.0,
        "night_ot_hours": 0.0,
        "weekend_ot_hours": 0.0,
        "holiday_ot_hours": 0.0,
        "checkins": [],
    }
    day.update(overrides)
    return day


def _checkin(time_str, log_type="IN", **overrides):
    checkin = {
        "name": "CHK-1",
        "time": time_str,
        "log_type": log_type,
        "is_overtime": False,
        "manually_edited": False,
        "ignored": False,
    }
    checkin.update(overrides)
    return checkin


def _employee(employee="EMP-0001", employee_name="John Smith", days=None, **overrides):
    emp = {
        "employee": employee,
        "employee_name": employee_name,
        "department": "Production",
        "designation": "Operator",
        "zk_biometric_device": "DEV-01",
        "attendance_device_id": "101",
        "shift_type": "General",
        "days": days if days is not None else [],
    }
    emp.update(overrides)
    return emp


def _sample_data():
    return {
        "attendance_summary": "SUM-0001",
        "company": "Acme Inc",
        "from_date": "2026-08-01",
        "to_date": "2026-08-31",
        "employees": [
            _employee(days=[
                _day("2026-08-03", checkins=[_checkin("08:57:00", "IN")]),
                _day("2026-08-04", status="Half Day", hours=3.5,
                     is_late=True, late_minutes=12.0),
                _day("2026-08-09", status="Weekly Off", hours=0.0),
                _day("2026-08-14", status="Holiday", hours=0.0,
                     is_holiday=True, holiday_name="Independence Day"),
                _day("2026-08-20", hours=8.5, overtime_hours=0.5,
                     day_ot_hours=0.5,
                     checkins=[
                         _checkin("08:00:00", "IN"),
                         _checkin("17:30:00", "OUT"),
                     ]),
            ]),
            _employee(employee="EMP-0002", employee_name="Jane Roe",
                      department="Finance", designation="",
                      zk_biometric_device="DEV-02",
                      attendance_device_id="102", shift_type="",
                      days=[
                          _day("2026-08-03", hours=9.0, checkins=[
                              _checkin("08:00:00", "IN", manually_edited=True),
                              _checkin("09:15:00", "OUT", ignored=True),
                              _checkin("17:00:00", "OUT", is_overtime=True),
                          ]),
                      ]),
        ],
    }


class TestDailyCheckinsPdfRenderer(unittest.TestCase):

    def test_hours_to_hhmm(self):
        """Decimal hours must render as clock-style H:MM like the page column."""
        self.assertEqual(_hhmm(7.6), "7:36")
        self.assertEqual(_hhmm(8.0), "8:00")
        self.assertEqual(_hhmm(0), "0:00")
        self.assertEqual(_hhmm(None), "0:00")
        self.assertEqual(_hhmm(-1.5), "0:00")  # negative hours never print

    def test_render_pdf_html_structure(self):
        """The document contains header, meta chips, legend and employee tables."""
        html = _render_pdf_html(_sample_data())

        self.assertIn("Employee Daily Check-ins", html)
        self.assertIn("Biometric Attendance Report", html)
        # Period formatted and shown in the report-period box
        self.assertIn("01 Aug 2026", html)
        self.assertIn("31 Aug 2026", html)
        # Meta chips
        self.assertIn("Acme Inc", html)
        self.assertIn("SUM-0001", html)
        self.assertIn("2", html)  # Employees count chip
        # Legend
        self.assertIn("Status", html)
        self.assertIn("Markers", html)

    def test_render_pdf_html_employee_rows(self):
        """Status pills, grace marks, OT chips and check-in chips are emitted."""
        html = _render_pdf_html(_sample_data())

        self.assertIn("John Smith", html)
        self.assertIn("Jane Roe", html)
        self.assertIn("p-present", html)
        self.assertIn("p-half", html)
        self.assertIn("p-weekoff", html)
        self.assertIn("p-holiday", html)
        self.assertIn("Independence Day", html)  # holiday sub-note
        self.assertIn("L-EN 12m", html)          # late-entry marker
        self.assertIn("7.60", html)              # decimal hours column
        self.assertIn("7:36", html)              # formatted hours column
        self.assertIn("ot-day", html)            # day OT chip on 20 Aug
        self.assertIn("08:57 <b>IN</b>", html)   # check-in chip
        self.assertIn("17:30 <b>OUT</b>", html)
        self.assertIn("ck-ot", html)             # overtime punch chip
        self.assertIn('<span class="ck-mark">*</span>', html)  # manual edit
        self.assertIn("<s>", html)               # ignored check-in struck out
        self.assertIn("Period total", html)      # per-employee totals row
        self.assertIn("DEV-01", html)            # device badge
        self.assertIn("General", html)           # shift badge

    def test_render_pdf_html_escapes_user_data(self):
        """Names and free-text fields must never leak raw HTML into the PDF."""
        data = _sample_data()
        data["employees"][0]["employee_name"] = "<script>alert(1)</script>"
        data["employees"][0]["days"][0]["holiday_name"] = "<img src=x onerror=alert(1)>"
        data["employees"][0]["days"][0]["checkins"] = [
            _checkin("08:00:00", "IN", edited_by="<b>admin</b>"),
        ]

        html = _render_pdf_html(data)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<img src=x onerror=alert(1)>", html)

    def test_render_pdf_html_empty_state(self):
        """No employees -> a clean message, no tables, no crash."""
        html = _render_pdf_html({
            "attendance_summary": "",
            "company": "",
            "from_date": "2026-08-01",
            "to_date": "2026-08-31",
            "employees": [],
        })
        self.assertIn("No attendance data found for the selected filters.", html)
        self.assertIn("no-data", html)
        self.assertNotIn("Period total", html)


if __name__ == "__main__":
    unittest.main()
