// ZK Shift Assignment Form JS
frappe.ui.form.on("ZK Shift Assignment", {

    refresh(frm) {
        
            frm.add_custom_button(__("Add Employees by Filter"), function () {
                frm.trigger("show_bulk_add_dialog");
            }, __("Actions"));
        
    },

    show_bulk_add_dialog(frm) {
        const d = new frappe.ui.Dialog({
            title: __("Bulk Add Employees"),
            fields: [
                { label: __("Department"),       fieldname: "department",      fieldtype: "Link", options: "Department" },
                { label: __("Project"),          fieldname: "project",         fieldtype: "Link", options: "Project" },
                { label: __("Job Title"),        fieldname: "job_title",       fieldtype: "Link", options: "Designation" },
            ],
            primary_action_label: __("Fetch & Add"),
            primary_action(values) {
                d.hide();
                frappe.call({
                    method: "zkteco_attendance.zkteco_attendance.doctype.zk_shift_assignment.zk_shift_assignment.get_employees_for_assignment",
                    args: {
                        company:         frm.doc.company,
                        department:      values.department      || null,
                        project:          values.project          || null,
                        job_title:       values.job_title || null,
                    },
                    callback(r) {
                        if (!r.message || !r.message.length) {
                            frappe.msgprint(__("No employees found matching those filters."));
                            return;
                        }
                        const existing = new Set(
                            (frm.doc.employees || []).map(e => e.employee)
                        );
                        let added = 0;
                        r.message.forEach(emp => {
                            if (!existing.has(emp.employee)) {
                                frm.add_child("employees", {
                                    employee:      emp.employee,
                                    employee_name: emp.employee_name,
                                    department:    emp.department,
                                    designation:   emp.designation,
                                });
                                added++;
                            }
                        });
                        frm.refresh_field("employees");
                        frappe.show_alert({
                            message: __("{0} employee(s) added.", [added]),
                            indicator: "green"
                        }, 4);
                    }
                });
            }
        });
        d.show();
    }
});
