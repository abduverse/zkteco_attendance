import frappe
from frappe import _
from frappe.model.document import Document


class ZKShiftAssignment(Document):

    def validate(self):
        self._validate_company()
        self._remove_duplicate_employee_rows()
        if not self.flags.get("skip_duplicate_assignment_check"):
            self._remove_duplicates_from_other_active_assignments()

    def _validate_company(self):
        # Company is optional when 'Ignore Company Restriction' is enabled.
        if not self.ignore_company_restriction and not self.company:
            frappe.throw(
                _("Company is required unless 'Ignore Company Restriction' is enabled.")
            )

    def _remove_duplicate_employee_rows(self):
        """Drop repeated rows for the same employee within this assignment.

        The first row for an employee is kept, later duplicates are removed,
        so the Employees table never lists one employee twice.
        """
        seen = set()
        duplicates = []
        for row in list(self.employees or []):
            if not row.employee:
                continue
            if row.employee in seen:
                duplicates.append(row)
            else:
                seen.add(row.employee)

        for row in duplicates:
            self.remove(row)

        if duplicates:
            frappe.msgprint(
                _("Duplicate employee rows were removed from this assignment: {0}").format(
                    ", ".join(row.employee for row in duplicates)
                ),
                alert=True,
                indicator="orange",
            )

    def _remove_duplicates_from_other_active_assignments(self):
        """Ensure each employee in this Active assignment has no other Active
        ZK Shift Assignment.

        Instead of raising a validation error, employees listed here are
        removed from any other Active assignment (that assignment is deleted
        when it ends up without employees), so saving this assignment always
        wins and no duplicate active assignments linger.
        """
        if self.status != "Active":
            return

        removed = []
        for row in list(self.employees or []):
            if not row.employee:
                continue
            conflicts = frappe.db.sql("""
                SELECT sa.name, sa.shift_type
                FROM `tabZK Shift Assignment` sa
                JOIN `tabZK Shift Assignment Employee` sae ON sae.parent = sa.name
                WHERE sae.employee = %s
                  AND sa.name != %s
                  AND sa.status = 'Active'
            """, (row.employee, self.name or "NEW"), as_dict=True)

            for conflict in conflicts:
                self._remove_employee_from_assignment(conflict.name, row.employee)
                removed.append((row.employee, conflict.shift_type or conflict.name))

        if removed:
            frappe.msgprint(
                _("Removed duplicate shift assignments: {0}").format(
                    ", ".join(f"{emp} (was in {shift})" for emp, shift in removed)
                ),
                alert=True,
                indicator="orange",
            )

    def _remove_employee_from_assignment(self, assignment, employee):
        """Drop `employee` from `assignment` (another ZK Shift Assignment);
        delete the assignment when no employees remain, mirroring how the
        device-mapping dialog manages assignments. The cleanup save is
        flagged so it does not trigger further cross-assignment removals.
        """
        other = frappe.get_doc("ZK Shift Assignment", assignment)
        for other_row in list(other.employees or []):
            if other_row.employee == employee:
                other.remove(other_row)

        if other.employees:
            other.flags.skip_duplicate_assignment_check = True
            other.save(ignore_permissions=True)
        else:
            frappe.delete_doc(
                "ZK Shift Assignment", assignment, ignore_permissions=True
            )


@frappe.whitelist()
def get_employees_for_assignment(department=None, project=None, job_title=None, company=None):
    """Fetch active employees matching optional filters for bulk-add to shift assignment."""
    filters = {"status": "Active"}
    if company:        filters["company"]         = company
    if department:     filters["department"]      = department
    if project:         filters["project"]          = project
    if job_title:      filters["designation"]       = job_title

    return frappe.get_all(
        "Employee",
        filters=filters,
        fields=["name as employee", "employee_name", "department", "designation"],
        order_by="employee_name asc"
    )
