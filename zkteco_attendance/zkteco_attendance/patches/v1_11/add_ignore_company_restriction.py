"""
Patch: v1_11 — Add 'Ignore Company Restriction' checkbox.

Adds ignore_company_restriction to Biometric Device, ZK Shift Type and
ZK Shift Assignment and makes their Company field optional when it is
enabled (via doctype sync). This patch only reloads the DocTypes so
existing sites pick up the JSON schema changes immediately.
"""

import frappe


def execute():
    for doctype in ("biometric_device", "zk_shift_type", "zk_shift_assignment"):
        try:
            frappe.reload_doc("zkteco_attendance", "doctype", doctype)
        except Exception:
            frappe.log_error(
                message="ZKTeco: could not reload DocType {0} for v1_11 patch".format(doctype),
                title="ZKTeco Patch v1_11"
            )

    frappe.db.commit()
