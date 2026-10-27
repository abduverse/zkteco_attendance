"""Reload Attendance Summary Detail to add absent-day calculation fields."""

import frappe


def execute():
    frappe.reload_doc("zkteco_attendance", "doctype", "attendance_summary_detail")
    frappe.db.commit()
