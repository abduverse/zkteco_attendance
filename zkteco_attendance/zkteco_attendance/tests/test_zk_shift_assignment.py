"""
Unit tests for ZK Shift Assignment duplicate handling:
- duplicate employee rows within one assignment are removed silently
- employees listed in an Active assignment are removed from other Active
  assignments (which are deleted when left empty) instead of raising a
  validation error
- Inactive assignments never touch other assignments
Run with: bench run-tests --app zkteco_attendance
"""

import unittest
from unittest.mock import MagicMock, call, patch

import frappe

from zkteco_attendance.zkteco_attendance.doctype.zk_shift_assignment.zk_shift_assignment import (
    ZKShiftAssignment,
)


def _assignment(status="Active", rows=None, name=None, company="Acme",
                ignore_company=0):
    doc = MagicMock(spec=ZKShiftAssignment)
    doc.name = name
    doc.status = status
    doc.company = company
    doc.ignore_company_restriction = ignore_company
    doc.employees = rows or []
    doc.flags = {}
    # Bind the controller methods under test to the mock document.
    doc.validate = ZKShiftAssignment.validate.__get__(doc)
    doc._validate_company = ZKShiftAssignment._validate_company.__get__(doc)
    doc._remove_duplicate_employee_rows = (
        ZKShiftAssignment._remove_duplicate_employee_rows.__get__(doc))
    doc._remove_duplicates_from_other_active_assignments = (
        ZKShiftAssignment._remove_duplicates_from_other_active_assignments.__get__(doc))
    doc._remove_employee_from_assignment = (
        ZKShiftAssignment._remove_employee_from_assignment.__get__(doc))
    # The MagicMock's own .remove is a no-op; replace it with a real
    # row-removal so `self.remove(row)` actually mutates doc.employees.
    doc.remove = lambda row: doc.employees.__setitem__(
        slice(None), [r for r in doc.employees if r is not row])
    return doc


def _row(employee):
    row = MagicMock()
    row.employee = employee
    return row


class TestRemoveDuplicateEmployeeRows(unittest.TestCase):
    """Duplicate rows for the same employee inside one assignment."""

    def _doc(self, employees):
        return _assignment(rows=[_row(e) for e in employees])

    def test_removes_later_duplicate_rows_keeps_first(self):
        doc = self._doc(["HR-EMP-00001", "HR-EMP-00002", "HR-EMP-00001"])
        with patch.object(frappe, "msgprint") as mock_msg:
            doc.validate()
        # first kept, later duplicate removed
        self.assertEqual([r.employee for r in doc.employees],
                         ["HR-EMP-00001", "HR-EMP-00002"])
        mock_msg.assert_called_once()

    def test_no_duplicates_no_message(self):
        doc = self._doc(["HR-EMP-00001", "HR-EMP-00002"])
        with patch.object(frappe, "msgprint") as mock_msg:
            doc.validate()
        self.assertEqual([r.employee for r in doc.employees],
                         ["HR-EMP-00001", "HR-EMP-00002"])
        mock_msg.assert_not_called()

    def test_blank_rows_are_ignored(self):
        doc = self._doc(["HR-EMP-00001", "", "HR-EMP-00001"])
        with patch.object(frappe, "msgprint"):
            doc.validate()
        # blank rows are ignored (kept, never counted as duplicates),
        # while the second HR-EMP-00001 row is removed
        self.assertEqual(
            [r.employee for r in doc.employees],
            ["HR-EMP-00001", ""])


class TestRemoveFromOtherActiveAssignments(unittest.TestCase):
    """Active assignment steals employees out of other Active assignments."""

    def test_employee_removed_from_other_active_assignment(self):
        doc = _assignment(rows=[_row("HR-EMP-00001")])
        other = MagicMock()
        other.name = "ZK-SA-0002"
        empty_after_removal = []

        with patch.object(frappe, "msgprint") as mock_msg, \
                patch.object(frappe.db, "sql", return_value=[
                    frappe._dict({"name": "ZK-SA-0002", "shift_type": "Night"}),
                ]), \
                patch.object(frappe, "get_doc", return_value=other), \
                patch.object(frappe, "delete_doc") as mock_delete:
            # simulate row removal leaving the other assignment empty
            other.employees = []
            doc.validate()

        other.save.assert_not_called()
        mock_delete.assert_called_once_with(
            "ZK Shift Assignment", "ZK-SA-0002", ignore_permissions=True)
        mock_msg.assert_called_once()

    def test_other_assignment_saved_when_it_still_has_employees(self):
        doc = _assignment(rows=[_row("HR-EMP-00001")])
        other = MagicMock()
        other.name = "ZK-SA-0002"

        with patch.object(frappe, "msgprint"), \
                patch.object(frappe.db, "sql", return_value=[
                    frappe._dict({"name": "ZK-SA-0002", "shift_type": "Night"}),
                ]), \
                patch.object(frappe, "get_doc", return_value=other):
            other.employees = [_row("HR-EMP-00009")]
            doc.validate()

        other.save.assert_called_once_with(ignore_permissions=True)
        # cleanup save must skip the cross-assignment check
        self.assertTrue(other.flags.get("skip_duplicate_assignment_check"))

    def test_no_conflicts_no_message_and_no_writes(self):
        doc = _assignment(rows=[_row("HR-EMP-00001")])
        with patch.object(frappe, "msgprint") as mock_msg, \
                patch.object(frappe.db, "sql", return_value=[]), \
                patch.object(frappe, "get_doc") as mock_get_doc, \
                patch.object(frappe, "delete_doc") as mock_delete:
            doc.validate()

        mock_get_doc.assert_not_called()
        mock_delete.assert_not_called()
        mock_msg.assert_not_called()

    def test_inactive_assignment_does_not_touch_others(self):
        doc = _assignment(status="Inactive", rows=[_row("HR-EMP-00001")])
        with patch.object(frappe, "msgprint") as mock_msg, \
                patch.object(frappe.db, "sql", return_value=[]) as mock_sql, \
                patch.object(frappe, "get_doc") as mock_get_doc:
            doc.validate()

        mock_sql.assert_not_called()
        mock_get_doc.assert_not_called()
        mock_msg.assert_not_called()

    def test_self_is_excluded_from_conflict_query(self):
        doc = _assignment(name="ZK-SA-0001", rows=[_row("HR-EMP-00001")])
        with patch.object(frappe, "msgprint"), \
                patch.object(frappe.db, "sql", return_value=[]) as mock_sql:
            doc.validate()

        (sql, params), kwargs = mock_sql.call_args
        self.assertIn("sa.name != %s", sql)
        # params are (employee, self.name) — the doc itself is excluded
        self.assertEqual(params, ("HR-EMP-00001", "ZK-SA-0001"))


if __name__ == "__main__":
    unittest.main()
