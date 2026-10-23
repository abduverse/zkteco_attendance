import io
import csv
import re
import base64
from datetime import date, datetime, time as dt_time, timedelta

import frappe
from frappe import _

from frappe.utils import get_datetime

from zkteco_attendance.zkteco_attendance.sync_engine import (
    create_employee_checkin,
    get_employee_by_biometric_id,
    filter_double_punches,
)


# ── Upload parsing (CSV / Excel) ───────────────────────────────────────
# People keep device exports in both formats, and Excel's default "Save As"
# is .xlsx rather than CSV, so the page accepts both. The file is parsed
# here instead of in the browser for two reasons: an .xlsx is a zip of XML
# (not text), and having one parser means the header rules below cannot
# drift between the two formats.

MAX_IMPORT_ROWS = 50000
HEADER_SCAN_ROWS = 10

# Header names are normalised (lowercase, punctuation dropped) before
# matching, so "User ID", "user-id" and "user_id" all become "userid".
ID_HEADERS = {
    "userid", "id", "badge", "badgenumber", "employeeid", "empid",
    "employeenumber", "personid", "enrollid", "attendancedeviceid",
    "deviceuserid", "fingerprintid", "pin",
}
TIME_HEADERS = {
    "time", "timestamp", "datetime", "dateandtime", "punch", "punchtime",
    "punchdatetime", "checkin", "checkintime", "clocktime", "logtime",
    "attendancetime", "recordtime",
}
LOG_HEADERS = {
    "logtype", "type", "status", "inout", "punchtype", "punchstate",
    "direction", "attendance",
}
# Some exports split the punch across two columns instead of one timestamp.
DATE_HEADERS = {
    "date", "attendancedate", "punchdate", "logdate", "recorddate",
    "checkindate", "datestamp", "day",
}


