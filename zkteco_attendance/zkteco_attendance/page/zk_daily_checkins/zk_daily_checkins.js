// ZKTeco Attendance — Employee Daily Checkins Page
// Shows a per-employee, per-day checkin breakdown.
// Supports standalone date-range mode (no Attendance Summary required)
// and navigation-from-summary mode.

frappe.pages["zk-daily-checkins"].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({
        parent: wrapper,
        title: __("Employee Daily Checkins"),
        single_column: true,
    });

    const state = {
        data:             null,
        expanded:         new Set(),
        from_date:        null,
        to_date:          null,
        attendance_summary: null,
        employee_list:    [],
        biometric_device: null,
        filter_employee:  null,
        filter_project:   null,
        can_edit_checkins: frappe.user_roles.includes("Checkin Editor"),
    };

    // ── Filter bar ────────────────────────────────────────────────────────
    const $filterWrap = $(`
        <div class="zk-daily-filterbar" style="padding:14px;">
            <div class="row" style="margin-bottom:8px;">
                <div class="col-sm-2" id="zk-fd"></div>
                <div class="col-sm-2" id="zk-td"></div>
                <div class="col-sm-2" id="zk-summary-wrap">
                    <div id="zk-summary"></div>
                </div>
                <div class="col-sm-2" id="zk-device-wrap">
                    <div id="zk-device"></div>
                </div>
                <div class="col-sm-2" id="zk-emp-wrap">
                    <div id="zk-emp"></div>
                </div>
                <div class="col-sm-2" id="zk-project-wrap">
                    <div id="zk-project"></div>
                </div>
            </div>
            <div class="row">
                <div class="col-sm-12" style="display:flex;align-items:center;padding-top:4px;">
                    <button class="btn btn-primary btn-sm" id="zk-load-btn">${__("Load")}</button>
                    &nbsp;
                    <button class="btn btn-default btn-sm" id="zk-clear-btn">${__("Clear")}</button>
                    &nbsp;
                    <button class="btn btn-default btn-sm" id="zk-invalids-btn" title="${__("Show employees with invalid attendance days in the selected range")}">
                        <i class="fa fa-exclamation-triangle" style="color:var(--orange-500);"></i> ${__("Check invalids")}
                    </button>
                    &nbsp;
                    <button class="btn btn-default btn-sm" id="zk-pull-btn" title="${__("Pull attendance logs from the selected biometric device (or all active devices) and create Employee Checkin records")}">
                        <i class="fa fa-refresh"></i> ${__("Pull checkins")}
                    </button>
                    <span style="flex:1;"></span>
                    <button class="btn btn-default btn-sm" id="zk-pdf-btn" title="${__("Download the loaded report as a PDF")}" disabled>
                        <i class="fa fa-file-pdf-o" style="color:var(--red-500);"></i> ${__("Download PDF")}
                    </button>
                    &nbsp;
                    <button class="btn btn-default btn-sm" id="zk-excel-btn" title="${__("Download the loaded report as an Excel workbook")}" disabled>
                        <i class="fa fa-file-excel-o" style="color:var(--green-500);"></i> ${__("Download Excel")}
                    </button>
                </div>
            </div>
        </div>
    `).appendTo(page.main);

    // From Date control
    const from_ctrl = frappe.ui.form.make_control({
        df: { fieldtype: "Date", fieldname: "from_date", label: __("From Date") },
        parent: $filterWrap.find("#zk-fd"),
        render_input: true,
    });
    from_ctrl.refresh();

    // To Date control
    const to_ctrl = frappe.ui.form.make_control({
        df: { fieldtype: "Date", fieldname: "to_date", label: __("To Date") },
        parent: $filterWrap.find("#zk-td"),
        render_input: true,
    });
    to_ctrl.refresh();

    // Attendance Summary link (optional — pre-fills dates + employees)
    const summary_ctrl = frappe.ui.form.make_control({
        df: {
            fieldtype: "Link",
            fieldname: "attendance_summary",
            label: __("Attendance Summary (optional)"),
            options: "Attendance Summary",
            change() {
                const val = summary_ctrl.get_value();
                if (!val) return;
                frappe.db.get_doc("Attendance Summary", val).then(doc => {
                    from_ctrl.set_value(doc.from_date);
                    to_ctrl.set_value(doc.to_date);
                    state.attendance_summary = val;
                    state.employee_list = (doc.details || []).map(r => r.employee);
                });
            },
        },
        parent: $filterWrap.find("#zk-summary"),
        render_input: true,
    });
    summary_ctrl.refresh();

    // Biometric Device filter
    const device_ctrl = frappe.ui.form.make_control({
        df: {
            fieldtype: "Link",
            fieldname: "biometric_device",
            label: __("Biometric Device"),
            options: "Biometric Device",
            change() {
                state.biometric_device = device_ctrl.get_value();
            },
        },
        parent: $filterWrap.find("#zk-device"),
        render_input: true,
    });
    device_ctrl.refresh();

    // Employee filter
    const emp_ctrl = frappe.ui.form.make_control({
        df: {
            fieldtype: "Link",
            fieldname: "filter_employee",
            label: __("Employee"),
            options: "Employee",
            change() {
                state.filter_employee = emp_ctrl.get_value();
            },
        },
        parent: $filterWrap.find("#zk-emp"),
        render_input: true,
    });
    emp_ctrl.refresh();

    // Project filter — narrows employees by their Project (Employee master),
    // the same field the Attendance Summary "Fetch Employees" dialog filters on.
    const project_ctrl = frappe.ui.form.make_control({
        df: {
            fieldtype: "Link",
            fieldname: "filter_project",
            label: __("Project"),
            options: "Project",
            change() {
                state.filter_project = project_ctrl.get_value();
            },
        },
        parent: $filterWrap.find("#zk-project"),
        render_input: true,
    });
    project_ctrl.refresh();

    const $body = $(`<div class="zk-daily-body" style="margin:18px;"></div>`).appendTo(page.main);

    // ── Delegated handlers (bound ONCE — $body persists across renders) ──
    // These must NOT be re-bound inside render_data(), otherwise each Load
    // stacks another copy and a single click opens multiple dialogs.
    $body.on("click", ".zk-add-checkin", function () {
        const emp     = $(this).data("employee");
        const date    = $(this).data("date");
        const summary = $(this).data("summary");
        show_checkin_dialog({ employee: emp, date, summary, mode: "add" });
    });

    $body.on("click", ".zk-edit-checkin", function (e) {
        e.stopPropagation();
        const emp     = $(this).data("employee");
        const date    = $(this).data("date");
        const time    = $(this).data("time");
        const logtype = $(this).data("logtype");
        const summary = $(this).data("summary");
        const checkin = $(this).data("checkin-name");
        const ot      = $(this).data("is-overtime");
        show_checkin_dialog({ employee: emp, date, time, logtype, summary, checkin_name: checkin, is_overtime: ot, mode: "edit" });
    });

    $body.on("click", ".zk-ignore-checkin", function (e) {
        e.stopPropagation();
        const $el = $(this);
        const checkinName = $el.data("checkin-name");
        if (!checkinName) return;
        frappe.confirm(
            __($el.data("ignored") ? "Unignore this checkin? It will be included in attendance processing again." : "Ignore this checkin? It will be excluded from attendance processing."),
            () => {
                frappe.call({
                    method: "zkteco_attendance.zkteco_attendance.page.zk_daily_checkins.zk_daily_checkins.toggle_ignore_checkin",
                    args: { checkin_name: checkinName },
                    freeze: true,
                    freeze_message: __($el.data("ignored") ? "Unignoring…" : "Ignoring…"),
                    callback(r) {
                        if (r.message) {
                            frappe.show_alert({
                                message: r.message.action === "ignored"
                                    ? __("Checkin ignored successfully.")
                                    : __("Checkin unignored successfully."),
                                indicator: r.message.action === "ignored" ? "orange" : "green",
                            }, 4);
                            trigger_load();
                        }
                    },
                });
            },
            () => {}
        );
    });

    // ── Load / Clear / Download PDF buttons ────────────────────────────────
    $filterWrap.find("#zk-load-btn").on("click", () => trigger_load());

    // ── Pull checkins — sync the selected device (or all active devices) ──
    // Reuses the same foreground endpoints as the Biometric Device form's
    // Pull Checkins button: pull_checkins_now runs the sync synchronously
    // while get_pull_progress supplies live progress (polling fallback when
    // realtime events are unavailable).
    $filterWrap.find("#zk-pull-btn").on("click", function () {
        const device = device_ctrl.get_value();

        if (device) {
            const deviceLabel = device;  // Biometric Device is named by device_name
            frappe.confirm(
                __("This will connect to <b>{0}</b> now, pull attendance logs, and create Employee Checkin records. This may take a little while for devices with many logs. Continue?",
                    [deviceLabel]),
                () => run_pull_with_progress(device, deviceLabel)
            );
        } else {
            frappe.confirm(
                __("No device is selected, so <b>all active devices</b> will be synced in the background. Check the <a href='/app/attendance-sync-log'>Attendance Sync Log</a> for results. Continue?"),
                () => {
                    frappe.call({
                        method: "zkteco_attendance.zkteco_attendance.api.endpoints.sync_all_devices",
                        freeze: true,
                        freeze_message: __("Queueing sync for all active devices…"),
                        callback() {
                            frappe.show_alert({
                                message: __("Sync jobs queued for all active devices. Check the Attendance Sync Log for results."),
                                indicator: "blue",
                            }, 6);
                        },
                    });
                }
            );
        }
    });

    // Foreground pull with live progress — mirrors the Biometric Device
    // form's pull_checkins_with_progress / _run_pull_checkins flow.
    function run_pull_with_progress(deviceName, deviceLabel) {
        // Unique token for this pull run — echoed back in every progress
        // payload so stale progress from a previous run is never applied.
        const run_id = "zk_pull_" + Date.now() + "_" + Math.random().toString(36).slice(2, 10);

        const dialog = new frappe.ui.Dialog({
            title: __("Pulling Check-ins from {0}", [deviceLabel]),
            size: "large",
            fields: [
                { fieldtype: "HTML", fieldname: "progress_html" },
            ],
        });

        const $dlgBody = dialog.fields_dict.progress_html.$wrapper;
        $dlgBody.html(`
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

        const $stage  = $dlgBody.find(".zkteco-pull-stage");
        const $bar    = $dlgBody.find(".zkteco-pull-bar");
        const $counts = $dlgBody.find(".zkteco-pull-counts");
        const $errors = $dlgBody.find(".zkteco-pull-errors");

        const setProgress = (pct, text) => {
            pct = Math.max(0, Math.min(100, pct));
            $bar.css("width", pct + "%");
            if (text) $stage.text(text);
        };

        const handler = (data) => {
            if (!data) return;
            // Ignore progress belonging to a different pull run.
            if (data.run_id && data.run_id !== run_id) return;

            switch (data.stage) {
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
                    $dlgBody.find(".zkteco-cnt-dp").text(data.double_punches ?? 0);
                    break;
                case "creating_checkins": {
                    const pct = data.total ? 55 + (data.current / data.total) * 40 : 55;
                    setProgress(pct, data.message);
                    $counts.show();
                    $dlgBody.find(".zkteco-cnt-new").text(data.new_records ?? 0);
                    $dlgBody.find(".zkteco-cnt-dupes").text(data.duplicates ?? 0);
                    $dlgBody.find(".zkteco-cnt-failed").text(data.failed ?? 0);
                    $dlgBody.find(".zkteco-cnt-ot").text(data.overtime_records ?? 0);
                    $dlgBody.find(".zkteco-cnt-dp").text(data.double_punches ?? 0);
                    $dlgBody.find(".zkteco-cnt-total").text(data.total ?? 0);
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

        // Realtime progress (fast path) + polling fallback, as on the
        // Biometric Device form.
        let realtime_on = false;
        try {
            if (frappe.realtime && typeof frappe.realtime.on === "function") {
                frappe.realtime.on("zkteco_pull_progress", handler);
                realtime_on = true;
            }
        } catch (e) {
            realtime_on = false;
        }

        let poll_timer = null;
        const stopPolling = () => {
            if (poll_timer) {
                clearInterval(poll_timer);
                poll_timer = null;
            }
        };
        const poll = () => {
            if (!dialog.$wrapper.is(":visible")) {
                stopPolling();
            }
            frappe.call({
                method: "zkteco_attendance.zkteco_attendance.api.endpoints.get_pull_progress",
                args: { device_name: deviceName, run_id: run_id },
                callback(r) {
                    if (r && r.message) handler(r.message);
                },
            });
        };
        poll_timer = setInterval(poll, 1500);
        poll();

        const cleanup = () => {
            stopPolling();
            if (realtime_on) {
                try {
                    frappe.realtime.off("zkteco_pull_progress", handler);
                } catch (e) { /* ignore */ }
            }
        };

        frappe.call({
            method: "zkteco_attendance.zkteco_attendance.api.endpoints.pull_checkins_now",
            args: { device_name: deviceName, run_id: run_id },
            callback(r) {
                cleanup();

                if (!r.message) {
                    setProgress(100, __("No response from server."));
                    dialog.get_close_btn().show();
                    return;
                }

                const res = r.message;

                if (!res.success) {
                    setProgress(100, __("Pull failed."));
                    $stage.removeClass("text-muted").addClass("text-danger");
                    $errors.show().text(res.error || __("Unknown error"));
                    dialog.get_close_btn().show();
                    return;
                }

                setProgress(100, __("Pull completed."));
                $counts.show();
                $dlgBody.find(".zkteco-cnt-total").text(res.total_records ?? 0);
                $dlgBody.find(".zkteco-cnt-new").text(res.new_records ?? 0);
                $dlgBody.find(".zkteco-cnt-dupes").text(res.duplicates ?? 0);
                $dlgBody.find(".zkteco-cnt-failed").text(res.failed ?? 0);
                $dlgBody.find(".zkteco-cnt-ot").text(res.overtime_records ?? 0);
                $dlgBody.find(".zkteco-cnt-dp").text(res.double_punches ?? 0);

                const statusColors = { Success: "text-success", Partial: "text-warning", Failed: "text-danger" };
                $dlgBody.find(".zkteco-cnt-status")
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

                // New checkins may have arrived — refresh the loaded report.
                if (state.data) trigger_load();
            },
            error() {
                cleanup();
                setProgress(100, __("Pull failed."));
                $stage.removeClass("text-muted").addClass("text-danger");
                dialog.get_close_btn().show();
            },
        });
    }

    $filterWrap.find("#zk-clear-btn").on("click", () => {
        from_ctrl.set_value("");
        to_ctrl.set_value("");
        summary_ctrl.set_value("");
        device_ctrl.set_value("");
        emp_ctrl.set_value("");
        project_ctrl.set_value("");
        state.attendance_summary = null;
        state.employee_list = [];
        state.biometric_device = null;
        state.filter_employee  = null;
        state.filter_project   = null;
        state.data = null;
        render_empty_state();
    });

    // Download PDF — server re-runs the same query as Load and streams a
    // styled PDF back. Navigate to the API URL so the browser saves the file.
    $filterWrap.find("#zk-pdf-btn").on("click", function () {
        const fd = from_ctrl.get_value();
        const td = to_ctrl.get_value();
        if (!fd || !td) {
            frappe.msgprint(__("Please set both From Date and To Date."));
            return;
        }
        if (!state.data || !state.data.employees || !state.data.employees.length) {
            frappe.msgprint(__("Load data first — there is nothing to download yet."));
            return;
        }
        const payload = {
            from_date:          fd,
            to_date:            td,
            attendance_summary: state.attendance_summary || "",
            employee_list:      state.employee_list.length ? JSON.stringify(state.employee_list) : "",
            biometric_device:   state.biometric_device || "",
            filter_employee:    state.filter_employee || "",
            filter_project:     state.filter_project || "",
        };
        const query = Object.keys(payload)
            .filter(k => payload[k])
            .map(k => encodeURIComponent(k) + "=" + encodeURIComponent(payload[k]))
            .join("&");
        const url = "/api/method/zkteco_attendance.zkteco_attendance.page.zk_daily_checkins.zk_daily_checkins.download_pdf?" + query;
        frappe.show_alert({ message: __("Preparing PDF download…"), indicator: "blue" }, 4);
        window.location.href = url;
    });

    // Download Excel — same query as Load/PDF; server streams back an .xlsx.
    $filterWrap.find("#zk-excel-btn").on("click", function () {
        const fd = from_ctrl.get_value();
        const td = to_ctrl.get_value();
        if (!fd || !td) {
            frappe.msgprint(__("Please set both From Date and To Date."));
            return;
        }
        if (!state.data || !state.data.employees || !state.data.employees.length) {
            frappe.msgprint(__("Load data first — there is nothing to download yet."));
            return;
        }
        const payload = {
            from_date:          fd,
            to_date:            td,
            attendance_summary: state.attendance_summary || "",
            employee_list:      state.employee_list.length ? JSON.stringify(state.employee_list) : "",
            biometric_device:   state.biometric_device || "",
            filter_employee:    state.filter_employee || "",
            filter_project:     state.filter_project || "",
        };
        const query = Object.keys(payload)
            .filter(k => payload[k])
            .map(k => encodeURIComponent(k) + "=" + encodeURIComponent(payload[k]))
            .join("&");
        const url = "/api/method/zkteco_attendance.zkteco_attendance.page.zk_daily_checkins.zk_daily_checkins.download_excel?" + query;
        frappe.show_alert({ message: __("Preparing Excel download…"), indicator: "blue" }, 4);
        window.location.href = url;
    });

    // Check invalids — popup listing employees with invalid days + the dates
    $filterWrap.find("#zk-invalids-btn").on("click", function () {
        const fd = from_ctrl.get_value();
        const td = to_ctrl.get_value();
        if (!fd || !td) {
            frappe.msgprint(__("Please set both From Date and To Date."));
            return;
        }

        frappe.call({
            method: "zkteco_attendance.zkteco_attendance.page.zk_daily_checkins.zk_daily_checkins.get_invalid_days",
            args: {
                attendance_summary: state.attendance_summary || null,
                from_date:          fd,
                to_date:            td,
                employee_list:      state.employee_list.length ? JSON.stringify(state.employee_list) : null,
                biometric_device:   state.biometric_device || null,
                filter_employee:    state.filter_employee || null,
                filter_project:     state.filter_project || null,
            },
            freeze: true,
            freeze_message: __("Checking invalid days…"),
            callback(r) {
                show_invalids_dialog(r.message || {});
            },
        });
    });

    function show_invalids_dialog(payload) {
        const invalids  = payload.invalids || [];
        const fromDate  = payload.from_date ? frappe.datetime.str_to_user(payload.from_date) : "";
        const toDate    = payload.to_date   ? frappe.datetime.str_to_user(payload.to_date)   : "";
        const periodTxt = fromDate && toDate && fromDate !== toDate ? `${fromDate} — ${toDate}` : (fromDate || "");

        if (!invalids.length) {
            frappe.msgprint({
                title: __("Check Invalids"),
                message: periodTxt
                    ? __("No invalid days found for {0}.", [periodTxt])
                    : __("No invalid days found."),
                indicator: "green",
            });
            return;
        }

        const rows = invalids.map(inv => `
            <tr>
                <td style="border:1px solid var(--border-color);padding:6px 10px;">
                    <a href="/app/employee/${encodeURIComponent(inv.employee)}">${frappe.utils.escape_html(inv.employee_name || inv.employee)}</a>
                    <div class="text-muted" style="font-size:0.75rem;">${frappe.utils.escape_html(inv.employee || "")}</div>
                </td>
                <td style="border:1px solid var(--border-color);padding:6px 10px;">${frappe.utils.escape_html(inv.department || "—")}</td>
                <td style="border:1px solid var(--border-color);padding:6px 10px;text-align:center;">
                    <span class="indicator-pill red">${inv.invalid_count}</span>
                </td>
                <td style="border:1px solid var(--border-color);padding:6px 10px;">
                    ${(inv.day_checkins && inv.day_checkins.length
                        ? inv.day_checkins
                        : (inv.invalid_dates || []).map(dt => ({ date: dt, checkins: null }))).map(dc => {
                        const punches = dc.checkins === null ? null : (dc.checkins || []).map(c =>
                            `<span class="zk-chip ${c.log_type === "IN" ? "zk-chip-in" : "zk-chip-out"}${c.is_overtime ? " zk-chip-ot" : ""}${c.ignored ? " zk-chip-ignored" : ""}" title="${c.is_overtime ? __("Overtime punch") : c.log_type}${c.ignored ? " — " + __("ignored (excluded from attendance)") : ""}"${c.ignored ? " style=\"opacity:0.55;text-decoration:line-through;\"" : ""}>${c.time} <b>${c.log_type}${c.is_overtime ? " (OT)" : ""}</b>${c.ignored ? " ⊘" : ""}</span>`
                        ).join(" ");
                        return `
                            <div style="margin:2px 0; display:flex; align-items:center; flex-wrap:wrap; border:1px solid var(--border-color); border-radius:4px; padding:2px 6px; background:var(--card-bg);">
                                <span class="zk-invalid-date" style="display:inline-block;border:1px solid #b03a3a;border-radius:4px;padding:1px 6px;margin:2px;font-size:0.75rem;background:#ffd6d6;color:#7a1010;">${frappe.datetime.str_to_user(dc.date)}</span>
                                ${punches ? `<span style="margin-left:6px;">${punches}</span>` : (punches === null ? "" : `<span class="text-muted" style="margin-left:6px;font-size:0.75rem;">${__("No checkins recorded")}</span>`)}
                            </div>`;
                    }).join("")}
                </td>
            </tr>`).join("");

        const tableHtml = `
            <div class="text-muted" style="margin-bottom:8px;">
                ${periodTxt ? `<b>${periodTxt}</b> &nbsp;|&nbsp;` : ""}
                ${__("{0} employee(s) with invalid days, of {1} checked.", [invalids.length, payload.total_employees_checked || 0])}
            </div>
            <div style="max-height:55vh;overflow:auto;">
                <table class="table table-bordered" style="margin-bottom:0; border:1px solid #b0b0b0; border-radius:4px;">
                    <thead>
                        <tr style="background:var(--table-bg,var(--card-bg));">
                            <th style="width:30%;">${__("Employee")}</th>
                            <th style="width:20%;">${__("Department")}</th>
                            <th style="width:12%; text-align:center;">${__("Invalid Days")}</th>
                            <th>${__("Invalid Dates & Checkins")}</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>`;

        // Render via an HTML field — the Dialog API renders fields into the
        // modal body itself, so this works across Frappe versions (unlike
        // reaching into dlg.$body / .modal-body directly).
        const dlg = new frappe.ui.Dialog({
            title: __("Employees with Invalid Days"),
            size: "extra-large",
            fields: [
                { fieldtype: "HTML", fieldname: "invalids_html", options: tableHtml },
            ],
            primary_action_label: __("Close"),
            primary_action: () => dlg.hide(),
        });
        dlg.show();
    }

    // ── Helpers ───────────────────────────────────────────────────────────
    function render_empty_state(msg) {
        $body.html(`<div class="text-muted text-center" style="padding:60px 0;">
            ${msg || __("Set a date range and click Load to view daily check-ins.")}
        </div>`);
        $filterWrap.find("#zk-pdf-btn").prop("disabled", true);
        $filterWrap.find("#zk-excel-btn").prop("disabled", true);
    }

    function status_color(status) {
        const map = { Present: "green", "Half Day": "orange", Absent: "red",
                      Invalid: "darkgrey", "Manual Review": "blue",
                      Holiday: "purple", "Weekly_Off": "grey" };
        return map[status] || "grey";
    }

    // OT breakdown chips — Day / Night / Weekend / Holiday overtime
    function ot_cell(d) {
        const chips = [];
        if (d.day_ot_hours)        chips.push(`<span class="zk-ot-chip zk-ot-day" title="${__("Day OT (after shift end)")}" style="border: 1px solid #38684e; background-color: #8bf8c2; border-radius: 4px; padding: 2px 4px">D ${(d.day_ot_hours||0).toFixed(1)}</span>`);
        if (d.night_ot_hours)      chips.push(`<span class="zk-ot-chip zk-ot-night" title="${__("Night OT (night window)")}" style="border: 1px solid #5d6838; background-color: #d6f88b; border-radius: 4px; padding: 2px 4px">N ${(d.night_ot_hours||0).toFixed(1)}</span>`);
        if (d.weekend_ot_hours)    chips.push(`<span class="zk-ot-chip zk-ot-weekend" title="${__("Weekend OT (weekly rest day)")}" style="border: 1px solid #683848; background-color: #f88bc5; border-radius: 4px; padding: 2px 4px;">W ${(d.weekend_ot_hours||0).toFixed(1)}</span>`);
        if (d.holiday_ot_hours)    chips.push(`<span class="zk-ot-chip zk-ot-holiday" title="${__("Holiday OT (public holiday)")}" style="border: 1px solid #4a3868; background-color: #bc8bf8; border-radius: 4px; padding: 2px 4px;">H ${(d.holiday_ot_hours||0).toFixed(1)}</span>`);
        return chips.length ? chips.join(" ") : `<span class="text-muted">—</span>`;
    }

    // Late entry / early exit badges (beyond the shift's grace periods)
    function grace_badges(d) {
        const badges = [];
        if (d.is_late) {
            badges.push(`<span class="zk-grace-badge" title="${__("Late entry")}: ${d.late_minutes} ${__("min beyond grace")}" style="border:1px solid #b07a2a;background-color:#ffd98b;color:#7a5200;border-radius:4px;padding:1px 4px;margin-left:4px;font-size:0.7rem;">${__("L-EN")}</span>`);
        }
        if (d.is_early_exit) {
            badges.push(`<span class="zk-grace-badge" title="${__("Early exit")}: ${d.early_minutes} ${__("min beyond grace")}" style="border:1px solid #2ab06f;background-color:#ffd98b;color:#7a5200;border-radius:4px;padding:1px 4px;margin-left:4px;font-size:0.7rem;">${__("E-EX")}</span>`);
        }
        return badges.join("");
    }

    function render_checkin_chips(checkins, emp, date, summary_name) {
        const summary_attr = summary_name ? frappe.utils.escape_html(summary_name) : "";
        const add_btn = (with_label) => `
            <button class="btn btn-xs btn-default zk-add-checkin"
                    data-employee="${frappe.utils.escape_html(emp)}"
                    data-date="${date}"
                    data-summary="${summary_attr}"
                    style="margin-left:6px;">
                <i class="fa fa-plus"></i>${with_label ? ` ${__("Add")}` : ""}
            </button>`;

        if (!checkins || !checkins.length) {
            if (!state.can_edit_checkins) {
                return `<span class="text-muted">${__("No check-ins")}</span>`;
            }
            return `<span class="text-muted">${__("No check-ins")}</span>${add_btn(true)}`;
        }

        const chips = checkins.map((c, idx) => {
            const otClass = c.is_overtime
                ? "zk-chip-ot"
                : (c.log_type === "IN" ? "zk-chip-in" : "zk-chip-out");
            const label = c.is_overtime ? `${c.log_type} (OT)` : c.log_type;
            const ignoredClass = c.ignored ? "zk-chip-ignored" : "";

            let manualBadge = "";
            let remarkBadge = "";
            let editBtn     = "";
            let ignoreBtn   = "";
            if (c.remark) {
                remarkBadge = `<span class="zk-manual-badge" title="${__("Remark")}: ${frappe.utils.escape_html(c.remark)}">💬</span>`;
            }
            if (c.manually_edited) {
                const tip = c.edited_by
                    ? `${__("Edited by")} ${frappe.utils.escape_html(c.edited_by)}${c.edited_at ? " @ " + c.edited_at.substring(0,16) : ""}`
                    : __("Manually added/edited");
                manualBadge = `<span class="zk-manual-badge" title="${tip}">✎</span>`;
            }
            if (c.ignored) {
                manualBadge = `<span class="zk-ignored-badge" title="${__("This checkin is ignored — excluded from attendance processing")}">⊘</span>`;
            }
            if (state.can_edit_checkins) {
                editBtn = `<span class="zk-edit-checkin" title="${__("Edit")}"
                                data-employee="${frappe.utils.escape_html(emp)}"
                                data-date="${date}"
                                data-time="${c.time}"
                                data-logtype="${c.log_type}"
                                data-idx="${idx}"
                                data-checkin-name="${frappe.utils.escape_html(c.name || "")}"
                                data-is-overtime="${c.is_overtime ? 1 : 0}"
                                data-summary="${summary_attr}"
                                style="cursor:pointer;margin-left:4px;opacity:0.6;">✎</span>`;
                ignoreBtn = `<span class="zk-ignore-checkin" title="${c.ignored ? __("Unignore (include in attendance)") : __("Ignore (exclude from attendance)")}"
                                data-checkin-name="${frappe.utils.escape_html(c.name || "")}"
                                data-ignored="${c.ignored ? 1 : 0}"
                                style="cursor:pointer;margin-left:4px;opacity:0.6;">${c.ignored ? "⊘" : "○"}</span>`;
            }
            return `<span class="zk-chip ${otClass} ${ignoredClass}">${c.time} <b>${label}</b>${manualBadge}${remarkBadge} ${editBtn} ${ignoreBtn}</span>`;
        }).join(" ");

        if (!state.can_edit_checkins) {
            return chips;
        }
        return chips + add_btn(false);
    }

    // Convert decimal hours (7.6) to clock-style H:MM (7:36)
    function format_hours_hhmm(hours) {
        const totalMinutes = Math.max(0, Math.round((hours || 0) * 60));
        const hh = Math.floor(totalMinutes / 60);
        const mm = String(totalMinutes % 60).padStart(2, "0");
        return `${hh}:${mm}`;
    }

    function render_employee_table(emp, summary_name) {
        const rows = emp.days.map(d => {
            const dayMark = d.is_holiday
                ? `<span class="zk-day-mark zk-day-holiday" title="${__("Public Holiday")}">HOL</span>`
                : d.is_weekend
                    ? `<span class="zk-day-mark zk-day-weekend" title="${__("Weekly Rest Day")}">SUN</span>`
                    : d.is_saturday
                        ? `<span class="zk-day-mark" title="${__("Saturday")}">SAT</span>`
                        : "";
            return `
                <tr>
                    <td style="border: 1px solid #385068;">${frappe.datetime.str_to_user(d.date)}${dayMark}</td>
                    <td style="border: 1px solid #385068;">${__(d.weekday)}</td>
                    <td style="border: 1px solid #385068;"><span class="indicator-pill ${status_color(d.status)}">${__(d.status)}</span><br>${grace_badges(d)}</td>
                    <td class="text-right" style="border: 1px solid #385068;">${(d.hours||0).toFixed(2)}</td>
                    <td class="text-right" style="border: 1px solid #385068;">${format_hours_hhmm(d.hours)}</td>
                    <td class="text-right ${d.overtime_hours ? 'text-warning' : ''}" style="border: 1px solid #385068;">${ot_cell(d)}</td>
                    <td style="border: 1px solid #385068;">${render_checkin_chips(d.checkins, emp.employee, d.date, summary_name)}</td>
                </tr>`;
        }).join("");

        return `
            <table class="table table-bordered zk-daily-table">
                <thead>
                    <tr>
                        <th style="width:120px; border: 1px solid #385068; background-color: #8bc2f8;">${__("Date")}</th>
                        <th style="width:90px; border: 1px solid #385068; background-color: #8bc2f8;">${__("Day")}</th>
                        <th style="width:120px; border: 1px solid #385068; background-color: #8bc2f8;">${__("Status")}</th>
                        <th style="width:70px; border: 1px solid #385068; background-color: #8bc2f8;" class="text-right">${__("Hours")}</th>
                        <th style="width:90px; border: 1px solid #385068; background-color: #8bc2f8;" class="text-right">${__("Formatted Hours")}</th>
                        <th style="width:160px; border: 1px solid #385068; background-color: #8bc2f8;" class="text-right">${__("OT Breakdown")}</th>
                        <th style="border: 1px solid #385068; background-color: #8bc2f8;">${__("Check-ins")}</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>`;
    }

    function render_data(data) {
        if (!data.employees || !data.employees.length) {
            render_empty_state(__("No employees found for the selected range."));
            return;
        }

        const header = `
            <div class="zk-daily-header" style="margin-bottom:14px;">
                <div class="text-muted">
                    <b>${frappe.datetime.str_to_user(data.from_date)}</b>
                    &mdash;
                    <b>${frappe.datetime.str_to_user(data.to_date)}</b>
                    ${data.company ? "&nbsp;|&nbsp;" + frappe.utils.escape_html(data.company) : ""}
                    &nbsp;|&nbsp; ${__("{0} employee(s)", [data.employees.length])}
                    ${data.attendance_summary
                        ? `&nbsp;|&nbsp; <a href="/app/attendance-summary/${encodeURIComponent(data.attendance_summary)}">${frappe.utils.escape_html(data.attendance_summary)}</a>`
                        : ""}
                </div>
                <div class="text-muted" style="font-size:0.72rem;margin-top:4px;">
                    ${__("Abbreviations and Buttons")}: <span class="zk-ot-chip zk-ot-day">D: ${__("Day OT")}</span>
                    <span class="zk-ot-chip zk-ot-night">N: ${__("Night OT")}</span>
                    <span class="zk-ot-chip zk-ot-weekend">W: ${__("Weekend OT")}</span>
                    <span class="zk-ot-chip zk-ot-holiday">H: ${__("Holiday OT")}</span>
                    <span class="zk-ot-chip zk-ot-late">L-EN: ${__("Late Entry")}</span>
                    <span class="zk-ot-chip zk-ot-early">E-EX: ${__("Early Exit")}</span>
                    <span class="zk-ot-chip zk-ot-night">○: ${__("Ignore Button")}</span>
                    <span class="zk-ot-chip zk-ot-night">✎: ${__("Manual Edit Button")}</span>
                </div>
            </div>`;

        const cards = data.employees.map(emp => {
            const isOpen   = state.expanded.has(emp.employee);
            const totalOt  = (emp.days||[]).reduce((s,d) => s+(d.overtime_hours||0), 0);
            const dayOt    = (emp.days||[]).reduce((s,d) => s+(d.day_ot_hours||0), 0);
            const nightOt  = (emp.days||[]).reduce((s,d) => s+(d.night_ot_hours||0), 0);
            const weekendOt = (emp.days||[]).reduce((s,d) => s+(d.weekend_ot_hours||0), 0);
            const holidayOt = (emp.days||[]).reduce((s,d) => s+(d.holiday_ot_hours||0), 0);
            const otLabel  = totalOt
                ? `<span class="text-warning" style="margin-right:10px;" title="${__("Day OT")}: ${dayOt.toFixed(2)}h | ${__("Night OT")}: ${nightOt.toFixed(2)}h | ${__("Weekend OT")}: ${weekendOt.toFixed(2)}h | ${__("Holiday OT")}: ${holidayOt.toFixed(2)}h">
                       ${__("OT")}: ${totalOt.toFixed(2)}h
                   </span>`
                : "";

            const deviceInfo = [];
            if (emp.zk_biometric_device) {
                deviceInfo.push(`<span class="text-muted" title="${__("Biometric Device")}" style="margin-left:8px;font-size:0.78rem;"><b>Device:</b> ${frappe.utils.escape_html(emp.zk_biometric_device)}</span>`);
            }
            if (emp.attendance_device_id) {
                deviceInfo.push(`<span class="text-muted" title="${__("Device ID")}" style="margin-left:8px;font-size:0.78rem;"><b>Device Emp ID:</b> ${frappe.utils.escape_html(emp.attendance_device_id)}</span>`);
            }
            if (emp.shift_type) {
                deviceInfo.push(`<span class="text-muted" title="${__("Shift Type")}" style="margin-left:8px;font-size:0.78rem;"><b>Shift:</b> ${frappe.utils.escape_html(emp.shift_type)}</span>`);
            }

            return `
                <div class="zk-emp-card" data-employee="${frappe.utils.escape_html(emp.employee)}" style="margin-bottom:4px; border: 1px solid #a19999">
                    <div class="zk-emp-card-head" style="cursor:pointer;display:flex;justify-content:space-between;align-items:center;padding:10px 14px;border:1px solid var(--border-color);border-radius:var(--border-radius);background:var(--card-bg);">
                        <div>
                            <span class="text-muted" style="margin-left:8px; border:1px solid #e4e3e3; border-radius:var(--border-radius); padding:2px 4px;"><b>Emp Name:</b> ${frappe.utils.escape_html(emp.fullname || emp.employee_name || emp.employee)}</span>
                            <span class="text-muted" style="margin-left:8px; border:1px solid #e4e3e3; border-radius:var(--border-radius); padding:2px 4px;"><b>Emp ID:</b> ${frappe.utils.escape_html(emp.employee)}</span>
                            ${emp.department ? `<span class="text-muted" style="margin-left:8px; border:1px solid #e4e3e3; border-radius:var(--border-radius); padding:2px 4px;"><b>Department:</b> ${frappe.utils.escape_html(emp.department)}</span>` : ""}
                            <span class="text-muted" style="margin-left:8px; border:1px solid #e4e3e3; border-radius:var(--border-radius); padding:2px 4px;">${deviceInfo.join("")}</span>
                        </div>
                        <div class="text-muted">${otLabel}<i class="fa fa-chevron-${isOpen?"up":"down"}"></i></div>
                    </div>
                    <div class="zk-emp-card-body" style="display:${isOpen?"block":"none"};padding-top:4px;">
                        ${render_employee_table(emp, data.attendance_summary)}
                    </div>
                </div>`;
        }).join("");

        $body.html(header + `<div class="zk-emp-cards">${cards}</div>`);

        // Expand/collapse
        $body.find(".zk-emp-card-head").on("click", function () {
            const $card = $(this).closest(".zk-emp-card");
            const employee = $card.data("employee");
            const $cb = $card.find(".zk-emp-card-body");
            const $ic = $(this).find("i.fa");
            if (state.expanded.has(employee)) {
                state.expanded.delete(employee);
                $cb.slideUp(150);
                $ic.removeClass("fa-chevron-up").addClass("fa-chevron-down");
            } else {
                state.expanded.add(employee);
                $cb.slideDown(150);
                $ic.removeClass("fa-chevron-down").addClass("fa-chevron-up");
            }
        });

        // A report is on screen — the PDF / Excel downloads are now available
        $filterWrap.find("#zk-pdf-btn").prop("disabled", false);
        $filterWrap.find("#zk-excel-btn").prop("disabled", false);
    }

    // ── Manual checkin dialog ─────────────────────────────────────────────
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

    function show_checkin_dialog({ employee, date, time, logtype, summary, checkin_name, is_overtime, remark, mode }) {
        const defaultTime = time || "08:00:00";
        const isOT = is_overtime ? 1 : 0;

        function get_shift_wrapper(dlg) {
            // Prefer fields_dict, fall back to direct DOM query
            if (dlg.fields_dict && dlg.fields_dict.shift_info && dlg.fields_dict.shift_info.$wrapper) {
                return dlg.fields_dict.shift_info.$wrapper;
            }
            return dlg.$wrapper.find('.frappe-control[data-fieldname="shift_info"]');
        }

        function fetch_and_render_shift(dlg, emp, work_date) {
            const $wrapper = get_shift_wrapper(dlg);
            if (!$wrapper || !$wrapper.length) return;
            frappe.call({
                method: "zkteco_attendance.zkteco_attendance.page.zk_daily_checkins.zk_daily_checkins.get_employee_shift_info",
                args: { employee: emp, work_date: work_date },
                callback(r) {
                    const $w = get_shift_wrapper(dlg);
                    if ($w && $w.length) render_shift_html($w, r.message);
                },
            });
        }

        const d = new frappe.ui.Dialog({
            title: mode === "edit" ? __("Edit Check-in") : __("Add Check-in"),
            size: "large",
            fields: [
                { fieldtype: "HTML", fieldname: "shift_info" },
                { fieldtype: "Section Break", fieldname: "section_break_1" },
                { fieldtype: "Link", fieldname: "employee", label: __("Employee"),
                  options: "Employee", default: employee, read_only: 1 },
                { fieldtype: "Select", fieldname: "request_type", label: __("Request Type"),
                  options: "New\nEdit", default: mode === "edit" ? "Edit" : "New", reqd: 1,
                  description: __("Edit modifies the existing check-in; New adds one.") },
                { fieldtype: "Date", fieldname: "checkin_date", label: __("Date"),
                  default: date, reqd: 1 },
                { fieldtype: "Time", fieldname: "checkin_time", label: __("Time"),
                  default: defaultTime, reqd: 1 },
                { fieldtype: "Column Break", fieldname: "column_break_1" },
                { fieldtype: "Select", fieldname: "log_type", label: __("Log Type"),
                  options: "IN\nOUT", default: logtype || "IN", reqd: 1 },
                { fieldtype: "Check", fieldname: "is_overtime", label: __("Is Overtime"),
                  default: isOT, description: __("Mark this punch as an overtime punch") },
                { fieldtype: "Small Text", fieldname: "zk_remark", label: __("Remark"),
                  default: remark || "", description: __("Optional note stored on the check-in when the request is submitted") },
            ],
            primary_action_label: mode === "edit" ? __("Create Request") : __("Create Request"),
            primary_action(vals) {
                if (!vals.checkin_date || !vals.checkin_time) {
                    frappe.msgprint(__("Date and Time are required."));
                    return;
                }
                // Create a Manual Checkin Request instead of touching the
                // checkin directly — the checkin is applied when the request
                // document is submitted.
                frappe.call({
                    method: "zkteco_attendance.zkteco_attendance.page.zk_daily_checkins.zk_daily_checkins.create_manual_checkin_request",
                    args: {
                        attendance_summary: summary || null,
                        employee:           vals.employee,
                        request_type:       vals.request_type || "New",
                        checkin_date:       vals.checkin_date,
                        checkin_time:       vals.checkin_time,
                        log_type:           vals.log_type,
                        checkin_name:       checkin_name || null,
                        is_overtime:        vals.is_overtime ? 1 : 0,
                        remarks:            vals.zk_remark || null,
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
        });
        d.show();
        // Fetch shift info after dialog DOM is fully rendered
        frappe.after_ajax(() => {
            setTimeout(() => fetch_and_render_shift(d, employee, date), 150);
        });
    }

    // ── Load logic ────────────────────────────────────────────────────────
    function trigger_load() {
        const fd = from_ctrl.get_value();
        const td = to_ctrl.get_value();

        if (!fd || !td) {
            frappe.msgprint(__("Please set both From Date and To Date."));
            return;
        }

        $filterWrap.find("#zk-pdf-btn").prop("disabled", true);
        $filterWrap.find("#zk-excel-btn").prop("disabled", true);
        $body.html(`<div class="text-muted text-center" style="padding:40px 0;">${__("Loading…")}</div>`);

        frappe.call({
            method: "zkteco_attendance.zkteco_attendance.page.zk_daily_checkins.zk_daily_checkins.get_data",
            args: {
                attendance_summary: state.attendance_summary || null,
                from_date:          fd,
                to_date:            td,
                employee_list:      state.employee_list.length ? JSON.stringify(state.employee_list) : null,
                biometric_device:   state.biometric_device || null,
                filter_employee:    state.filter_employee || null,
                filter_project:     state.filter_project || null,
            },
            callback(r) {
                if (!r.message) { render_empty_state(); return; }
                state.data = r.message;
                render_data(state.data);
            },
            error() {
                $body.html(`<div class="text-danger text-center" style="padding:40px 0;">${__("Failed to load data.")}</div>`);
            },
        });
    }

    // ── Route initialisation ──────────────────────────────────────────────
    // Route: /app/zk-daily-checkins/<Attendance Summary name>
    // or:    /app/zk-daily-checkins  (standalone)
    const route        = frappe.get_route();
    const route_param  = route && route[1];

    if (route_param && route_param !== "zk-daily-checkins") {
        // Navigated from Attendance Summary — pre-fill everything from it
        state.attendance_summary = route_param;
        summary_ctrl.set_value(route_param);
        frappe.db.get_doc("Attendance Summary", route_param).then(doc => {
            from_ctrl.set_value(doc.from_date);
            to_ctrl.set_value(doc.to_date);
            state.employee_list = (doc.details || []).map(r => r.employee);
            trigger_load();
        });
    } else {
        // Standalone — default to current month
        const today       = frappe.datetime.get_today();
        const first_of_month = today.substring(0, 8) + "01";
        from_ctrl.set_value(first_of_month);
        to_ctrl.set_value(today);
        render_empty_state();
    }
};
