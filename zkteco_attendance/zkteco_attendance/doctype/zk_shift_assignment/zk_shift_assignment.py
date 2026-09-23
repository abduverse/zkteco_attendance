import frappe
from frappe import _
from frappe.model.document import Document


class ZKShiftAssignment(Document):

    def validate(self):
        self._check_duplicate_assignments()

    def _check_duplicate_assignments(self):
        """Prevent an employee from having two active shift assignments."""
        duplicates = []
        for row in self.employees:
            if not row.employee:
                continue
            conflict = frappe.db.sql("""
                SELECT sa.name, sa.shift_type
                FROM `tabZK Shift Assignment` sa
                JOIN `tabZK Shift Assignment Employee` sae ON sae.parent = sa.name
                WHERE sae.employee = %s
                  AND sa.name != %s
                  AND sa.status = 'Active'
            """, (row.employee, self.name or "NEW"), as_dict=True)

            if conflict:
                duplicates.append((row.employee, conflict[0].shift_type))

        if duplicates:
            frappe.throw(
                _("The following employees already have an active shift assignment: {0}").format(
                    ", ".join([f"{emp} ({shift})" for emp, shift in duplicates])
                ),
                alert=True, indicator="orange"
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
