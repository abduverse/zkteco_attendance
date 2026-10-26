"""Patch: v1_13 — Add the Lunch Deviation fields to ZK Shift Type.

Adds the Enable Lunch Deviation checkbox, Lunch Start Time, Lunch End Time,
the per-day allowed deviation in minutes and the day threshold after which an
employee is flagged.  Shifts that already have both lunch times configured are
enabled so their existing lunch window keeps being measured; every other shift
stays off until the checkbox is ticked.
"""

import frappe


def execute():
    try:
        frappe.reload_doc("zkteco_attendance", "doctype", "zk_shift_type")
    except Exception:
        frappe.log_error(
            message="ZKTeco: could not reload DocType 'ZK Shift Type' for lunch deviation fields",
            title="ZKTeco Patch v1_13"
        )

    # Shifts that already configured a lunch window were being measured
    # before the checkbox existed — keep them on.
    try:
        frappe.db.sql(
            """
            UPDATE `tabZK Shift Type`
               SET enable_lunch_deviation = 1
             WHERE IFNULL(lunch_start_time, '') != ''
               AND IFNULL(lunch_end_time, '') != ''
            """
        )
    except Exception:
        frappe.log_error(
            message="ZKTeco: could not enable lunch deviation for shifts with a lunch window",
            title="ZKTeco Patch v1_13"
        )

    frappe.db.commit()
