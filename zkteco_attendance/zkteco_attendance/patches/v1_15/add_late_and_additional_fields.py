"""
Patch: v1_15 — Add late / additional working time fields to Attendance Summary.

Adds the late minutes breakdown (late_shift_minutes, late_lunch_minutes), the
total late hours and the additional working hours to Attendance Summary Detail
so processing can record them. This patch only reloads the DocTypes so
existing sites pick up the JSON schema change; the new columns are nullable,
so no data migration is needed.
"""

import frappe


def execute():
    for doctype in ("attendance_summary_detail", "attendance_summary"):
        try:
            frappe.reload_doc("zkteco_attendance", "doctype", doctype)
        except Exception:
            frappe.log_error(
                message="ZKTeco: could not reload DocType {0} for v1_15 patch".format(doctype),
                title="ZKTeco Patch v1_15"
            )

    frappe.db.commit()
