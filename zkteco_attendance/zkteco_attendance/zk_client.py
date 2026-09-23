"""
ZKTeco Device Client
Wraps pyzk library with error handling, logging, and timezone support.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime, get_datetime, cint
import pytz
from datetime import datetime, timedelta


def get_zk_connection(device_doc):
    """
    Establish a connection to a ZKTeco device.
    Returns a ZK instance (connected) or raises an exception.
    """
    try:
        from zk import ZK, const
    except ImportError:
        frappe.throw(
            _("pyzk library is not installed. Run: pip install pyzk"),
            title=_("Missing Dependency")
        )

    ip = device_doc.device_ip
    port = int(device_doc.port or 4370)

    # get_password() works the same way across v14/v15/v16
    password = device_doc.get_password("connection_password") or 0
    timeout = 10

    zk = ZK(ip, port=port, timeout=timeout, password=int(password) if password else 0, force_udp=False, ommit_ping=False)

    try:
        conn = zk.connect()
        return conn, zk
    except Exception as e:
        frappe.log_error(
            message=f"ZKTeco connection failed for device {device_doc.name} ({ip}:{port}): {str(e)}",
            title="ZKTeco Connection Error"
        )
        raise frappe.ValidationError(
            _("Cannot connect to device {0} at {1}:{2}. Error: {3}").format(
                device_doc.device_name, ip, port, str(e)
            )
        )


def test_device_connection(device_name):
    """
    Test connection to a device and return device info.
    Called from the form button and the API.
    """
    device = frappe.get_doc("Biometric Device", device_name)

    result = {
        "success": False,
        "device_name": device_name,
        "ip": device.device_ip,
        "port": device.port,
    }

    conn = None
    zk = None
    try:
        conn, zk = get_zk_connection(device)

        serial = conn.get_serialnumber()
        firmware = conn.get_firmware_version()
        device_time = conn.get_time()
        users = conn.get_users()
        attendance = conn.get_attendance()

        result.update({
            "success": True,
            "serial_number": serial,
            "firmware_version": firmware,
            "device_time": str(device_time),
            "enrolled_users": len(users) if users else 0,
            "attendance_logs": len(attendance) if attendance else 0,
            "message": _("Connection successful"),
        })

        # Update device status
        frappe.db.set_value("Biometric Device", device_name, "status", "Active")

    except Exception as e:
        result["error"] = str(e)
        result["message"] = _("Connection failed: {0}").format(str(e))
        frappe.db.set_value("Biometric Device", device_name, "status", "Inactive")

    finally:
        if conn:
            try:
                conn.disconnect()
            except Exception:
                pass

    frappe.db.commit()
    return result


def get_device_users(device_name):
    """
    Fetch users enrolled on a ZKTeco device (uid, user_id, name, privilege).
    Called from the Biometric Device form's "Browse Employees On Device"
    dialog so users can be mapped to ERPNext Employees.
    """
    device = frappe.get_doc("Biometric Device", device_name)

    conn = None
    try:
        conn, zk = get_zk_connection(device)
        users = conn.get_users() or []

        return [
            {
                "uid": u.uid,
                "user_id": str(u.user_id),
                "name": (u.name or "").strip(),
                "privilege": u.privilege,
            }
            for u in users
        ]
    finally:
        if conn:
            try:
                conn.disconnect()
            except Exception:
                pass


def _adjust_for_clock_offset(timestamp, device_doc):
    """
    Most ZKTeco devices report attendance timestamps using the device's own
    real-time clock, already set to the *local* time the punch happened
    (e.g. the employee badges at 7:55 AM and the device stores "07:55:00").

    Frappe stores Datetime fields as naive values in the *site's* local
    timezone (System Settings > Time Zone), NOT in UTC. So the correct
    handling of a device timestamp that already represents local
    wall-clock time is to store it AS-IS (naive, with no UTC conversion).

    Previously this code ran the naive device time through
    `device_tz.localize(...).astimezone(UTC)`, which shifted it by the
    device timezone's UTC offset. That produced incorrect "pulled" times
    on Employee Checkin (e.g. an actual 07:55 punch ending up showing as
    07:21 or similar after the extra shift).

    If a device's clock genuinely runs ahead/behind local time, configure
    `clock_offset_minutes` on the Biometric Device to apply a fixed
    correction. Defaults to 0 (no shift), which is correct for the
    standard "device clock == local wall time" setup.
    """
    offset_minutes = cint(getattr(device_doc, "clock_offset_minutes", 0) or 0)
    if offset_minutes:
        return timestamp + timedelta(minutes=offset_minutes)
    return timestamp


def pull_attendance_from_device(device_doc, fetch_mode="New Records Only", progress_callback=None):
    """
    Pull attendance logs from a ZKTeco device.
    Returns list of dicts: {uid, user_id, timestamp, punch, status}

    progress_callback(stage, current, total, message) is called (if provided)
    at key points so the caller can surface live progress to the user.
    """
    conn = None
    zk = None
    records = []

    def _progress(stage, current, total, message=""):
        if progress_callback:
            try:
                progress_callback(stage, current, total, message)
            except Exception:
                pass

    try:
        _progress("connecting", 0, 0, _("Connecting to device {0}...").format(device_doc.device_name))
        conn, zk = get_zk_connection(device_doc)

        _progress("fetching", 0, 0, _("Fetching attendance logs from device..."))
        attendance_logs = conn.get_attendance() or []
        total = len(attendance_logs)

        _progress("fetched", 0, total, _("Fetched {0} raw record(s) from device.").format(total))

        if not attendance_logs:
            return records

        for idx, log in enumerate(attendance_logs, start=1):
            # Device timestamps already represent local wall-clock time.
            # Do NOT convert through UTC — store as-is (with optional
            # fixed clock offset if configured on the device).
            ts = log.timestamp
            try:
                ts = _adjust_for_clock_offset(ts, device_doc)
            except Exception:
                pass

            records.append({
                "uid": log.uid,
                "user_id": str(log.user_id),
                "timestamp": ts,  # naive datetime, local wall-clock time
                "punch": log.punch,      # 0=Check In, 1=Check Out, 2=Break Out, 3=Break In, 4=OT In, 5=OT Out
                "status": log.status,
            })

            if idx % 25 == 0 or idx == total:
                _progress("processing_raw", idx, total, _("Read {0} of {1} record(s)...").format(idx, total))

        # Clear logs if configured
        if device_doc.clear_device_logs_after_sync:
            try:
                conn.clear_attendance()
                frappe.log_error(
                    message=f"Cleared attendance logs on device {device_doc.name} after sync.",
                    title="ZKTeco: Logs Cleared"
                )
            except Exception as e:
                frappe.log_error(
                    message=f"Failed to clear logs on device {device_doc.name}: {str(e)}",
                    title="ZKTeco: Clear Logs Failed"
                )

    except Exception as e:
        frappe.log_error(
            message=f"Error pulling attendance from {device_doc.name}: {str(e)}",
            title="ZKTeco Pull Error"
        )
        raise
    finally:
        if conn:
            try:
                conn.disconnect()
            except Exception:
                pass

    return records


def get_punch_type(punch_code):
    """
    Map ZKTeco punch codes to ERPNext check-in log types.
    0 = Check In, 1 = Check Out, 2 = Break Out, 3 = Break In, 4 = OT In, 5 = OT Out

    NOTE: Many ZKTeco devices/firmwares always send punch=0 for every
    fingerprint/face punch regardless of whether it's an actual check-in
    or check-out (the device has no concept of shift state). When that is
    the case, `get_punch_type` alone is NOT enough to tell IN apart from
    OUT — the sync engine resolves the final log_type per employee/day by
    alternating IN/OUT in chronological order (see
    `sync_engine.resolve_log_types_for_day`). This function still provides
    the best first guess and is authoritative for explicit OT punches
    (4/5), which are preserved as overtime log types when overtime
    management is enabled.
    """
    in_codes = {0, 4}
    out_codes = {1, 2, 3, 5}

    if punch_code in in_codes:
        return "IN"
    elif punch_code in out_codes:
        return "OUT"
    else:
        return "IN"  # default


def is_overtime_punch(punch_code):
    """Return True if the punch code represents an explicit OT In/Out punch."""
    return punch_code in (4, 5)
