"""
Install / Uninstall hooks for ZKTeco Attendance.
"""

import frappe
from frappe import _


def after_install():
    """Run after app is installed via bench install-app."""
    _create_biometric_device_manager_role()
    _create_checkin_editor_role()
    _create_checkin_request_approver_role()
    _add_employee_checkin_device_field()
    _add_employee_checkin_zk_uid_field()
    _add_employee_checkin_overtime_field()
    _add_employee_checkin_manual_fields()
    _add_employee_checkin_ignored_field()
    _add_employee_checkin_remark_field()
    _add_employee_biometric_field()
    _add_employee_location_device_field()
    frappe.db.commit()
    frappe.msgprint(_("ZKTeco Attendance installed successfully."))


def before_uninstall():
    """Cleanup on uninstall."""
    pass


def _create_biometric_device_manager_role():
    if not frappe.db.exists("Role", "Biometric Device Manager"):
        role = frappe.get_doc({
            "doctype": "Role",
            "role_name": "Biometric Device Manager",
            "desk_access": 1,
            "is_custom": 1,
        })
        role.insert(ignore_permissions=True)


def _create_checkin_editor_role():
    if not frappe.db.exists("Role", "Checkin Editor"):
        role = frappe.get_doc({
            "doctype": "Role",
            "role_name": "Checkin Editor",
            "desk_access": 1,
            "is_custom": 1,
        })
        role.insert(ignore_permissions=True)


def _create_checkin_request_approver_role():
    """Approves (submits) or cancels Manual Checkin Requests."""
    if not frappe.db.exists("Role", "Checkin Request Approver"):
        role = frappe.get_doc({
            "doctype": "Role",
            "role_name": "Checkin Request Approver",
            "desk_access": 1,
            "is_custom": 1,
        })
        role.insert(ignore_permissions=True)


def _field_exists(doctype, fieldname):
    """
    True if `fieldname` already exists on `doctype` — whether as a
    standard/core field (e.g. ERPNext HR's Employee.attendance_device_id)
    or as a previously-created Custom Field. Checking the doctype meta
    (instead of only `Custom Field`) avoids "field already exists" errors
    on installs where the field is already part of core.
    """
    try:
        if frappe.get_meta(doctype).has_field(fieldname):
            return True
    except Exception:
        pass

    return frappe.db.exists("Custom Field", {"dt": doctype, "fieldname": fieldname})


def _add_employee_checkin_remark_field():
    """Add zk_remark field to Employee Checkin for a free-text punch remark."""
    if _field_exists("Employee Checkin", "zk_remark"):
        return

    after = "edited_at" if _field_exists("Employee Checkin", "edited_at") else "log_type"

    cf = frappe.get_doc({
        "doctype": "Custom Field",
        "dt": "Employee Checkin",
        "module": "Zkteco Attendance",
        "label": "Remark",
        "fieldname": "zk_remark",
        "fieldtype": "Small Text",
        "insert_after": after,
        "description": "Free-text remark on this checkin (e.g. reason for a manually added punch).",
        "in_list_view": 0,
    })
    cf.insert(ignore_permissions=True)


def _add_employee_biometric_field():
    """
    Add a 'Biometric Attendance ID' field to Employee, used to match
    attendance device records to ERPNext employees.

    ERPNext's HR module already ships a core `attendance_device_id` field
    on Employee — if present, we reuse it as-is and do nothing further.
    Only create the Custom Field if neither a core nor custom version of
    this field exists yet.
    """
    if _field_exists("Employee", "attendance_device_id"):
        return

    cf = frappe.get_doc({
        "doctype": "Custom Field",
        "dt": "Employee",
        "module": "Zkteco Attendance",
        "label": "Biometric Attendance ID",
        "fieldname": "attendance_device_id",
        "fieldtype": "Data",
        "insert_after": "employee_number",
        "description": "ID enrolled on the ZKTeco biometric device. Used to match attendance records.",
        "in_list_view": 0,
        "search_index": 1,
    })
    cf.insert(ignore_permissions=True)


def _add_employee_location_device_field():
    """
    Add 'Biometric Device' Link field to Employee so employees can be
    mapped to a specific device/location — used alongside
    attendance_device_id to scope pull lookups to the correct device.
    """
    if _field_exists("Employee", "zk_biometric_device"):
        return

    # insert after attendance_device_id (or employee_number as fallback)
    after = "attendance_device_id" if _field_exists("Employee", "attendance_device_id") else "employee_number"

    cf = frappe.get_doc({
        "doctype": "Custom Field",
        "dt": "Employee",
        "module": "Zkteco Attendance",
        "label": "Biometric Device",
        "fieldname": "zk_biometric_device",
        "fieldtype": "Link",
        "options": "Biometric Device",
        "insert_after": after,
        "description": "Link this employee to their primary biometric device. Used to restrict checkin lookup to the correct device when pulling attendance.",
        "in_list_view": 0,
    })
    cf.insert(ignore_permissions=True)


