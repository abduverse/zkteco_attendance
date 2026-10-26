"""Patch: v1_14 — Add the Threshold for Late/Compensate field to ZK Shift Type.

The field is the grace used for the lunch late/early decision when a shift
enables Lunch Deviation: minutes after Lunch End Time count as late entry
minutes and minutes before Lunch Start Time count as early exit minutes, both
beyond this threshold.  This patch only reloads the DocType so existing sites
pick up the JSON schema change; the column is nullable with a 0 default, so no
data migration is needed.
"""

import frappe


def execute():
    try:
        frappe.reload_doc("zkteco_attendance", "doctype", "zk_shift_type")
    except Exception:
        frappe.log_error(
            message="ZKTeco: could not reload DocType 'ZK Shift Type' for the Threshold for Late/Compensate field",
            title="ZKTeco Patch v1_14"
        )

    frappe.db.commit()
