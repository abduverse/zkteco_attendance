from . import __version__ as app_version

app_name = "zkteco_attendance"
app_title = "ZKTeco Attendance"
app_publisher = "abdu"
app_description = "ZKTeco Biometric Device Integration for ERPNext"
app_email = "abdulsomed@0825@gmail.com"
app_license = "MIT"

app_include_css = "/assets/zkteco_attendance/css/zkteco_attendance.css"
app_include_js  = "/assets/zkteco_attendance/js/zkteco_attendance.js"

# Frappe v14 auto-loads <module>/doctype/<name>/<name>.js — no doctype_js needed.

after_install    = "zkteco_attendance.zkteco_attendance.install.after_install"
before_uninstall = "zkteco_attendance.zkteco_attendance.install.before_uninstall"

# v14-compatible scheduler
scheduler_events = {
    "all": [
        "zkteco_attendance.zkteco_attendance.tasks.scheduler.sync_devices_on_schedule"
    ],
    "hourly": [
        "zkteco_attendance.zkteco_attendance.tasks.scheduler.sync_devices_hourly"
    ],
    "daily": [
        "zkteco_attendance.zkteco_attendance.tasks.scheduler.sync_devices_daily"
    ],
}

fixtures = [
    {"dt": "Custom Field", "filters": [["module", "=", "Zkteco Attendance"]]},
    {"dt": "Role",         "filters": [["name", "in", ["Biometric Device Manager", "Checkin Editor", "Checkin Request Approver"]]]}
]
