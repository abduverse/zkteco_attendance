// ZKTeco Attendance — Biometric Device Form JS
// Compatible with Frappe v14 / v15 / v16

frappe.ui.form.on("Biometric Device", {
    refresh(frm) {
        frm.disable_save();  // re-enable
        frm.enable_save();

        // ── Test Connection ──────────────────────────────────────────────
        frm.add_custom_button(__("Test Connection"), function () {
            if (!frm.doc.device_ip) {
                frappe.msgprint(__("Please enter a Device IP first."));
                return;
            }
            frm.call({
                method: "zkteco_attendance.zkteco_attendance.api.endpoints.test_connection",
                args: { device_name: frm.doc.name },
                freeze: true,
                freeze_message: __("Connecting to device…"),
                callback(r) {
                    if (r.message && r.message.success) {
                        const d = r.message;
                        frappe.msgprint({
                            title: __("Connection Successful ✓"),
                            indicator: "green",
                            message: `
                                <table class="table table-bordered" style="margin-top:8px">
                                    <tr><td><b>${__("Serial Number")}</b></td><td>${d.serial_number || "—"}</td></tr>
                                    <tr><td><b>${__("Firmware")}</b></td><td>${d.firmware_version || "—"}</td></tr>
                                    <tr><td><b>${__("Device Time")}</b></td><td>${d.device_time || "—"}</td></tr>
                                    <tr><td><b>${__("Enrolled Users")}</b></td><td>${d.enrolled_users}</td></tr>
                                    <tr><td><b>${__("Attendance Logs")}</b></td><td>${d.attendance_logs}</td></tr>
                                </table>`,
                        });
                        frm.reload_doc();
                    } else {
                        frappe.msgprint({
                            title: __("Connection Failed"),
                            indicator: "red",
                            message: (r.message && r.message.error) || __("Unknown error"),
                        });
                        frm.reload_doc();
                    }
                },
            });
        }, __("Actions"));

        // ── Pull Checkins (with live progress + results) ──────────────────
        frm.add_custom_button(__("Pull Checkins"), function () {
            frm.trigger("pull_checkins_with_progress");
        }, __("Actions"));

        // ── View Sync Logs ───────────────────────────────────────────────
        frm.add_custom_button(__("View Sync Logs"), function () {
            frappe.set_route("List", "Attendance Sync Log", { device: frm.doc.name });
        }, __("Actions"));

        // ── Browse Employees On Device (map device users to Employees) ───
        frm.add_custom_button(__("Browse Employees On Device"), function () {
            frm.trigger("browse_employees_on_device");
        }, __("Actions"));

        // ── Status badge ────────────────────────────────────────────────
        const statusColor = { "Active": "green", "Inactive": "red" };
        frm.page.set_indicator(
            frm.doc.status,
            statusColor[frm.doc.status] || "orange"
        );

        // ── Last sync info ───────────────────────────────────────────────
        if (frm.doc.last_sync_time) {
            frm.dashboard.add_comment(
                __("Last successful sync: <b>{0}</b>",
                    [frappe.datetime.str_to_user(frm.doc.last_sync_time)]),
                "blue",
                true
            );
        } else {
            frm.dashboard.add_comment(__("No sync has been performed yet."), "orange", true);
        }
    },

    browse_employees_on_device(frm) {
        if (!frm.doc.device_ip) {
            frappe.msgprint(__("Please save the Device IP first."));
            return;
        }
        if (!frm.doc.enable) {
            frappe.msgprint({
                title: __("Device Not Enabled"),
                indicator: "red",
                message: __("This device is not enabled. Please enable it first."),
            });
            return;
        }

        frappe.call({
            method: "zkteco_attendance.zkteco_attendance.api.endpoints.get_device_users",
            args: { device_name: frm.doc.name },
            freeze: true,
            freeze_message: __("Fetching employees from device…"),
            callback(r) {
                const res = r.message || {};
                if (!res.success) {
                    frappe.msgprint({
                        title: __("Fetch Failed"),
                        indicator: "red",
                        message: res.error || __("Could not fetch users from the device."),
                    });
                    return;
                }
                showEmployeeMappingDialog(frm, res);
            },
        });
    },

    pull_checkins_with_progress(frm) {
        if (!frm.doc.enable) {
            frappe.msgprint({
                title: __("Device Not Enabled"),
                indicator: "red",
                message: __("This device is not enabled. Please enable it first."),
            });
            return;
        }

        frappe.confirm(
            __("This will connect to <b>{0}</b> now, pull attendance logs, and create Employee Checkin records. This may take a little while for devices with many logs. Continue?",
                [frm.doc.device_name]),
            function () {
                frm.trigger("_run_pull_checkins");
            }
        );
    },

    _run_pull_checkins(frm) {
        // Unique token for this pull run — echoed back in every progress
        // payload so stale progress from a previous run is never applied.
        const run_id = "zk_pull_" + Date.now() + "_" + Math.random().toString(36).slice(2, 10);

        // ── Build a progress dialog ────────────────────────────────────────
        const dialog = new frappe.ui.Dialog({
            title: __("Pulling Check-ins from {0}", [frm.doc.device_name]),
            size: "large",
            fields: [
                {
                    fieldtype: "HTML",
                    fieldname: "progress_html",
                },
            ],
        });

        const $body = dialog.fields_dict.progress_html.$wrapper;
        $body.html(`
            <div class="zkteco-pull-progress">
                <div class="zkteco-pull-stage text-muted" style="margin-bottom:8px;">
                    ${__("Starting…")}
                </div>
                <div class="progress" style="height:18px;">
                    <div class="progress-bar progress-bar-striped active zkteco-pull-bar"
                         role="progressbar" style="width:5%;">
                    </div>
                </div>
                <div class="zkteco-pull-counts" style="margin-top:14px; display:none;">
                    <table class="table table-bordered table-sm" style="margin-bottom:0;">
                        <tr>
                            <td>${__("Total Pulled")}</td><td class="text-right zkteco-cnt-total">0</td>
                            <td>${__("New")}</td><td class="text-right text-success zkteco-cnt-new">0</td>
                        </tr>
                        <tr>
                            <td>${__("Duplicates")}</td><td class="text-right zkteco-cnt-dupes">0</td>
                            <td>${__("Failed")}</td><td class="text-right text-danger zkteco-cnt-failed">0</td>
                        </tr>
                        <tr>
                            <td>${__("Double Punches")}</td><td class="text-right text-muted zkteco-cnt-dp">0</td>
                            <td>${__("Overtime Punches")}</td><td class="text-right text-warning zkteco-cnt-ot">0</td>
                        </tr>
                        <tr>
                            <td colspan="2">${__("Status")}</td><td colspan="2" class="text-right zkteco-cnt-status">—</td>
                        </tr>
                    </table>
                </div>
                <div class="zkteco-pull-errors text-danger" style="margin-top:10px; display:none; max-height:150px; overflow:auto; font-size:12px;"></div>
            </div>
        `);

        dialog.show();
        dialog.get_close_btn().hide();

        const $stage  = $body.find(".zkteco-pull-stage");
        const $bar    = $body.find(".zkteco-pull-bar");
        const $counts = $body.find(".zkteco-pull-counts");
        const $errors = $body.find(".zkteco-pull-errors");

        const setProgress = (pct, text) => {
            pct = Math.max(0, Math.min(100, pct));
            $bar.css("width", pct + "%");
            if (text) $stage.text(text);
        };

        // ── Render the final result (from the cached job result) ──────────
        const finishPull = (res) => {
            cleanup();

            if (!res || !res.success) {
                setProgress(100, __("Pull failed."));
                $stage.removeClass("text-muted").addClass("text-danger");
                $errors.show().text((res && res.error) || __("Unknown error"));
                dialog.get_close_btn().show();
                frm.reload_doc();
                return;
            }

            setProgress(100, __("Pull completed."));
            $counts.show();
            $body.find(".zkteco-cnt-total").text(res.total_records ?? 0);
            $body.find(".zkteco-cnt-new").text(res.new_records ?? 0);
            $body.find(".zkteco-cnt-dupes").text(res.duplicates ?? 0);
            $body.find(".zkteco-cnt-failed").text(res.failed ?? 0);
            $body.find(".zkteco-cnt-ot").text(res.overtime_records ?? 0);
            $body.find(".zkteco-cnt-dp").text(res.double_punches ?? 0);

            const statusColors = { Success: "text-success", Partial: "text-warning", Failed: "text-danger" };
            $body.find(".zkteco-cnt-status")
                .removeClass("text-success text-warning text-danger")
                .addClass(statusColors[res.sync_status] || "")
                .text(res.sync_status || "—");

            if (res.errors && res.errors.length) {
                $errors.show().html(
                    "<b>" + __("Issues") + ":</b><br>" +
                    res.errors.map(e => frappe.utils.escape_html(e)).join("<br>")
                );
            }

            frappe.show_alert({
                message: __("Pulled {0} record(s): {1} new, {2} duplicate(s), {3} failed.",
                    [res.total_records, res.new_records, res.duplicates, res.failed]),
                indicator: res.sync_status === "Success" ? "green" : (res.sync_status === "Partial" ? "orange" : "red"),
            }, 8);

            dialog.get_close_btn().show();
            frm.reload_doc();
        };

        // ── Subscribe to realtime progress events ─────────────────────────
        const handler = (data) => {
            if (!data || data.device !== frm.doc.name) return;
            // Ignore progress belonging to a different pull run.
            if (data.run_id && data.run_id !== run_id) return;

            switch (data.stage) {
                case "queued":
                    setProgress(5, data.message || __("Sync job queued…"));
                    break;
                case "connecting":
                    setProgress(5, data.message);
                    break;
                case "fetching":
                    setProgress(10, data.message);
                    break;
                case "fetched":
                    setProgress(15, data.message);
                    break;
                case "processing_raw": {
                    const pct = data.total ? 15 + (data.current / data.total) * 35 : 15;
                    setProgress(pct, data.message);
                    break;
                }
                case "filtered":
                    setProgress(55, data.message);
                    break;
                case "deduped":
                    setProgress(55, data.message);
                    $counts.show();
                    $body.find(".zkteco-cnt-dp").text(data.double_punches ?? 0);
                    break;
                case "creating_checkins": {
                    const pct = data.total ? 55 + (data.current / data.total) * 40 : 55;
                    setProgress(pct, data.message);
                    $counts.show();
                    $body.find(".zkteco-cnt-new").text(data.new_records ?? 0);
                    $body.find(".zkteco-cnt-dupes").text(data.duplicates ?? 0);
                    $body.find(".zkteco-cnt-failed").text(data.failed ?? 0);
                    $body.find(".zkteco-cnt-ot").text(data.overtime_records ?? 0);
                    $body.find(".zkteco-cnt-dp").text(data.double_punches ?? 0);
                    $body.find(".zkteco-cnt-total").text(data.total ?? 0);
                    break;
                }
                case "done":
                    setProgress(100, data.message || __("Done."));
                    break;
                case "error":
                case "failed":
                    setProgress(100, data.message || __("Failed."));
                    $stage.removeClass("text-muted").addClass("text-danger");
                    break;
            }
        };

        // ── Subscribe to realtime progress events (fast path) ─────────────
        // Frappe delivers these over websocket/socket.io. When realtime is
        // unavailable (socket.io not running, proxy blocking websockets,
        // etc.) the events never reach the browser, so we ALSO poll the
        // server-side progress snapshot below as a fallback.
        let realtime_on = false;
        try {
            if (frappe.realtime && typeof frappe.realtime.on === "function") {
                frappe.realtime.on("zkteco_pull_progress", handler);
                realtime_on = true;
            }
        } catch (e) {
            realtime_on = false;
        }

        // ── Poll fallback for progress (works without realtime) ──────────
        let poll_timer = null;
        const stopPolling = () => {
            if (poll_timer) {
                clearInterval(poll_timer);
                poll_timer = null;
            }
        };
        const poll = () => {
            // If the dialog was dismissed while the pull is still running,
            // stop polling (the background job keeps running server-side).
            if (!dialog.$wrapper.is(":visible")) {
                stopPolling();
                return;
            }
            frappe.call({
                method: "zkteco_attendance.zkteco_attendance.api.endpoints.get_pull_progress",
                args: { device_name: frm.doc.name, run_id: run_id },
                callback(r) {
                    if (!r || !r.message) return;
                    handler(r.message);
                    // Background job finished — the payload carries the
                    // cached final result; finish the dialog from it.
                    if (r.message.result && (r.message.stage === "done" || r.message.stage === "failed")) {
                        stopPolling();
                        finishPull(r.message.result);
                    }
                },
            });
        };
        poll_timer = setInterval(poll, 1500);
        poll();  // poll immediately so the first stage shows without waiting

        const cleanup = () => {
            stopPolling();
            if (realtime_on) {
                try {
                    frappe.realtime.off("zkteco_pull_progress", handler);
                } catch (e) { /* ignore */ }
            }
        };

        // ── Queue the pull as a background job ─────────────────────────────
        // The sync runs in a background worker instead of inside this web
        // request, so gunicorn / reverse-proxy timeouts can no longer kill
        // long pulls ("timed out"). Progress and the final result arrive
        // through the polling loop above (and realtime events when
        // available); finishPull renders the outcome.
        frappe.call({
            method: "zkteco_attendance.zkteco_attendance.api.endpoints.start_pull_checkins",
            args: { device_name: frm.doc.name, run_id: run_id },
            callback(r) {
                if (!r.message || !r.message.success) {
                    cleanup();
                    setProgress(100, __("Could not start the pull."));
                    $stage.removeClass("text-muted").addClass("text-danger");
                    dialog.get_close_btn().show();
                    return;
                }
                // Queued — the user may dismiss the dialog; the job keeps
                // running and results also land in the Attendance Sync Log.
                dialog.get_close_btn().show();
            },
            error() {
                cleanup();
                setProgress(100, __("Could not start the pull."));
                $stage.removeClass("text-muted").addClass("text-danger");
                dialog.get_close_btn().show();
            },
        });
    },
});

