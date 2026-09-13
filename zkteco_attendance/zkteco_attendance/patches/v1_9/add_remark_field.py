"""
Patch: v1_9 — Add zk_remark field to Employee Checkin.

Adds a Small Text 'Remark' field so checkins can carry a free-text
note (e.g. reason for a manually added punch). Reuses the installer
helper so new installs and patched sites end up identical.
Inserting the Custom Field itself creates the DB column, so no
doctype reload is needed.
"""

import frappe


def execute():
    from zkteco_attendance.zkteco_attendance.install import (
        _add_employee_checkin_remark_field,
    )

    _add_employee_checkin_remark_field()

    frappe.db.commit()