def _normalize_header(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _cell_text(value):
    """Render one spreadsheet cell as the plain text the importer expects."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, dt_time):
        return value.strftime("%H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _excel_serial_to_datetime(value):
    """Convert an unformatted Excel date serial (e.g. 45678.34) to datetime."""
    try:
        num = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not 20000 <= num <= 80000:  # any plausible calendar date
        return None
    return datetime(1899, 12, 30) + timedelta(days=num)


def _parse_date_text(text):
    """Return a date for a date cell, or None.

    Accepts ISO dates, day-first and month-first numeric dates (decided by
    the values themselves wherever they are unambiguous), Excel date serials
    and spelled-out months such as "22 Oct 2026".
    """
    text = str(text or "").strip()
    if not text:
        return None

    # A date column may still carry a clock: Excel stores dates as
    # datetimes, so a "Date" cell often arrives as 2026-02-01 00:00:00.
    text = re.sub(
        r"[ T]\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?\s*(?:[ap]\.?m\.?)?$",
        "",
        text,
        flags=re.I,
    ).strip()
    if not text:
        return None

    if re.fullmatch(r"\d+(\.\d+)?", text):
        serial = _excel_serial_to_datetime(text)
        return serial.date() if serial else None

    month_names = {
        name: number for number, name in enumerate(
            ["jan", "feb", "mar", "apr", "may", "jun",
             "jul", "aug", "sep", "oct", "nov", "dec"],
            start=1,
        )
    }
    spelled = (re.match(r"^\s*(\d{1,2})[\s\-./]+([A-Za-z]{3,})", text)
               or re.match(r"^\s*([A-Za-z]{3,})[\s\-./]+(\d{1,2})", text))
    if spelled:
        first, second = spelled.group(1), spelled.group(2)
        day_text, month_text = (first, second) if first.isdigit() else (second, first)
        month = month_names.get(month_text[:3].lower())
        year_match = re.search(r"(\d{4})", text)
        if month and year_match:
            try:
                return date(int(year_match.group(1)), month, int(day_text))
            except ValueError:
                return None

    numeric = re.fullmatch(r"(\d{1,4})[-/.\s](\d{1,2})[-/.\s](\d{1,4})", text)
    if numeric:
        first, second, third = numeric.groups()
        if len(first) == 4:  # 2026-02-01
            year, month, day = int(first), int(second), int(third)
        else:
            year = int(third)
            if year < 100:
                year += 2000 if year < 70 else 1900
            if int(first) > 12:  # 22/10/2026
                day, month = int(first), int(second)
            elif int(second) > 12:  # 10/22/2026
                month, day = int(first), int(second)
            else:  # genuinely ambiguous: exports are day-first
                day, month = int(first), int(second)
        try:
            return date(year, month, day)
        except ValueError:
            return None

    return None


def _parse_time_text(text):
    """Return a time for a time cell, or None.

    Accepts hh:mm[:ss], optional am/pm, and Excel time serials — either the
    plain fraction of a day (0.6875) or a full timestamp serial.
    """
    text = str(text or "").strip()
    if not text:
        return None

    if re.fullmatch(r"\d+(\.\d+)?", text):
        num = float(text)
        if 0 <= num < 1:
            seconds = round(num * 86400)
        elif 20000 <= num <= 80000:  # full serial: keep the clock part
            seconds = round((num % 1) * 86400)
        else:
            return None
        seconds %= 86400
        return dt_time(seconds // 3600, (seconds % 3600) // 60, seconds % 60)

    # Not anchored at the start: a cell may hold a whole timestamp whose
    # clock we still want ("2026-02-01 08:02:11").
    match = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([ap]\.?m\.?)?$", text, re.I)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        second = int(match.group(3) or 0)
        meridiem = (match.group(4) or "").replace(".", "").lower()
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        try:
            return dt_time(hour, minute, second)
        except ValueError:
            return None

    return None


def _combine_date_time(date_text, time_text):
    """Join separate date and time cells into 'YYYY-MM-DD HH:MM:SS'.

    Returns "" when the date cell cannot be read, so the caller can fall
    back to whatever the time cell holds on its own.
    """
    day = _parse_date_text(date_text)
    if day is None:
        return ""
    clock = _parse_time_text(time_text) or dt_time(0, 0, 0)
    return datetime.combine(day, clock).strftime("%Y-%m-%d %H:%M:%S")


def _decode_text(raw):
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Excel on a legacy Windows locale saves CSV as cp1252
        return raw.decode("cp1252", errors="replace")


def _read_table(filename, raw):
    """Return (rows, error). Each row is a list of cell strings."""
    lower = (filename or "").lower()

    if raw[:2] == b"PK" or lower.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        try:
            workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        except Exception:
            return None, _(
                "This Excel file could not be opened. Re-save it as .xlsx or "
                "CSV and try again."
            )
        try:
            rows = [
                [_cell_text(value) for value in row]
                for row in workbook.active.iter_rows(values_only=True)
            ]
        finally:
            workbook.close()
        return rows, None

    if raw[:4] == b"\xd0\xcf\x11\xe0":
        return None, _(
            "Old .xls workbooks are not supported. Open the file in Excel and "
            "save it as .xlsx or CSV, then upload again."
        )

    text = _decode_text(raw)
    sample = text[:8192]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        # heading/data rows alone are not enough for the sniffer
        delimiter = "\t" if sample.count("\t") > sample.count(",") else ","

    rows = [
        [(cell or "").strip() for cell in row]
        for row in csv.reader(io.StringIO(text), delimiter=delimiter)
    ]
    return rows, None


def _find_header(rows):
    """Locate the header row and the columns we need.

    The header is not always the first line (exports often carry a title or
    a report-date line above it), so the first few rows are scanned for one
    that contains both an id column and a time column. Some exports split
    the punch into a date column and a time column; that date column is
    reported too so the two can be joined row by row.

    Returns (row_index, id_index, time_index, log_index, date_index,
    header_cells) or None when no such row exists.
    """
    for row_index, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        normalized = [_normalize_header(cell) for cell in row]
        id_index = next((i for i, h in enumerate(normalized) if h in ID_HEADERS), None)
        time_index = next((i for i, h in enumerate(normalized) if h in TIME_HEADERS), None)
        if id_index is not None and time_index is not None:
            log_index = next((i for i, h in enumerate(normalized) if h in LOG_HEADERS), None)
            date_index = next(
                (i for i, h in enumerate(normalized) if h in DATE_HEADERS and i != time_index),
                None,
            )
            return row_index, id_index, time_index, log_index, date_index, row
    return None


@frappe.whitelist()
def parse_checkins_file(filename=None, content=None):
    """Parse an uploaded CSV/Excel file into importable checkin rows.

    `content` is the base64-encoded file as read by the page. Returns
    {success, rows: [{user_id, timestamp, log_type}]} plus, on a header
    failure, the first row of the file so the message can show what was
    actually found.
    """
    if content is None:
        return {"success": False, "error": _("No file content was received.")}

    try:
        raw = base64.b64decode(content, validate=False)
    except Exception:
        return {"success": False, "error": _("Could not read the uploaded file.")}

    rows, error = _read_table(filename, raw)
    if error:
        return {"success": False, "error": error}
    if not rows or not any(any(cell for cell in row) for row in rows):
        return {"success": False, "error": _("The file is empty.")}

    found = _find_header(rows)
    if not found:
        first_row = next((r for r in rows if any(c for c in r)), [])
        return {
            "success": False,
            "error": _(
                "Could not find a user_id column and a time column in the "
                "file. Expected a header row like: user_id, time, log_type "
                "(or a separate date column and time column)"
            ),
            "header": [c for c in first_row if str(c).strip()][:15],
        }

    header_index, id_index, time_index, log_index, date_index, _header = found

    parsed = []
    for row in rows[header_index + 1:]:
        if not any(str(cell).strip() for cell in row):
            continue
        cell = lambda i: str(row[i]).strip() if i is not None and i < len(row) else ""
        user_id = cell(id_index)
        if date_index is not None:
            timestamp = _combine_date_time(cell(date_index), cell(time_index)) \
                or cell(time_index)
        else:
            timestamp = cell(time_index)
        if timestamp and not re.search(r"[-:]", timestamp):
            serial = _excel_serial_to_datetime(timestamp)
            if serial:
                timestamp = serial.strftime("%Y-%m-%d %H:%M:%S")
        if not user_id and not timestamp:
            continue
        parsed.append({
            "user_id": user_id,
            "timestamp": timestamp,
            "log_type": cell(log_index),
        })
        if len(parsed) > MAX_IMPORT_ROWS:
            return {
                "success": False,
                "error": _("This file has more than {0} rows. Split it into "
                           "smaller files and import them one at a time.").format(MAX_IMPORT_ROWS),
            }

    if not parsed:
        return {"success": False, "error": _("No data rows found in the file.")}

    return {"success": True, "rows": parsed, "total_rows": len(parsed)}


@frappe.whitelist()
def import_checkins(device_name=None, rows=None, dry_run=0):
    """
    Import attendance punches (e.g. logs exported from a device at another
    branch) into Employee Checkins.

    `rows` is a JSON array of {user_id, timestamp, log_type?} objects — the
    page posts the rows it got back from parse_checkins_file above.

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