// ─────────────────────────────────────────────────────────────────────────────
// Employee mapping dialog (file scope — not a form event handler)
// Shows every user enrolled on the device with a checkbox and an Employee
// Link control per row; "Map Selected Rows" writes attendance_device_id +
// zk_biometric_device onto the Employees of the ticked rows only. A search
// box on top filters the fetched device users by ID or name.
// ─────────────────────────────────────────────────────────────────────────────
function showEmployeeMappingDialog(frm, res) {
    const users = res.users || [];

    if (!users.length) {
        frappe.msgprint({
            title: __("No Users On Device"),
            indicator: "orange",
            message: __("No users are enrolled on this device. Enroll users on the device first, then browse again."),
        });
        return;
    }

    const dialog = new frappe.ui.Dialog({
        title: __("Employees On {0}", [frm.doc.device_name || frm.doc.name]),
        size: "extra-large",
        fields: [
            {
                fieldtype: "HTML",
                fieldname: "mapping_html",
            },
        ],
        primary_action_label: __("Map Selected Rows"),
        primary_action() {
            // Only rows with the checkbox ticked are submitted; every other
            // device user is left untouched on the server (the mapping
            // endpoint only touches the user_ids it receives).
            const mappings = [];
            dialog.$wrapper.find(".zk-emp-row").each(function () {
                const $row = $(this);
                if (!$row.find(".zk-emp-select").is(":checked")) return;
                const control = $row.data("zk-link-control");
                const employee = control ? (control.get_value() || "") : "";
                mappings.push({
                    user_id: $row.data("user-id"),
                    employee: employee,
                });
            });

            if (!mappings.length) {
                frappe.msgprint({
                    title: __("No Rows Selected"),
                    indicator: "orange",
                    message: __("Tick the checkbox of at least one row to map."),
                });
                return;
            }

            frappe.call({
                method: "zkteco_attendance.zkteco_attendance.api.endpoints.map_device_employees",
                args: {
                    device_name: frm.doc.name,
                    mappings: JSON.stringify(mappings),
                },
                freeze: true,
                freeze_message: __("Saving employee mappings…"),
                callback(r2) {
                    const out = r2.message || {};
                    if (out.success) {
                        dialog.hide();
                        frappe.show_alert({
                            message: __("{0} employee(s) mapped, {1} unmapped.", [out.mapped, out.unmapped]),
                            indicator: "green",
                        }, 6);
                    }
                    // failures are toasted by frappe.call's default error handler
                },
            });
        },
    });

    const $body = dialog.fields_dict.mapping_html.$wrapper;

    const rows_html = users.map(u => {
        const badge = u.employee
            ? `<span class="indicator green" title="${frappe.utils.escape_html(u.employee_name || u.employee)}"></span>`
            : `<span class="indicator orange" title="${__("Not mapped")}"></span>`;
        const emp_label = u.employee
            ? frappe.utils.escape_html(u.employee_name || u.employee)
            : "";
        const emp_label_name = u.employee
            ? frappe.utils.escape_html(u.fullname || u.first_name)
            : "";
        return `
            <tr class="zk-emp-row" data-user-id="${frappe.utils.escape_html(u.user_id)}">
                <td class="text-center" style="width:36px;">
                    <input type="checkbox" class="zk-emp-select" data-user-id="${frappe.utils.escape_html(u.user_id)}" />
                </td>
                <td class="text-center" style="width:36px;">${badge}</td>
                <td style="width:110px;"><b>${frappe.utils.escape_html(u.user_id)}</b></td>
                <td>${frappe.utils.escape_html(u.name || "—")}</td>
                <td class="text-muted" style="width:160px;">${emp_label}</td>
                <td class="text-muted" style="width:160px;">${emp_label_name}</td>
                <td style="min-width:240px;">
                    <div class="zk-emp-link-target"
                         data-user-id="${frappe.utils.escape_html(u.user_id)}"
                         data-current="${frappe.utils.escape_html(u.employee || "")}"></div>
                </td>
            </tr>`;
    }).join("");

    $body.html(`
        <div class="zk-emp-mapping">
            <div class="text-muted" style="margin-bottom:8px;">
                ${__("{0} user(s) enrolled on this device. Tick the rows to map, optionally use the search box to filter them, then click Map Selected Rows.", [users.length])}
            </div>
            <div class="zk-emp-search-row" style="margin-bottom:8px;">
                <input type="text"
                       class="form-control input-xs zk-emp-search"
                       placeholder="${__("Search device ID or name…")}"
                       autocomplete="off" />
            </div>
            <div style="max-height:420px; overflow:auto;">
                <table class="table table-bordered table-sm" style="margin-bottom:0;">
                    <thead>
                        <tr>
                            <th class="text-center" style="width:36px;">
                                <input type="checkbox" class="zk-emp-select-all"
                                       title="${__("Select visible rows")}" />
                            </th>
                            <th style="width:110px;">${__("Device ID")}</th>
                            <th>${__("Name On Device")}</th>
                            <th style="width:130px;">${__("Current Employee")}</th>
                            <th style="width:160px;">${__("Current Emp Name")}</th>
                            <th style="min-width:240px;">${__("Employee")}</th>
                        </tr>
                    </thead>
                    <tbody>${rows_html}</tbody>
                </table>
            </div>
        </div>
    `);

    // ── Search / filter the fetched device users ────────────────────────
    const $search = $body.find(".zk-emp-search");
    $search.on("input", function () {
        applyEmployeeFilter();
    });

    function applyEmployeeFilter() {
        const term = ($search.val() || "").trim().toLowerCase();
        $body.find(".zk-emp-row").each(function () {
            const $row = $(this);
            const text = ($row.text() || "").toLowerCase();
            $row.toggle(!term || text.indexOf(term) !== -1);
        });
        syncSelectAllCheckbox();
    }

    // ── Row / select-all checkboxes (map only the selected rows) ────────
    function syncSelectAllCheckbox() {
        const $visible = $body.find(".zk-emp-row").filter(":visible");
        const $checked = $visible.find(".zk-emp-select:checked");
        $body.find(".zk-emp-select-all").prop(
            "checked",
            $visible.length > 0 && $checked.length === $visible.length
        );
    }

    $body.on("change", ".zk-emp-select", syncSelectAllCheckbox);

    $body.on("change", ".zk-emp-select-all", function () {
        const checked = $(this).is(":checked");
        // Applies to visible rows only, so it respects the active search.
        $body.find(".zk-emp-row").filter(":visible").each(function () {
            $(this).find(".zk-emp-select").prop("checked", checked);
        });
        syncSelectAllCheckbox();
    });

    // ── Mount a real Employee Link control on every row ─────────────────
    // frappe.ui.form.make_control is the supported way to create standalone
    // controls (same as the filter bar on the zk-daily-checkins page). It
    // wires up parent/render_input/refresh so the Link gets its full
    // autocomplete/awesomplete UX — hand-constructing ControlLink leaves the
    // input blank and dead because BaseControl skips its own setup without
    // a parent passed through the factory.
    const build_controls = () => {
        $body.find(".zk-emp-link-target").each(function () {
            const $target = $(this);
            const fieldname = "zk_emp_" + String($target.data("user-id")).replace(/\W/g, "_");

            const control = frappe.ui.form.make_control({
                df: {
                    fieldtype: "Link",
                    fieldname: fieldname,
                    options: "Employee",
                    label: "",
                },
                parent: $target,
                render_input: true,
            });
            control.refresh();
            control.set_value($target.data("current") || "");

            // re-trigger the awesomplete suggestions when the user clicks in
            control.$input.on("focus", function () {
                const v = control.get_value() || "";
                control.$input.val("").trigger("input");
                control.$input.val(v).trigger("input");
            });

            $target.closest(".zk-emp-row").data("zk-link-control", control);
        });
    };

    // Build the controls only once the dialog is visible (the wrapper
    // must be in the DOM and laid out before Link autocomplete works).
    dialog.show();
    setTimeout(build_controls, 100);
}
