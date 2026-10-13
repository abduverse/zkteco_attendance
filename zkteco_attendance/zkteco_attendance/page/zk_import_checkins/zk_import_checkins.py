import frappe
from frappe import _

from frappe.utils import get_datetime

from zkteco_attendance.zkteco_attendance.sync_engine import (
    create_employee_checkin,
    get_employee_by_biometric_id,
    filter_double_punches,
)


@frappe.whitelist()
def import_checkins(device_name=None, rows=None, dry_run=0):
    """
    Import attendance punches (e.g. logs exported from a device at another
    branch) into Employee Checkins.

    `rows` is a JSON array of {user_id, timestamp, log_type?} objects — the
    page JS parses the user's CSV file client-side and posts the rows here.

    Dedup is the same two-layer net the device pull uses:
      1. Existing checkins for this device in the imported date range are
         loaded once in bulk; a row whose time is within ±60s of an existing
         one for the same employee is recorded as duplicate (skipped).
      2. Double punches within the same file (same employee within 60s) are
         collapsed via filter_double_punches.
      3. ERPNext's DuplicateEntryError is treated as duplicate too.

    dry_run=1 performs the whole resolution pass and returns counts +
    problems, creating nothing — used for the page's Preview step.

    Unknown biometric IDs are reported back (not silently dropped), so the
    user can map them on the device first and re-import.
    """
    from frappe.utils import now_datetime

    device_name = (device_name or "").strip()
    if not device_name:
        frappe.throw(_("Device is required."))
    if not frappe.db.exists("Biometric Device", device_name):
        frappe.throw(_("Device {0} does not exist.").format(device_name))
    device = frappe.get_doc("Biometric Device", device_name)
    if not device.enable:
        frappe.throw(_("Device {0} is not enabled.").format(device_name))

    device_name_field = device.device_name or device.name

    try:
        records = frappe.parse_json(rows) or []
    except Exception:
        frappe.throw(_("Could not parse the uploaded rows."))

    if not isinstance(records, list) or not records:
        frappe.throw(_("No rows to import."))

    total_received = len(records)

    # ── Normalize + validate rows ──────────────────────────────────────
    parsed_pool = []       # [{user_id, timestamp(datetime), log_type?, punch?}]
    problems = []          # row-level problems reported to the user
    bad_rows = 0

    for idx, rec in enumerate(records):
        user_id = str((rec.get("user_id") or "")).strip()
        ts_raw = rec.get("timestamp") or rec.get("time") or ""
        log_type = (rec.get("log_type") or "").strip().upper() or None
        if not user_id or not ts_raw:
            problems.append(_("Row {0}: missing user_id or time.").format(idx + 1))
            bad_rows += 1
            continue
        try:
            ts = get_datetime(ts_raw)
        except Exception:
            problems.append(_("Row {0}: unreadable time {1}.").format(idx + 1, ts_raw))
            bad_rows += 1
            continue
        if log_type not in (None, "IN", "OUT"):
            log_type = None  # let the engine alternate

        parsed_pool.append({
            "user_id": user_id,
            "timestamp": ts,
            "log_type": log_type,
            "punch": rec.get("punch"),
        })

    if not parsed_pool:
        return {
            "success": False,
            "error": _("No usable rows in the file."),
            "problems": problems[:50],
        }

    # ── Employee resolution (bulk, cached) ─────────────────────────────
    emp_cache = {}
    unmatched = {}

    # Reuses group_records_by_attendance_date from the sync engine so an
    # imported punch is attributed to the same attendance date the device
    # pull would give it (night-shift early-AM rollback), keeping Attendance
    # Summary consistent between the pull and import paths.
    from zkteco_attendance.zkteco_attendance.sync_engine import group_records_by_attendance_date
    grouped, emp_cache = group_records_by_attendance_date(parsed_pool, device)

    for (uid, _date), day_recs in grouped.items():
        if uid not in emp_cache or not emp_cache.get(uid):
            unmatched[uid] = unmatched.get(uid, 0) + len(day_recs)

    # Drop rows with no matching Employee; report them.
    resolvable = [rec for rec in parsed_pool
                  if emp_cache.get(rec["user_id"])]
    skipped_no_employee = total_received - bad_rows - len(resolvable)

    if not resolvable:
        return {
            "success": False,
            "error": _("No rows matched an Employee. Map the biometric IDs first "
                       "on the device, then import again."),
            "unmatched_ids": sorted(unmatched.keys())[:50],
            "problems": problems[:50],
        }

    # ── Duplicate detection (bulk, like the device pull) ───────────────
    earliest = min(rec["timestamp"] for rec in resolvable)
    employee_names = {emp_cache[rec["user_id"]]["name"] for rec in resolvable}
    from datetime import timedelta
    from zkteco_attendance.zkteco_attendance.sync_engine import get_existing_checkins
    existing_pairs = get_existing_checkins(
        employee_names, device_name, after=earliest - timedelta(seconds=61)
    )

    # ── Collapse intra-file double punches (same employee within 60s) ──
    kept, double_punches = filter_double_punches(resolvable)

    # ── Create / count checkins ────────────────────────────────────────
    dry_run = 1 if (dry_run in (1, "1", True)) else 0
    created = 0
    duplicates = 0
    failed = 0
    errors = []

    if not dry_run:
        frappe.only_for(["System Manager", "HR Manager", "Biometric Device Manager"])

    for rec in kept:
        emp = emp_cache[rec["user_id"]]
        rec_ts = rec["timestamp"]
        dup = any(known_emp == emp["name"] and abs((known_ts - rec_ts).total_seconds()) <= 60
                  for known_emp, known_ts in existing_pairs)
        if dup:
            duplicates += 1
            continue
        existing_pairs.add((emp["name"], rec_ts))

        if dry_run:
            created += 1
            continue

        try:
            create_employee_checkin(
                emp["name"], emp["employee_name"], rec_ts,
                rec.get("log_type") or "IN", device_name,
                uid=rec.get("user_id"),
            )
            created += 1
        except Exception as e:
            err_msg = str(e)
            if "already has a log with the same timestamp" in err_msg or \
               "DuplicateEntryError" in type(e).__name__:
                duplicates += 1
                continue
            failed += 1
            errors.append("uid={}: {}".format(rec["user_id"], err_msg))

    if not dry_run:
        frappe.db.commit()

    return {
        "success": True,
        "dry_run": bool(dry_run),
        "device": device_name_field,
        "total_rows": total_received,
        "created": created,
        "duplicates": duplicates,
        "double_punches": double_punches,
        "skipped_no_employee": skipped_no_employee,
        "unmatched_ids": sorted(unmatched.keys())[:50],
        "bad_rows": bad_rows,
        "failed": failed,
        "errors": errors[:20],
        "problems": problems[:50],
    }