def _add_employee_checkin_manual_fields():
    """
    Add manually_edited, edited_by, edited_at fields to Employee Checkin
    to track records added or modified manually via Attendance Summary.
    """
    after = "is_overtime" if _field_exists("Employee Checkin", "is_overtime") else "log_type"

    fields = [
        {
            "fieldname": "manually_edited",
            "fieldtype": "Check",
            "label": "Manually Edited",
            "insert_after": after,
            "description": "Set when this record was added or modified manually (not from device).",
            "read_only": 1, "no_copy": 1,
        },
        {
            "fieldname": "edited_by",
            "fieldtype": "Link",
            "options": "User",
            "label": "Edited By",
            "insert_after": "manually_edited",
            "read_only": 1, "no_copy": 1,
        },
        {
            "fieldname": "edited_at",
            "fieldtype": "Datetime",
            "label": "Edited At",
            "insert_after": "edited_by",
            "read_only": 1, "no_copy": 1,
        },
    ]
    for fdef in fields:
        if _field_exists("Employee Checkin", fdef["fieldname"]):
            continue
        cf = frappe.get_doc({
            "doctype": "Custom Field",
            "dt": "Employee Checkin",
            "module": "Zkteco Attendance",
            **fdef,
        })
        cf.insert(ignore_permissions=True)


def _add_employee_checkin_device_field():
    """Add device_id field to Employee Checkin if not already there."""
    if _field_exists("Employee Checkin", "device_id"):
        return

    cf = frappe.get_doc({
        "doctype": "Custom Field",
        "dt": "Employee Checkin",
        "module": "Zkteco Attendance",
        "label": "Biometric Device",
        "fieldname": "device_id",
        "fieldtype": "Data",
        "insert_after": "log_type",
        "description": "ZKTeco device that recorded this checkin.",
        "in_list_view": 0,
    })
    cf.insert(ignore_permissions=True)


def _add_employee_checkin_zk_uid_field():
    """Add zk_uid field to Employee Checkin (informational only)."""
    if _field_exists("Employee Checkin", "zk_uid"):
        return

    insert_after = "device_id" if _field_exists("Employee Checkin", "device_id") else "log_type"

    cf = frappe.get_doc({
        "doctype": "Custom Field",
        "dt": "Employee Checkin",
        "module": "Zkteco Attendance",
        "label": "ZK Device Record ID",
        "fieldname": "zk_uid",
        "fieldtype": "Data",
        "insert_after": insert_after,
        "description": "Device-internal user id (uid) of the punch. Informational only — not unique per attendance log, so it is NOT used for de-duplication.",
        "in_list_view": 0,
        "read_only": 1,
        "no_copy": 1,
    })
    cf.insert(ignore_permissions=True)


def _add_employee_checkin_overtime_field():
    """Add is_overtime checkbox to Employee Checkin for overtime punches (OT In/Out)."""
    if _field_exists("Employee Checkin", "is_overtime"):
        return

    insert_after = "zk_uid" if _field_exists("Employee Checkin", "zk_uid") else "log_type"

    cf = frappe.get_doc({
        "doctype": "Custom Field",
        "dt": "Employee Checkin",
        "module": "Zkteco Attendance",
        "label": "Overtime Punch",
        "fieldname": "is_overtime",
        "fieldtype": "Check",
        "insert_after": insert_after,
        "description": "Set when this checkin was recorded as an Overtime In/Out punch (device punch code 4/5).",
        "in_list_view": 1,
        "no_copy": 1,
    })
    cf.insert(ignore_permissions=True)


def _add_employee_checkin_ignored_field():
    """Add ignored checkbox to Employee Checkin to exclude checkins from attendance processing."""
    if _field_exists("Employee Checkin", "zk_ignored"):
        return

    after = "is_overtime" if _field_exists("Employee Checkin", "is_overtime") else "log_type"

    cf = frappe.get_doc({
        "doctype": "Custom Field",
        "dt": "Employee Checkin",
        "module": "Zkteco Attendance",
        "label": "Ignored",
        "fieldname": "zk_ignored",
        "fieldtype": "Check",
        "insert_after": after,
        "description": "Set when this checkin should be excluded from attendance processing.",
        "in_list_view": 0,
    })
    cf.insert(ignore_permissions=True)
