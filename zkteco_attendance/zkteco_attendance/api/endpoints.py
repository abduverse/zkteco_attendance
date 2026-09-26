"""
Whitelisted API endpoints for ZKTeco Attendance.
These are callable from JavaScript / REST.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime, nowdate, add_days, cint
from zkteco_attendance.zkteco_attendance.utils import has_column


@frappe.whitelist()
def test_connection(device_name):
    """Test connection to a biometric device and return device info."""
    frappe.only_for(["System Manager", "HR Manager", "Biometric Device Manager"])
    from zkteco_attendance.zkteco_attendance.zk_client import test_device_connection
    return test_device_connection(device_name)


@frappe.whitelist()
def start_pull_checkins(device_name, run_id=None):
    """
    Start a Pull Checkins sync as a BACKGROUND job and return immediately.

    The old `pull_checkins_now` ran the whole sync inside the web request, so
    gunicorn's worker timeout (and any reverse-proxy read timeout) killed
    long pulls with a "timed out" error. Queuing the sync instead means the
    HTTP request only takes milliseconds; progress is fetched from
    get_pull_progress (which also returns the final result once the job has
    finished).

    run_id is a client-generated token for this pull; it is echoed in every
    progress payload and in the final result so the client can ignore stale
    data from other runs.
    """
    frappe.only_for(["System Manager", "HR Manager", "Biometric Device Manager"])

    device = frappe.get_doc("Biometric Device", device_name)
    if not device.enable:
        frappe.throw(_("Device {0} is not enabled. Please enable it first.").format(device_name))

    run_id = run_id or frappe.generate_hash(length=10)

    frappe.enqueue(
        "zkteco_attendance.zkteco_attendance.sync_engine.run_sync_job",
        device_name=device_name,
        triggered_by="Manual",
        user=frappe.session.user,
        run_id=run_id,
        queue="long",
        timeout=3600,
        job_name="zkteco_pull_{}".format(device_name),
        enqueue_after_commit=True,
    )

    # Seed the progress cache so the client's first poll finds this run.
    from zkteco_attendance.zkteco_attendance.sync_engine import _emit_progress
    _emit_progress(device_name, frappe.session.user, "queued", message=_("Sync job queued..."),
                   run_id=run_id)

    return {"success": True, "run_id": run_id, "status": "queued"}


@frappe.whitelist()
def pull_checkins_now(device_name, run_id=None):
    """
    DEPRECATED foreground pull — kept for backward compatibility.

    Runs the whole sync synchronously inside the web request, which gunicorn
    or a reverse proxy kills with a timeout when the pull takes too long.
    The Biometric Device form and the zk-daily-checkins page now use
    `start_pull_checkins` instead. The live-progress machinery (realtime
    events + cached polling snapshot) still works the same way.
    """
    frappe.only_for(["System Manager", "HR Manager", "Biometric Device Manager"])

    device = frappe.get_doc("Biometric Device", device_name)
    if not device.enable:
        frappe.throw(_("Device {0} is not enabled. Please enable it first.").format(device_name))

    from zkteco_attendance.zkteco_attendance.sync_engine import sync_device

    result = sync_device(device_name, triggered_by="Manual", user=frappe.session.user,
                         run_id=run_id)
    return result


@frappe.whitelist()
def get_pull_progress(device_name, run_id=None):
    """
    Return the latest cached progress payload for a Pull Checkins run of this
    device by the current user (or None).

    Used as a polling fallback so live progress still displays when
    realtime/websocket events don't reach the browser. Pass the same run_id
    the pull was started with so stale progress from an older run is ignored.

    When the run has finished, the payload additionally carries the full
    result summary under `result` (read from the cached job result), so the
    client learns the outcome from this same poll even though the sync itself
    runs in a background worker.
    """
    frappe.only_for(["System Manager", "HR Manager", "Biometric Device Manager"])

    from zkteco_attendance.zkteco_attendance.sync_engine import get_live_progress, get_cached_run_result

    payload = get_live_progress(device_name, user=frappe.session.user, run_id=run_id)

    if payload and run_id and payload.get("stage") in ("done", "failed"):
        result = get_cached_run_result(device_name, run_id)
        if result:
            payload["result"] = result

    return payload


def _get_active_shift_types(employees):
    """
    Return {employee: {"assignment": <ZK Shift Assignment>, "shift_type":
    <ZK Shift Type>}} for the given employees' active ZK Shift Assignment,
    if any. An employee has at most one active assignment (enforced by
    ZK Shift Assignment.validate).
    """
    if not employees:
        return {}

    child_rows = frappe.get_all(
        "ZK Shift Assignment Employee",
        filters={"employee": ["in", employees]},
        fields=["employee", "parent"],
    )

    parent_names = list({r.parent for r in child_rows})
    active_shifts = {}
    if parent_names:
        for p in frappe.get_all(
            "ZK Shift Assignment",
            filters={"name": ["in", parent_names], "status": "Active"},
            fields=["name", "shift_type"],
        ):
            active_shifts[p.name] = p.shift_type

    assigned = {}
    for r in child_rows:
        if r.parent in active_shifts:
            assigned[r.employee] = {
                "assignment": r.parent,
                "shift_type": active_shifts[r.parent],
            }
    return assigned


def _apply_shift_assignment(employee, shift_type, device_company):
    """
    Move `employee` into the active ZK Shift Assignment for `shift_type`
    (creating that assignment for `device_company` if it does not exist
    yet), or remove them from their current assignment when shift_type is
    empty. Returns True when a change was made, False when the employee is
    already in the requested state. An assignment left without employees is
    deleted, mirroring how assignments are managed from the UI.
    """
    current = _get_active_shift_types([employee]).get(employee)
    cur_shift = current["shift_type"] if current else ""
    if cur_shift == (shift_type or ""):
        return False

    if current:
        old_doc = frappe.get_doc("ZK Shift Assignment", current["assignment"])
        for row in list(old_doc.employees or []):
            if row.employee == employee:
                old_doc.remove(row)
        if old_doc.employees:
            old_doc.save(ignore_permissions=True)
        else:
            frappe.delete_doc(
                "ZK Shift Assignment", current["assignment"],
                ignore_permissions=True,
            )

    if shift_type:
        target = frappe.get_all(
            "ZK Shift Assignment",
            filters={
                "shift_type": shift_type,
                "company": device_company,
                "status": "Active",
            },
            fields=["name"],
            limit_page_length=1,
        )
        if target:
            sa_doc = frappe.get_doc("ZK Shift Assignment", target[0].name)
        else:
            sa_doc = frappe.new_doc("ZK Shift Assignment")
            sa_doc.shift_type = shift_type
            sa_doc.company = device_company
            sa_doc.status = "Active"

        emp = frappe.db.get_value(
            "Employee", employee,
            ["employee_name", "department", "designation"], as_dict=True,
        ) or {}
        sa_doc.append("employees", {
            "employee": employee,
            "employee_name": emp.get("employee_name"),
            "department": emp.get("department"),
            "designation": emp.get("designation"),
        })
        sa_doc.save(ignore_permissions=True)

    return True


@frappe.whitelist()
def get_device_users(device_name):
    """
    Return the users enrolled on a biometric device, annotated with the
    ERPNext Employee currently mapped to each user_id (via
    attendance_device_id), so the Biometric Device form's
    "Browse Employees On Device" dialog can show and edit the mapping.
    Each user also carries the shift_type of the employee's active ZK Shift
    Assignment (empty when unmapped or unassigned).
    """
    frappe.only_for(["System Manager", "HR Manager", "Biometric Device Manager"])

    device = frappe.get_doc("Biometric Device", device_name)

    from zkteco_attendance.zkteco_attendance.zk_client import get_device_users as fetch_users

    users = fetch_users(device_name)

    # Which device user_ids are already mapped to an Employee (of this
    # device's company) through attendance_device_id?
    mapped_by_id = {}
    if users:
        user_ids = list({u["user_id"] for u in users})
        rows = frappe.get_all(
            "Employee",
            filters={
                "attendance_device_id": ["in", user_ids],
                "zk_biometric_device": device.name,
                "status": "Active",
            },
            fields=["name", "employee_name", "attendance_device_id", "first_name", "fullname"],
        )
        for row in rows:
            mapped_by_id[str(row.attendance_device_id)] = row

    # Current ZK Shift Assignment per mapped employee, so the mapping
    # dialog can pre-fill the Shift Type column.
    assigned_shifts = {}
    if users:
        emp_names = [row.name for row in mapped_by_id.values()]
        if emp_names:
            assigned_shifts = _get_active_shift_types(emp_names)

    for u in users:
        emp = mapped_by_id.get(u["user_id"])
        u["employee"] = emp.name if emp else ""
        u["employee_name"] = emp.employee_name if emp else ""
        u["first_name"] = emp.first_name if emp else ""
        u["fullname"] = emp.fullname if emp else ""
        u["shift_type"] = (assigned_shifts.get(emp.name) or {}).get("shift_type", "") if emp else ""

    return {"success": True, "users": users, "count": len(users)}


@frappe.whitelist()
def map_device_employees(device_name, mappings):
    """
    Save employee mappings from the "Browse Employees On Device" dialog.

    mappings is a list of {user_id, employee, shift_type} dicts (possibly a
    JSON string when sent from JS; shift_type is optional). For every device
    user_id the Employee's attendance_device_id is set to that user_id and
    zk_biometric_device is set to this device — the combination the sync
    engine requires to match a punch to an employee. Passing employee=""
    clears the mapping for that device user. Previously-mapped employees of
    this device whose user_id is no longer present in mappings are unmapped
    (their attendance_device_id is cleared) so stale mappings never linger.

    shift_type moves the employee into the active ZK Shift Assignment for
    that ZK Shift Type (creating the assignment if needed); an empty
    shift_type removes the employee from their current assignment. Rows
    that omit the shift_type key entirely leave shift assignments
    untouched (backward compatibility with older callers).
    """
    frappe.only_for(["System Manager", "HR Manager", "Biometric Device Manager"])

    import json as _json

    device = frappe.get_doc("Biometric Device", device_name)

    if isinstance(mappings, str):
        try:
            mappings = _json.loads(mappings)
        except Exception:
            frappe.throw(_("Invalid mappings payload."))

    if not isinstance(mappings, list):
        frappe.throw(_("Mappings must be a list of user_id / employee pairs."))

    # ── Validate & normalise ────────────────────────────────────────────
    seen_ids = set()
    employees_to_map = {}   # employee -> user_id
    ids_to_clear = set()    # user_ids explicitly unmapped
    shifts_by_employee = {}  # employee -> requested shift_type ("") to unassign

    for m in mappings:
        if not isinstance(m, dict) or not m.get("user_id"):
            frappe.throw(_("Each mapping must include a device user_id."))
        user_id = str(m["user_id"])
        employee = (m.get("employee") or "").strip()

        if user_id in seen_ids:
            frappe.throw(_("Duplicate device user_id in mappings: {0}").format(user_id))
        seen_ids.add(user_id)

        if not employee:
            ids_to_clear.add(user_id)
            continue

        if not frappe.db.exists("Employee", employee):
            frappe.throw(_("Employee {0} does not exist.").format(employee))

        emp_company = frappe.db.get_value("Employee", employee, "company")
        if device.company and emp_company != device.company:
            frappe.throw(
                _("Employee {0} belongs to company {1}, but the device is set up for {2}.").format(
                    employee, emp_company, device.company
                )
            )

        # Shift changes are opt-in per row: only rows that explicitly carry
        # a shift_type key are processed, so legacy payloads (and rows that
        # omit the field) never touch existing shift assignments. An empty
        # value unassigns the employee; a value names the ZK Shift Type to
        # move them to.
        if "shift_type" in m:
            shift_type = (m.get("shift_type") or "").strip()

            if shift_type and not frappe.db.exists("ZK Shift Type", shift_type):
                frappe.throw(_("ZK Shift Type {0} does not exist.").format(shift_type))

            if shift_type:
                shift_company = frappe.db.get_value("ZK Shift Type", shift_type, "company")
                if device.company and shift_company and shift_company != device.company:
                    frappe.throw(
                        _("ZK Shift Type {0} belongs to company {1}, but the device is set up for {2}.").format(
                            shift_type, shift_company, device.company
                        )
                    )

            shifts_by_employee[employee] = shift_type

        employees_to_map[employee] = user_id

    # ── Unmap device ids no longer mapped (stale mappings cleanup) ─────
    if seen_ids:
        stale = frappe.get_all(
            "Employee",
            filters={
                "zk_biometric_device": device.name,
                "attendance_device_id": ["in", list(seen_ids)],
            },
            fields=["name"],
        )
        for row in stale:
            if row.name not in employees_to_map:
                frappe.db.set_value("Employee", row.name, "attendance_device_id", None)

    # ── Apply new mappings (and re-maps) ───────────────────────────────
    for employee, user_id in employees_to_map.items():
        frappe.db.set_value("Employee", employee, {
            "attendance_device_id": user_id,
            "zk_biometric_device": device.name,
        }, update_modified=True)

    # ── Apply requested ZK Shift Assignments ───────────────────────────
    # "changed" means created / moved / removed; "skipped" means the
    # employee is already in the requested state.
    shifts_changed = 0
    for employee, shift_type in shifts_by_employee.items():
        if _apply_shift_assignment(employee, shift_type, device.company):
            shifts_changed += 1

    frappe.db.commit()

    return {
        "success": True,
        "mapped": len(employees_to_map),
        "unmapped": len(ids_to_clear),
        "shifts_assigned": shifts_changed,
    }


@frappe.whitelist()
def sync_device(device_name):
    """
    Trigger a background sync for a single device (legacy/queue-based path,
    kept for scripts and the scheduler). For interactive use from the
    Biometric Device form, prefer `pull_checkins_now`, which runs in the
    foreground and reports live progress.
    Result is visible in Attendance Sync Log.
    """
    frappe.only_for(["System Manager", "HR Manager", "Biometric Device Manager"])

    device = frappe.get_doc("Biometric Device", device_name)
    if not device.enable:
        frappe.throw(_("Device {0} is not enabled. Please enable it first.").format(device_name))

    frappe.enqueue(
        "zkteco_attendance.zkteco_attendance.sync_engine.sync_device",
        device_name=device_name,
        triggered_by="Manual",
        queue="long",
        timeout=600,
        job_name="zkteco_sync_{}".format(device_name),
    )

    return {
        "status": "queued",
        "message": _("Sync job queued for device {0}. Check Attendance Sync Log for results.").format(device_name),
    }


@frappe.whitelist()
def sync_all_devices():
    """Trigger background sync for all active devices."""
    frappe.only_for(["System Manager", "HR Manager", "Biometric Device Manager"])

    frappe.enqueue(
        "zkteco_attendance.zkteco_attendance.sync_engine.sync_all_active_devices",
        triggered_by="Manual",
        queue="long",
        timeout=600,
        job_name="zkteco_sync_all",
    )

    return {
        "status": "queued",
        "message": _("Sync jobs queued for all active devices. Check Attendance Sync Log for results."),
    }


@frappe.whitelist()
def get_device_status(device_name=None):
    """Get status summary for one or all devices."""
    frappe.only_for(["System Manager", "HR Manager", "Biometric Device Manager"])

    filters = {"name": device_name} if device_name else {}
    return frappe.get_all(
        "Biometric Device",
        filters=filters,
        fields=["name", "device_name", "device_ip", "status", "last_sync_time",
                "auto_sync_enabled", "sync_frequency"]
    )


@frappe.whitelist()
def get_sync_logs(device_name=None, limit=20):
    """Retrieve recent sync logs."""
    frappe.only_for(["System Manager", "HR Manager", "Biometric Device Manager"])

    filters = {}
    if device_name:
        filters["device"] = device_name

    fields = [
        "name", "device", "start_time", "end_time",
        "total_records_pulled", "new_records_created",
        "duplicate_records", "failed_records",
        "sync_status", "triggered_by"
    ]

    if has_column("Attendance Sync Log", "overtime_records"):
        fields.append("overtime_records")

    return frappe.get_all(
        "Attendance Sync Log",
        filters=filters,
        fields=fields,
        order_by="start_time desc",
        limit=int(limit),
    )


@frappe.whitelist()
def get_latest_sync_log(device_name):
    """Return the single most recent Attendance Sync Log row for a device."""
    frappe.only_for(["System Manager", "HR Manager", "Biometric Device Manager"])

    logs = get_sync_logs(device_name=device_name, limit=1)
    return logs[0] if logs else None


@frappe.whitelist()
def get_daily_checkins(attendance_summary=None, from_date=None, to_date=None,
                       employee_list=None, company=None, biometric_device=None,
                       filter_employee=None, filter_project=None):
    """
    Return per-employee, per-day checkin breakdown.
    Accepts either an Attendance Summary name OR a direct date range + employee list.
    """
    frappe.only_for(["System Manager", "HR Manager", "Biometric Device Manager"])

    from zkteco_attendance.zkteco_attendance.attendance_processor import get_daily_checkins_data

    if not attendance_summary and not (from_date and to_date):
        frappe.throw(_("Either Attendance Summary or From Date + To Date are required."))

    # employee_list may come as JSON string from JS
    if employee_list and isinstance(employee_list, str):
        import json as _json
        try:
            employee_list = _json.loads(employee_list)
        except Exception:
            employee_list = [e.strip() for e in employee_list.split(",") if e.strip()]

    # filter_employee overrides employee_list if provided
    if filter_employee:
        employee_list = [filter_employee]

    return get_daily_checkins_data(
        attendance_summary=attendance_summary,
        from_date=from_date,
        to_date=to_date,
        employee_list=employee_list or None,
        company=company,
        biometric_device=biometric_device,
        project=filter_project,
    )


@frappe.whitelist()
def save_manual_checkin(attendance_summary=None, employee=None, checkin_time=None,
                        log_type=None, checkin_name=None, is_overtime=0, remark=None):
    """
    Add or update an Employee Checkin manually from the Daily Checkins dashboard.
    Works standalone (attendance_summary=None) or within an Attendance Summary
    context (which also validates the employee belongs to the summary).
    Records edited_by / edited_at for audit trail, plus an optional remark.
    """
    frappe.only_for(["System Manager", "HR Manager", "Biometric Device Manager",
                    "Checkin Editor"])

    from zkteco_attendance.zkteco_attendance.attendance_processor import save_manual_checkin_record

    if attendance_summary:
        doc = frappe.get_doc("Attendance Summary", attendance_summary)
        return doc.save_manual_checkin(
            employee=employee,
            checkin_time=checkin_time,
            log_type=log_type,
            checkin_name=checkin_name,
            is_overtime=is_overtime,
            remark=remark,
        )

    # Standalone: no Attendance Summary required
    return save_manual_checkin_record(
        employee=employee,
        checkin_time=checkin_time,
        log_type=log_type,
        checkin_name=checkin_name,
        is_overtime=is_overtime,
        remark=remark,
    )


@frappe.whitelist()
def create_manual_checkin_request(employee=None, checkin_date=None, checkin_time=None,
                                  log_type="IN", is_overtime=0, attendance_summary=None,
                                  checkin_name=None, remarks=None, request_type="New"):
    """
    Create a Manual Checkin Request (Draft) from the Daily Checkins page or
    the Attendance Summary "Add Check-in" button.

    The Employee Checkin is NOT touched here — it is only created or updated
    when the request document is submitted (see ManualCheckinRequest.on_submit).
    """
    frappe.only_for(["System Manager", "HR Manager", "Biometric Device Manager",
                    "Checkin Editor"])

    if not employee or not checkin_date or not checkin_time:
        frappe.throw(_("Employee, Check-in Date, and Check-in Time are required."))
    if log_type not in ("IN", "OUT"):
        frappe.throw(_("Log Type must be IN or OUT."))
    if request_type not in ("New", "Edit"):
        frappe.throw(_("Request Type must be New or Edit."))
    if request_type == "Edit" and not checkin_name:
        frappe.throw(_("An Existing Check-in must be set when Request Type is Edit."))

    doc = frappe.get_doc({
        "doctype": "Manual Checkin Request",
        "employee": employee,
        "checkin_date": checkin_date,
        "checkin_time": checkin_time,
        "log_type": log_type,
        "is_overtime": cint(is_overtime),
        "attendance_summary": attendance_summary or None,
        "checkin_name": checkin_name or None,
        "request_remarks": remarks,
        "request_type": request_type,
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()

    return {"name": doc.name, "action": "created", "status": "Draft"}


@frappe.whitelist()
def toggle_ignore_checkin(checkin_name):
    """
    Toggle the 'ignored' flag on an Employee Checkin.
    Ignored checkins are excluded from attendance processing.
    """
    frappe.only_for(["System Manager", "HR Manager", "Biometric Device Manager",
                    "Checkin Editor"])

    from zkteco_attendance.zkteco_attendance.utils import has_column

    if not checkin_name or not frappe.db.exists("Employee Checkin", checkin_name):
        frappe.throw(_("Invalid checkin record."))

    if not has_column("Employee Checkin", "zk_ignored"):
        frappe.throw(_("The ignored field is not available. Please run the latest patch."))

    doc = frappe.get_doc("Employee Checkin", checkin_name)
    current_val = bool(doc.get("zk_ignored"))
    doc.db_set("zk_ignored", 0 if current_val else 1, update_modified=False)
    frappe.db.commit()

    return {
        "name": checkin_name,
        "ignored": not current_val,
        "action": "unignored" if current_val else "ignored",
    }


@frappe.whitelist()
def get_employee_shift_info(employee, work_date=None):
    """
    Return the employee's shift details for a given date.
    Used by the checkin dialog to display shift context.
    """
    from zkteco_attendance.zkteco_attendance.attendance_processor import get_employee_shift_info
    return get_employee_shift_info(employee, work_date)


@frappe.whitelist()
def get_dashboard_data():
    """Aggregate data (including chart series) for the ZKTeco dashboard page."""
    frappe.only_for(["System Manager", "HR Manager", "Biometric Device Manager"])

    total_devices = frappe.db.count("Biometric Device")
    online_devices = frappe.db.count("Biometric Device", {"enable": 1})
    offline_devices = total_devices - online_devices

    today = nowdate()

    # Use frappe.db.sql for date-function filtering -- frappe.db.count()
    # does not support SQL expressions as filter keys (v14/15/16).
    todays_checkins = frappe.db.sql(
        "SELECT COUNT(*) FROM `tabEmployee Checkin` WHERE DATE(`time`) = %s",
        (today,)
    )[0][0]

    # Check if Attendance Sync Log table exists before querying
    failed_syncs_today = 0
    if frappe.db.table_exists("Attendance Sync Log"):
        failed_syncs_today = frappe.db.sql(
            "SELECT COUNT(*) FROM `tabAttendance Sync Log` WHERE sync_status = 'Failed' AND DATE(start_time) = %s",
            (today,)
        )[0][0]

    last_sync = None
    if frappe.db.table_exists("Attendance Sync Log"):
        rows = frappe.db.sql(
            """SELECT device, start_time, sync_status
               FROM `tabAttendance Sync Log`
               ORDER BY start_time DESC
               LIMIT 1""",
            as_dict=True
        )
        last_sync = rows[0] if rows else None

    # ── Chart 1: Check-ins per day for the last 7 days ──────────────────────
    checkins_chart = _checkins_last_n_days(7)

    # ── Chart 2: Sync results per day for the last 7 days ───────────────────
    sync_chart = _sync_results_last_n_days(7)

    # ── Chart 3: Device status breakdown (pie/donut) ────────────────────────
    device_status_chart = {
        "labels": [_("Online"), _("Offline")],
        "values": [online_devices, offline_devices],
    }

    # ── Chart 4: Today's IN vs OUT vs Overtime checkins ─────────────────────
    punch_breakdown_chart = _todays_punch_breakdown(today)

    return {
        "total_devices": total_devices,
        "online_devices": online_devices,
        "offline_devices": offline_devices,
        "todays_checkins": todays_checkins,
        "failed_syncs_today": failed_syncs_today,
        "last_sync": last_sync,
        "charts": {
            "checkins_last_7_days": checkins_chart,
            "sync_results_last_7_days": sync_chart,
            "device_status": device_status_chart,
            "todays_punch_breakdown": punch_breakdown_chart,
        },
    }


def _checkins_last_n_days(n=7):
    """Daily Employee Checkin counts for the last n days (line/bar chart)."""
    start_date = add_days(nowdate(), -(n - 1))

    rows = frappe.db.sql(
        """SELECT DATE(`time`) as day, COUNT(*) as cnt
           FROM `tabEmployee Checkin`
           WHERE DATE(`time`) BETWEEN %s AND %s
           GROUP BY DATE(`time`)
           ORDER BY day ASC""",
        (start_date, nowdate()),
        as_dict=True,
    )
    counts_by_day = {str(r["day"]): r["cnt"] for r in rows}

    labels = []
    values = []
    for i in range(n):
        d = add_days(start_date, i)
        labels.append(frappe.utils.formatdate(d, "dd MMM"))
        values.append(counts_by_day.get(str(d), 0))

    return {"labels": labels, "values": values}


def _sync_results_last_n_days(n=7):
    """Daily sync record counts (new/duplicate/failed) for the last n days."""
    if not frappe.db.table_exists("Attendance Sync Log"):
        return {"labels": [], "new": [], "duplicate": [], "failed": []}

    start_date = add_days(nowdate(), -(n - 1))

    rows = frappe.db.sql(
        """SELECT DATE(start_time) as day,
                  SUM(new_records_created) as new_records,
                  SUM(duplicate_records) as dupes,
                  SUM(failed_records) as failed
           FROM `tabAttendance Sync Log`
           WHERE DATE(start_time) BETWEEN %s AND %s
           GROUP BY DATE(start_time)
           ORDER BY day ASC""",
        (start_date, nowdate()),
        as_dict=True,
    )
    by_day = {str(r["day"]): r for r in rows}

    labels, new_vals, dupe_vals, failed_vals = [], [], [], []
    for i in range(n):
        d = add_days(start_date, i)
        key = str(d)
        labels.append(frappe.utils.formatdate(d, "dd MMM"))
        row = by_day.get(key)
        new_vals.append(cint(row["new_records"]) if row else 0)
        dupe_vals.append(cint(row["dupes"]) if row else 0)
        failed_vals.append(cint(row["failed"]) if row else 0)

    return {"labels": labels, "new": new_vals, "duplicate": dupe_vals, "failed": failed_vals}


def _todays_punch_breakdown(today):
    """Count of IN / OUT / Overtime Employee Checkins created today."""
    has_overtime_col = has_column("Employee Checkin", "is_overtime")

    in_count = frappe.db.sql(
        "SELECT COUNT(*) FROM `tabEmployee Checkin` WHERE DATE(`time`) = %s AND log_type = 'IN'",
        (today,)
    )[0][0]
    out_count = frappe.db.sql(
        "SELECT COUNT(*) FROM `tabEmployee Checkin` WHERE DATE(`time`) = %s AND log_type = 'OUT'",
        (today,)
    )[0][0]

    overtime_count = 0
    if has_overtime_col:
        overtime_count = frappe.db.sql(
            "SELECT COUNT(*) FROM `tabEmployee Checkin` WHERE DATE(`time`) = %s AND is_overtime = 1",
            (today,)
        )[0][0]

    return {
        "labels": [_("IN"), _("OUT"), _("Overtime")],
        "values": [cint(in_count), cint(out_count), cint(overtime_count)],
    }
