// Attendance Summary Form JS
frappe.ui.form.on("Attendance Summary", {

    refresh(frm) {
        frm.trigger("set_status_indicator");

        if (frm.doc.status === "Draft" || frm.doc.status === "Processing") {
            // ── Fetch Employees ──────────────────────────────────────────
            frm.add_custom_button(__("Fetch Employees"), function () {
                if (!frm.doc.from_date || !frm.doc.to_date) {
                    frappe.msgprint(__("Please set From Date and To Date first."));
                    return;
                }
                if (!frm.doc.company) {
                    frappe.msgprint(__("Please select a Company first."));
                    return;
                }
                frm.trigger("show_fetch_dialog");
            }, __("Actions"));
        }

        if (frm.doc.details && frm.doc.details.length > 0) {
            // ── Process Attendance ───────────────────────────────────────
            frm.add_custom_button(__("Process Attendance"), function () {
                const processable = (frm.doc.details || []).filter(r => !r.do_not_process).length;
                frappe.confirm(
                    __("Process attendance for <b>{0}</b> employees from <b>{1}</b> to <b>{2}</b>? This will also calculate overtime where enabled on the shift.",
                        [processable,
                        frappe.datetime.str_to_user(frm.doc.from_date),
                        frappe.datetime.str_to_user(frm.doc.to_date)]),
                    function () {
                        frm.call({
                            doc: frm.doc,
                            method: "process_attendance",
                            freeze: true,
                            freeze_message: __("Queuing attendance processing..."),
                            callback(r) {
                                if (r.message) {
                                    frappe.show_alert({
                                        message: r.message.message || __("Processing started."),
                                        indicator: "blue"
                                    }, 8);
                                    // Poll for completion
                                    frm.trigger("poll_status");
                                }
                            }
                        });
                    }
                );
            }, __("Actions"));

            // ── View Daily Checkins ────────────────────────────────────────
            frm.add_custom_button(__("View Daily Checkins"), function () {
                frappe.set_route("zk-daily-checkins", frm.doc.name);
            }, __("View"));

            // ── Add Manual Check-in (requires Checkin Editor role) ────────
            if (frappe.user_roles.includes("Checkin Editor")) {
                frm.add_custom_button(__("Add Check-in"), function () {
                    frm.trigger("show_manual_checkin_dialog");
                }, __("Actions"));
            }
        }

        // ── Overtime summary indicator ─────────────────────────────────────
        if (frm.doc.status === "Completed" && frm.doc.total_overtime_hours) {
            const parts = [
                [__("Day"),     frm.doc.total_day_ot_hours],
                [__("Night"),   frm.doc.total_night_ot_hours],
                [__("Weekend"), frm.doc.total_weekend_ot_hours],
                [__("Holiday"), frm.doc.total_holiday_ot_hours],
            ].filter(([, v]) => v).map(([k, v]) => `${k}: ${v} hrs`).join(" | ");

            frm.dashboard.add_comment(
                __("Total Overtime: <b>{0} hrs</b> across {1} employee(s).{2}",
                    [frm.doc.total_overtime_hours, frm.doc.total_employees || 0,
                     parts ? `<br>${parts}` : ""]),
                "orange",
                true
            );
        }
    },

    from_date(frm) {
        if (frm.doc.from_date) {
            let d = frappe.datetime.str_to_obj(frm.doc.from_date);
            frm.set_value("year", d.getFullYear());
            frm.set_value("month", [
                "January","February","March","April","May","June",
                "July","August","September","October","November","December"
            ][d.getMonth()]);

            let last = new Date(d.getFullYear(), d.getMonth() + 1, 0);
            frm.set_value("to_date", frappe.datetime.obj_to_str(last));
        }
    },

    set_status_indicator(frm) {
        const colors = { "Draft": "gray", "Processing": "orange", "Completed": "green" };
        frm.page.set_indicator(frm.doc.status, colors[frm.doc.status] || "gray");
    },

    poll_status(frm) {
        // Check every 4 seconds if processing is done
        let polls = 0;
        const interval = setInterval(function () {
            polls++;
            frappe.db.get_value("Attendance Summary", frm.doc.name, "status").then(r => {
                if (r.message.status === "Completed") {
                    clearInterval(interval);
                    frappe.show_alert({ message: __("Processing complete!"), indicator: "green" }, 5);
                    frm.reload_doc();
                } else if (polls > 60) {
                    // Timeout after ~4 minutes
                    clearInterval(interval);
                    frm.reload_doc();
                }
            });
        }, 4000);
    },

    show_manual_checkin_dialog(frm) {
        const employee_options = (frm.doc.details || []).map(r =>
            `${r.employee} — ${r.employee_name || ""}`
        );
        const employee_map = {};
        (frm.doc.details || []).forEach(r => { employee_map[r.employee] = r.employee_name; });
        const emp_keys = Object.keys(employee_map);

        // ── Rich shift card (same design as zk_daily_checkins) ────────
        function render_shift_html($wrapper, s) {
            if (!s) {
                $wrapper.html(
                    `<div class="zk-shift-empty" style="padding:10px 0 8px;">
                        <span class="text-muted" style="font-size:0.82rem;">${__("No shift assigned")}</span>
                    </div>`
                );
                return;
            }
            const nightBadge = s.is_night_shift
                ? `<span style="display:inline-flex;align-items:center;gap:3px;background:#fff3cd;color:#856404;border:1px solid #ffc107;border-radius:20px;padding:2px 10px;font-size:0.7rem;font-weight:600;margin-left:8px;"><i class="fa fa-moon-o"></i>${__("Night")}</span>`
                : "";
            const lunchHtml = s.lunch_break_hours
                ? `<div style="display:flex;align-items:center;gap:6px;">
                        <div style="width:6px;height:6px;border-radius:50%;background:#e0a800;flex-shrink:0;"></div>
                        <span style="font-size:0.78rem;color:var(--text-muted);">${__("Lunch")}</span>
                        <span style="font-size:0.82rem;font-weight:600;">${s.lunch_break_hours}h</span>
                   </div>`
                : "";
            const otHtml = s.enable_overtime
                ? `<div style="display:flex;align-items:center;gap:6px;">
                        <div style="width:6px;height:6px;border-radius:50%;background:var(--green-500);flex-shrink:0;"></div>
                        <span style="font-size:0.78rem;color:var(--text-muted);">${__("Overtime")}</span>
                        <span style="font-size:0.78rem;font-weight:600;color:var(--green-600);">${s.overtime_calculation_method}</span>
                   </div>`
                : `<div style="display:flex;align-items:center;gap:6px;">
                        <div style="width:6px;height:6px;border-radius:50%;background:var(--text-muted);flex-shrink:0;"></div>
                        <span style="font-size:0.78rem;color:var(--text-muted);">${__("Overtime")}</span>
                        <span style="font-size:0.78rem;color:var(--text-muted);">${__("Disabled")}</span>
                   </div>`;
            const satLabel = s.saturday_mode === "Half Day"
                ? `${s.saturday_mode} (${s.saturday_half_day_hours}h)`
                : s.saturday_mode;
            $wrapper.html(`
                <div class="zk-shift-card" style="background:var(--card-bg);border:1px solid var(--border-color);border-radius:10px;padding:14px 16px;margin-bottom:10px;">
                    <!-- Header -->
                    <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;padding-bottom:10px;border-bottom:1px solid var(--border-color);">
                        <div style="display:flex;align-items:center;justify-content:center;width:32px;height:32px;border-radius:8px;background:linear-gradient(135deg,#4facfe 0%,#00f2fe 100%);color:#fff;font-size:0.85rem;flex-shrink:0;">
                            <i class="fa fa-clock-o"></i>
                        </div>
                        <div style="display:flex;align-items:center;flex-wrap:wrap;gap:4px;">
                            <span style="font-weight:700;font-size:0.88rem;color:var(--text-color);">${frappe.utils.escape_html(s.name)}</span>
                            ${nightBadge}
                        </div>
                    </div>
                    <!-- Grid -->
                    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px 16px;">
                        <!-- Shift Time -->
                        <div style="display:flex;align-items:center;gap:8px;">
                            <div style="width:28px;height:28px;border-radius:6px;background:#e8f5e9;display:flex;align-items:center;justify-content:center;flex-shrink:0;">
                                <i class="fa fa-long-arrow-right" style="color:#2e7d32;font-size:0.75rem;"></i>
                            </div>
                            <div>
                                <div style="font-size:0.68rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;">${__("Time")}</div>
                                <div style="font-size:0.82rem;font-weight:600;">${s.start_time} – ${s.end_time}</div>
                            </div>
                        </div>
                        <!-- Full Day -->
                        <div style="display:flex;align-items:center;gap:8px;">
                            <div style="width:28px;height:28px;border-radius:6px;background:#e3f2fd;display:flex;align-items:center;justify-content:center;flex-shrink:0;">
                                <i class="fa fa-sun-o" style="color:#1565c0;font-size:0.75rem;"></i>
                            </div>
                            <div>
                                <div style="font-size:0.68rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;">${__("Full Day")}</div>
                                <div style="font-size:0.82rem;font-weight:600;">${s.full_day_hours}h</div>
                            </div>
                        </div>
                        <!-- Half Day -->
                        <div style="display:flex;align-items:center;gap:8px;">
                            <div style="width:28px;height:28px;border-radius:6px;background:#fce4ec;display:flex;align-items:center;justify-content:center;flex-shrink:0;">
                                <i class="fa fa-adjust" style="color:#c62828;font-size:0.75rem;"></i>
                            </div>
                            <div>
                                <div style="font-size:0.68rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;">${__("Half Day")}</div>
                                <div style="font-size:0.82rem;font-weight:600;">${s.half_day_hours}h</div>
                            </div>
                        </div>
                        <!-- Standard Hours -->
                        <div style="display:flex;align-items:center;gap:8px;">
                            <div style="width:28px;height:28px;border-radius:6px;background:#f3e5f5;display:flex;align-items:center;justify-content:center;flex-shrink:0;">
                                <i class="fa fa-bullseye" style="color:#6a1b9a;font-size:0.75rem;"></i>
                            </div>
                            <div>
                                <div style="font-size:0.68rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;">${__("Standard")}</div>
                                <div style="font-size:0.82rem;font-weight:600;">${s.standard_working_hours}h</div>
                            </div>
                        </div>
                        <!-- Saturday Mode -->
                        <div style="display:flex;align-items:center;gap:8px;">
                            <div style="width:28px;height:28px;border-radius:6px;background:#fff8e1;display:flex;align-items:center;justify-content:center;flex-shrink:0;">
                                <i class="fa fa-calendar-check-o" style="color:#f57f17;font-size:0.75rem;"></i>
                            </div>
                            <div>
                                <div style="font-size:0.68rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;">${__("Saturday")}</div>
                                <div style="font-size:0.82rem;font-weight:600;">${satLabel}</div>
                            </div>
                        </div>
                        <!-- Lunch + OT (stacked in one cell) -->
                        <div style="display:flex;flex-direction:column;gap:6px;justify-content:center;">
                            ${lunchHtml}
                            ${otHtml}
                        </div>
                    </div>
                </div>`);
        }

        function fetch_shift_info(dlg, employee, work_date) {
            if (!employee) {
                dlg.fields_dict.shift_info.$wrapper.html("");
                return;
            }
            frappe.call({
                method: "zkteco_attendance.zkteco_attendance.api.endpoints.get_employee_shift_info",
                args: { employee: employee, work_date: work_date || frm.doc.from_date },
                callback(r) {
                    render_shift_html(dlg.fields_dict.shift_info.$wrapper, r.message);
                },
            });
        }

        const d = new frappe.ui.Dialog({
            title: __("Add Manual Check-in"),
            size: "large",
            fields: [
                { fieldtype: "HTML", fieldname: "shift_info" },
                { fieldtype: "Section Break", fieldname: "sec" },
                {
                    fieldtype: "Link",
                    fieldname: "employee",
                    label: __("Employee"),
                    options: "Employee",
                    get_query() {
                        return { filters: { name: ["in", emp_keys] } };
                    },
                    reqd: 1,
                    onchange() {
                        fetch_shift_info(d, d.get_value("employee"), d.get_value("checkin_date"));
                    },
                },
                { fieldtype: "Column Break" },
                {
                    fieldtype: "Select",
                    fieldname: "log_type",
                    label: __("Log Type"),
                    options: "IN\nOUT",
                    default: "IN",
                    reqd: 1,
                },
                { fieldtype: "Section Break" },
                {
                    fieldtype: "Date",
                    fieldname: "checkin_date",
                    label: __("Date"),
                    default: frm.doc.from_date,
                    reqd: 1,
                },
                { fieldtype: "Column Break" },
                {
                    fieldtype: "Time",
                    fieldname: "checkin_time",
                    label: __("Time"),
                    default: "08:00:00",
                    reqd: 1,
                },
                { fieldtype: "Section Break" },
                {
                    fieldtype: "Check",
                    fieldname: "is_overtime",
                    label: __("Is Overtime"),
                    default: 0,
                    description: __("Mark this punch as an overtime punch"),
                },
            ],
            primary_action_label: __("Create Request"),
            primary_action(vals) {
                if (!vals.employee || !vals.checkin_date || !vals.checkin_time) {
                    frappe.msgprint(__("All fields are required."));
                    return;
                }
                // Create a Manual Checkin Request instead of touching the
                // checkin directly — the checkin is applied when the request
                // document is submitted.
                frappe.call({
                    method: "zkteco_attendance.zkteco_attendance.api.endpoints.create_manual_checkin_request",
                    args: {
                        employee:           vals.employee,
                        checkin_date:       vals.checkin_date,
                        checkin_time:       vals.checkin_time,
                        log_type:           vals.log_type,
                        is_overtime:        vals.is_overtime ? 1 : 0,
                        attendance_summary: frm.doc.name,
                    },
                    freeze: true,
                    freeze_message: __("Creating request…"),
                    callback(r) {
                        d.hide();
                        if (r.message && r.message.name) {
                            frappe.show_alert({
                                message: __("Manual Check-in Request {0} created. Submit it to apply the check-in.", [r.message.name]),
                                indicator: "blue",
                            }, 6);
                            frappe.set_route("Form", "Manual Checkin Request", r.message.name);
                        }
                    },
                });
            },
            on_show() {
                // Auto-fetch shift info if an employee is pre-selected
                const emp = d.get_value("employee");
                if (emp) {
                    fetch_shift_info(d, emp, d.get_value("checkin_date"));
                }
            },
        });
        d.show();
    },

    show_fetch_dialog(frm) {
        const d = new frappe.ui.Dialog({
            title: __("Fetch Employees"),
            fields: [
                { 
                    label: __("Include Employees with only attendance id and biometric device"), 
                    fieldname: "include_only_id_and_device", 
                    fieldtype: "Check",
                    default: 0,
                    description: __("If checked, employees without a biometric device or attendance device id will not fetched in the summary. Otherwise, they will be fetched and marked as 'Do Not Process'.")
                },
                {
                    label: __("Filter By"),
                    fieldname: "filter_by",
                    fieldtype: "Select",
                    options: ["All Active Employees", "Department", "Designation", "Project"],
                    default: "All Active Employees",
                    onchange() {
                        const v = d.get_value("filter_by");
                        d.set_df_property("department", "hidden", v !== "Department");
                        d.set_df_property("designation", "hidden", v !== "Designation");
                        d.set_df_property("project", "hidden", v !== "Project");
                    }
                },
                { label: __("Department"), fieldname: "department", fieldtype: "Link", options: "Department", hidden: 1 },
                { label: __("Designation"), fieldname: "designation", fieldtype: "Link", options: "Designation", hidden: 1 },
                { label: __("Project"), fieldname: "project", fieldtype: "Link", options: "Project", hidden: 1 },
            ],
            primary_action_label: __("Fetch"),
            primary_action(values) {
                const filters = {
                    company: frm.doc.company,
                    status: "Active",
                };
                if (values.filter_by === "Department" && values.department) {
                    filters.department = values.department;
                }
                if (values.filter_by === "Designation" && values.designation) {
                    filters.designation = values.designation;
                }
                if (values.filter_by === "Project" && values.project) {
                    filters.project = values.project;
                }

                // include_only_id_and_device: when checked, only employees
                // mapped to a biometric device (zk_biometric_device AND
                // attendance_device_id both set) are fetched at all. When
                // unchecked, everyone is fetched and unmapped employees are
                // added as "Do Not Process" (see below).
                if (values.include_only_id_and_device) {
                    filters["zk_biometric_device"] = ["is", "set"];
                    filters["attendance_device_id"] = ["is", "set"];
                }

                frappe.call({
                    method: "frappe.client.get_list",
                    args: {
                        doctype: "Employee",
                        filters: filters,
                        fields: ["name", "employee_name", "department", "designation", "zk_biometric_device", "attendance_device_id"],
                        limit_page_length: 5000,
                        order_by: "employee_name asc",
                    },
                    freeze: true,
                    freeze_message: __("Fetching employees..."),
                    callback(r) {
                        d.hide();

                        const emps = r.message || [];
                        if (!emps.length) {
                            frappe.msgprint(__("No employees found matching those filters."));
                            return;
                        }

                        const existing = new Set((frm.doc.details || []).map(row => row.employee));
                        let added = 0;

                        const working_days = frappe.datetime.get_diff(frm.doc.to_date, frm.doc.from_date) + 1;

                        emps.forEach(emp => {
                            // is zk_biometric_device and attendance_device_id are empty
                            if (!existing.has(emp.name)) {
                                frm.add_child("details", {
                                    employee: emp.name,
                                    employee_name: emp.employee_name,
                                    department: emp.department,
                                    designation: emp.designation,
                                    do_not_process: emp.zk_biometric_device && emp.attendance_device_id ? 0 : 1,
                                    working_days: emp.zk_biometric_device && emp.attendance_device_id ? working_days : 0,
                                });
                                existing.add(emp.name);
                                added++;
                            }
                        });

                        frm.refresh_field("details");

                        frm.doc.total_employees = frm.doc.details.length;
                        frm.refresh_field("total_employees");

                        frappe.show_alert({
                            message: __("{0} employee(s) added. Total: {1}", [added, frm.doc.details.length]),
                            indicator: "green"
                        }, 5);
                    }
                });
            }
        });
        d.show();
    }
});
