"""
Patch: v1_12 — Add Make Present Start/End Time fields.

Adds requested_start_time and requested_end_time (Time) to Manual Checkin
Request so a "Make Present" request records the exact times chosen in the
dialog (prefilled from the employee's shift). This patch only reloads the
DocType so existing sites pick up the JSON schema change immediately; the
new columns are nullable, so no data migration is needed and older requests
keep falling back to the employee's shift on submit.
"""

import frappe


def execute():
    try:
        frappe.reload_doc("zkteco_attendance", "doctype", "manual_checkin_request")
    except Exception:
        frappe.log_error(
            message="ZKTeco: could not reload DocType manual_checkin_request for v1_12 patch",
            title="ZKTeco Patch v1_12"
        )

    frappe.db.commit()
