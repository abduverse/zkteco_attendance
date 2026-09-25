"""
Patch: v1_10 — Add sync_data_after (Date) to Biometric Device.

Optional cutoff date for pulls: only attendance punches on or after this
date are synced to Employee Checkins. Reloads the DocType schema so the
new column is created.
"""

import frappe


def execute():
    try:
        frappe.reload_doc("zkteco_attendance", "doctype", "biometric_device")
    except Exception:
        frappe.log_error(
            message="ZKTeco: could not reload DocType for v1_10 patch",
            title="ZKTeco Patch v1_10"
        )

    frappe.db.commit()
