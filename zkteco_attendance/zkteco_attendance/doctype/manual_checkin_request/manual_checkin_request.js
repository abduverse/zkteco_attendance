// Manual Checkin Request Form JS
frappe.ui.form.on("Manual Checkin Request", {

    request_type(frm) {
        // A "New" request must not reference an existing check-in
        if (frm.doc.request_type === "New" && frm.doc.checkin_name) {
            frm.set_value("checkin_name", "");
        }
    },

    refresh(frm) {
        if (frm.doc.docstatus === 0) {
            frm.dashboard.add_comment(
                __("This is a draft request. <b>Submit</b> it to create or update the employee check-in."),
                "blue",
                true
            );
        } else if (frm.doc.docstatus === 1) {
            if (frm.doc.applied_checkin) {
                frm.dashboard.add_comment(
                    __("Check-in <b>{0}</b> has been applied to the attendance records.", [frm.doc.applied_checkin]),
                    "green",
                    true
                );
            }
            if (frm.doc.original_checkin_data) {
                frm.dashboard.add_comment(
                    __("Cancelling this request will restore the check-in to its original values."),
                    "orange",
                    true
                );
            } else if (frm.doc.applied_checkin) {
                frm.dashboard.add_comment(
                    __("Cancelling this request will delete the check-in it created."),
                    "orange",
                    true
                );
            }
        } else if (frm.doc.docstatus === 2) {
            frm.dashboard.add_comment(
                __("This request was cancelled; the employee check-in has been reverted."),
                "red",
                true
            );
        }
    }

});