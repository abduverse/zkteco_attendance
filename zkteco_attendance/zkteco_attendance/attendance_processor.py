"""
Attendance Processor
====================
Core logic for Attendance Summary processing.

Handles:
- Working day calculation (Mon-Sat by default)
- Employee Checkin grouping by date
- Night shift date attribution
- First IN/Last OUT vs Actual Pairs methods
- Present / Half-Day / Absent classification
- Missing checkin scenarios
- Absent hours calculation
- Grace periods: Late Entry / Early Exit minutes beyond the shift's grace
  are deducted from the day's hours (can drop Present -> Half Day -> Absent)
- Overtime calculation split into four explicit categories, honoring the
  shift's Overtime Calculation Method:
    After Standard Hours - Day OT in 06:00-22:00 beyond the daily limit,
                           Night OT in 22:00-06:00 (next day)
    After Shift End Time - Day OT past the shift's End Time, Night OT in the
                           shift's Night OT Start/End window after standard
    OT Punches Only      - only explicit OT punches (is_overtime) count
    Weekend OT  - 00:00 to 24:00 on the weekly rest day (Sunday)
    Holiday OT  - 00:00 to 24:00 on official public holidays (Holiday List)
- OT Threshold (minutes) and Max OT per-day cap
"""

import frappe
from frappe import _
from frappe.utils import getdate, get_datetime, nowdate, flt, cint
from datetime import date, datetime, timedelta, time
from zkteco_attendance.zkteco_attendance.utils import has_column


# ─────────────────────────────────────────────────────────────────────────────
# Overtime time windows (fixed policy)
# ─────────────────────────────────────────────────────────────────────────────

DAY_OT_START    = time(6, 0)    # 06:00
DAY_OT_END      = time(22, 0)   # 22:00
NIGHT_OT_START  = time(22, 0)   # 22:00
NIGHT_OT_END    = time(6, 0)    # 06:00 (next day)


# ─────────────────────────────────────────────────────────────────────────────
# Holidays (public holidays from the Holiday List doctype)
# ─────────────────────────────────────────────────────────────────────────────

def get_holiday_list_for_employee(employee):
    """
    Return the Holiday List name for an employee.
    Uses Employee.holiday_list, falling back to the Company default.
    Returns None when no Holiday List is configured.
    """
    holiday_list = None
    try:
        holiday_list = frappe.db.get_value("Employee", employee, "holiday_list")
    except Exception:
        holiday_list = None
    if not holiday_list:
        try:
            company = frappe.db.get_value("Employee", employee, "company")
        except Exception:
            company = None
        if company:
            try:
                holiday_list = frappe.db.get_value("Company", company, "default_holiday_list")
            except Exception:
                holiday_list = None
    return holiday_list


def get_holidays_in_range(employee, from_date, to_date):
    """
    Return a dict ``{date: description}`` of official public holidays for
    the employee within [from_date, to_date].

    Weekly-off entries in the Holiday child table (weekly_off = 1) are
    excluded — only true public holidays count as holiday overtime.

    The dict supports ``date in holidays`` (key membership) for backward
    compatibility and ``holidays[date]`` / ``holidays.get(date)`` to
    retrieve the holiday description.
    """
    holiday_list = get_holiday_list_for_employee(employee)
    if not holiday_list:
        return {}
    if not frappe.db.table_exists("Holiday"):
        return {}

    filters = {
        "parent": holiday_list,
        "holiday_date": ["between", [str(from_date), str(to_date)]],
    }
    if has_column("Holiday", "weekly_off"):
        filters["weekly_off"] = 0

    rows = frappe.db.get_all(
        "Holiday",
        filters=filters,
        fields=["holiday_date", "description"],
    )
    return {getdate(r.holiday_date): (r.description or "") for r in rows}


# ─────────────────────────────────────────────────────────────────────────────
# Working Days
# ─────────────────────────────────────────────────────────────────────────────

def get_working_days_in_range(from_date, to_date, saturday_mode="Full Day"):
    """
    Return list of date objects that are working days.
    Sundays are always excluded.
    Saturday behaviour is controlled by saturday_mode:
      'Full Day'  — Saturday counts as a full working day (default)
      'Half Day'  — Saturday is included but classified as a half day
      'Off'       — Saturday is excluded entirely
    """
    from_date = getdate(from_date)
    to_date   = getdate(to_date)
    days = []
    current = from_date
    while current <= to_date:
        wd = current.weekday()  # 0=Mon … 5=Sat, 6=Sun
        if wd == 6:  # Sunday — always off
            current += timedelta(days=1)
            continue
        if wd == 5 and saturday_mode == "Off":  # Saturday excluded
            current += timedelta(days=1)
            continue
        days.append(current)
        current += timedelta(days=1)
    return days


def count_working_days(from_date, to_date):
    return len(get_working_days_in_range(from_date, to_date))


# ─────────────────────────────────────────────────────────────────────────────
# Shift lookup for an employee
# ─────────────────────────────────────────────────────────────────────────────

SHIFT_FIELDS = [
    "name", "start_time", "end_time", "is_night_shift",
    "full_day_hours", "half_day_hours", "standard_working_hours",
    "working_hours_method", "lunch_break_hours", "missing_checkin_action",
    "late_entry_grace", "early_exit_grace",
    "saturday_mode", "saturday_end_time", "saturday_half_day_hours",
    "enable_overtime", "overtime_calculation_method",
    "overtime_threshold_minutes",
    "max_overtime_hours_per_day",
    "standard_start_time", "night_ot_start_time", "night_ot_end_time",
]


def get_shift_for_employee(employee, work_date, default_shift_name=None):
    """
    Return a ZK Shift Type doc (as dict) for employee on a given date, or None.
    Checks ZK Shift Assignment first, then falls back to default_shift_name.
    """
    work_date_str = str(work_date)
    columns = ", ".join("st.{0}".format(f) for f in SHIFT_FIELDS)

    result = frappe.db.sql("""
        SELECT {columns}
        FROM `tabZK Shift Assignment` sa
        JOIN `tabZK Shift Assignment Employee` sae ON sae.parent = sa.name
        JOIN `tabZK Shift Type` st ON st.name = sa.shift_type
        WHERE sae.employee = %s
          AND sa.status = 'Active'
          AND sa.from_date <= %s
          AND sa.to_date   >= %s
        ORDER BY sa.from_date DESC
        LIMIT 1
    """.format(columns=columns), (employee, work_date_str, work_date_str), as_dict=True)

    if result:
        return result[0]

    if default_shift_name:
        shift = frappe.db.get_value(
            "ZK Shift Type", default_shift_name,
            SHIFT_FIELDS,
            as_dict=True
        )
        return shift

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Fetch and group checkins
# ─────────────────────────────────────────────────────────────────────────────

def fetch_checkins(employee_list, from_date, to_date):
    """
    Fetch all Employee Checkin records for the given employees and period.
    Returns dict: { employee -> [ {name, time, log_type, is_overtime,
                                   manually_edited, edited_by, edited_at}, ... ] }
    """
    from_dt   = str(from_date) + " 00:00:00"
    to_dt_ext = str(getdate(to_date) + timedelta(days=1)) + " 23:59:59"

    if not employee_list:
        return {}

    has_overtime_col      = has_column("Employee Checkin", "is_overtime")
    has_manual_edited_col = has_column("Employee Checkin", "manually_edited")
    has_ignored_col       = has_column("Employee Checkin", "zk_ignored")
    has_remark_col        = has_column("Employee Checkin", "zk_remark")

    extra_select = ""
    if has_overtime_col:
        extra_select += ", is_overtime"
    if has_manual_edited_col:
        extra_select += ", manually_edited, edited_by, edited_at"
    if has_ignored_col:
        extra_select += ", zk_ignored"
    if has_remark_col:
        extra_select += ", zk_remark"

    placeholders = ", ".join(["%s"] * len(employee_list))
    rows = frappe.db.sql("""
        SELECT name, employee, time, log_type{extra}
        FROM `tabEmployee Checkin`
        WHERE employee IN ({placeholders})
          AND time BETWEEN %s AND %s
        ORDER BY employee, time
    """.format(placeholders=placeholders, extra=extra_select),
        tuple(employee_list) + (from_dt, to_dt_ext),
        as_dict=True
    )

    grouped = {}
    for r in rows:
        grouped.setdefault(r.employee, []).append({
            "name":           r.name,
            "time":           get_datetime(r.time),
            "log_type":       r.log_type,
            "is_overtime":    bool(r.get("is_overtime")) if has_overtime_col else False,
            "manually_edited": bool(r.get("manually_edited")) if has_manual_edited_col else False,
            "edited_by":      r.get("edited_by") or "",
            "edited_at":      str(r.get("edited_at") or ""),
            "ignored":        bool(r.get("zk_ignored")) if has_ignored_col else False,
            "remark":         (r.get("zk_remark") or "") if has_remark_col else "",
        })
    return grouped


def group_checkins_by_date(checkins, shift, default_method="First IN - Last OUT",
                            from_date=None, to_date=None):
    """
    Group a flat list of checkin dicts by attendance date.

    Night shift logic (start_time > end_time, crossing midnight):
      A punch belongs to the shift that started most recently before it.
      This correctly groups cross-midnight punches — e.g. a 17:03 punch
      on Day 1 and a 06:02 punch on Day 2 both land in Day 1's group.
      A 60-minute buffer before the official shift start accommodates
      early arrivals.

    After grouping, night-shift labels are re-resolved so the first
    chronological punch is always IN and the last is always OUT.

    Returns dict: { date -> [checkin, ...] }
    """
    from_date = getdate(from_date) if from_date else None
    to_date   = getdate(to_date)   if to_date   else None

    is_night = bool(shift and shift.get("is_night_shift"))
    start_t  = _coerce_time(shift.get("start_time") or "17:00:00") if is_night else None
    end_t    = _coerce_time(shift.get("end_time") or "06:00:00") if is_night else None
    crosses_midnight = (
        is_night and start_t is not None and end_t is not None
        and start_t > end_t
    )

    daily = {}
    for c in checkins:
        dt = c["time"]
        att_date = dt.date()

        if crosses_midnight:
            # A punch belongs to the shift whose start-time is the most
            # recent shift-start ≤ punch time.  Allow a 60-minute buffer
            # before the official start so early arrivals still land in
            # the correct shift.
            buffer = timedelta(minutes=60)
            today_shift_start = datetime.combine(dt.date(), start_t)
            if dt >= today_shift_start - buffer:
                att_date = dt.date()
            else:
                att_date = (dt - timedelta(days=1)).date()
        elif is_night:
            # Night shift that doesn't cross midnight (unusual)
            if dt.time() <= end_t:
                att_date = (dt - timedelta(days=1)).date()

        # Only include dates within our target range
        if from_date and att_date < from_date:
            continue
        if to_date and att_date > to_date:
            continue

        daily.setdefault(att_date, []).append(c)

    # For night shifts that cross midnight, re-resolve IN/OUT labels
    # within each group so the first chronological punch is IN and the
    # last is OUT — the stored labels may be swapped from the night
    # shift perspective.
    if crosses_midnight:
        for att_date in list(daily.keys()):
            daily[att_date] = _reresolve_night_shift_labels(daily[att_date])

    return daily


def _reresolve_night_shift_labels(checkins):
    """
    For night-shift groups, re-resolve IN/OUT log types by alternating
    chronologically (IN, OUT, IN, …).  This ensures the first punch
    is always IN and the last is always OUT, regardless of how the
    sync engine originally labelled them.
    """
    if len(checkins) <= 1:
        return checkins
    sorted_ci = sorted(checkins, key=lambda c: c["time"])
    resolved = []
    for i, c in enumerate(sorted_ci):
        new_c = dict(c)  # shallow copy — preserves datetime objects
        new_c["log_type"] = "IN" if i % 2 == 0 else "OUT"
        resolved.append(new_c)
    return resolved


def _coerce_time(value):
    """Parse a Time-field value (str, timedelta, or time) into datetime.time."""
    if isinstance(value, str):
        # Frappe Time fields commonly come back as "HH:MM:SS" or "HH:MM:SS.ffffff"
        value = value.split(".")[0]
        return datetime.strptime(value, "%H:%M:%S").time()
    if isinstance(value, timedelta):
        total_seconds = int(value.total_seconds()) % (24 * 3600)
        h, rem = divmod(total_seconds, 3600)
        m, s = divmod(rem, 60)
        return datetime(2000, 1, 1, h, m, s).time()
    return value


def _shift_window_datetimes(shift, work_date):
    """
    Return (start_dt, end_dt) datetimes for the shift on work_date.
    Night shifts (or any shift whose End Time is at/before its Start Time)
    end on the next calendar day.
    Returns (None, None) when no shift is provided.
    """
    if not shift:
        return None, None
    start_t = _coerce_time(shift.get("start_time") or "09:00:00")
    end_t   = _coerce_time(shift.get("end_time") or "17:00:00")
    start_dt = datetime.combine(work_date, start_t)
    end_dt   = datetime.combine(work_date, end_t)
    if shift.get("saturday_mode") == "Half Day" and work_date.weekday() == 5:
        # Saturday half-day end time is configurable; if not set, default to 12:00
        saturday_end_t = _coerce_time(shift.get("saturday_end_time") or "12:00:00")
        end_dt = datetime.combine(work_date, saturday_end_t)
    if shift.get("is_night_shift") or end_dt <= start_dt:
        end_dt = datetime.combine(work_date + timedelta(days=1), end_t)
    return start_dt, end_dt


def _late_early_minutes(day_checkins, shift, work_date):
    """
    Return (late_minutes, early_minutes) for a day's punches, per the shift's
    Late Entry Grace / Early Exit Grace (minutes).

    - late_minutes:  first IN after (Start Time + Late Entry Grace)
    - early_minutes: last OUT before (End Time - Early Exit Grace)

    Both are floored at 0 — arriving early or leaving after the shift end
    never counts against the employee.
    """
    if not day_checkins or not shift or not shift.get("start_time"):
        return 0.0, 0.0
    start_dt, end_dt = _shift_window_datetimes(shift, work_date)
    if start_dt is None:
        return 0.0, 0.0

    late_grace  = flt(shift.get("late_entry_grace") or 0)
    early_grace = flt(shift.get("early_exit_grace") or 0)

    sorted_ci = sorted(day_checkins, key=lambda c: c["time"])
    first_in = None
    last_out = None
    for c in sorted_ci:
        if c["log_type"] == "IN" and first_in is None:
            first_in = c["time"]
    for c in reversed(sorted_ci):
        if c["log_type"] == "OUT":
            last_out = c["time"]
            break

    late_minutes = 0.0
    if first_in is not None:
        late_minutes = max(0.0, (first_in - (start_dt + timedelta(minutes=late_grace)))
                           .total_seconds() / 60.0)

    early_minutes = 0.0
    if last_out is not None:
        early_minutes = max(0.0, ((end_dt - timedelta(minutes=early_grace)) - last_out)
                            .total_seconds() / 60.0)

    return late_minutes, early_minutes


# ─────────────────────────────────────────────────────────────────────────────
# Working hours calculation
# ─────────────────────────────────────────────────────────────────────────────

def calc_hours_first_last(day_checkins, lunch_break_hours=0.0):
    """Method 1: Last checkout minus first checkin (total span).

    When *lunch_break_hours* > 0 the span is reduced by that amount,
    so e.g. 08:00-17:00 with a 1-hour lunch = 8 h instead of 9 h.
    """
    if not day_checkins:
        return 0.0
    times = sorted(c["time"] for c in day_checkins)
    delta = (times[-1] - times[0]).total_seconds() / 3600
    return max(0.0, delta - flt(lunch_break_hours))


def calc_hours_actual_pairs(day_checkins):
    """
    Method 2: Sum of each IN->OUT pair.
    Unmatched IN at end is ignored.
    """
    sorted_ci = sorted(day_checkins, key=lambda c: c["time"])
    total = 0.0
    i = 0
    while i < len(sorted_ci) - 1:
        if sorted_ci[i]["log_type"] == "IN" and sorted_ci[i+1]["log_type"] == "OUT":
            delta = sorted_ci[i+1]["time"] - sorted_ci[i]["time"]
            total += delta.total_seconds() / 3600
            i += 2
        else:
            i += 1
    return total


def calc_working_hours(day_checkins, method="First IN - Last OUT",
                       lunch_break_hours=0.0):
    if method == "Actual Pairs (IN-OUT)":
        return calc_hours_actual_pairs(day_checkins)
    return calc_hours_first_last(day_checkins, lunch_break_hours=lunch_break_hours)


# ─────────────────────────────────────────────────────────────────────────────
# Overtime calculation
# ─────────────────────────────────────────────────────────────────────────────

def _worked_intervals(day_checkins, method):
    """
    Return the worked time intervals as a list of (start, end) datetimes,
    following the same rules as calc_working_hours (First IN - Last OUT
    or Actual Pairs).
    """
    sorted_ci = sorted(day_checkins, key=lambda c: c["time"])
    if method == "Actual Pairs (IN-OUT)":
        intervals = []
        i = 0
        while i < len(sorted_ci) - 1:
            if sorted_ci[i]["log_type"] == "IN" and sorted_ci[i + 1]["log_type"] == "OUT":
                intervals.append((sorted_ci[i]["time"], sorted_ci[i + 1]["time"]))
                i += 2
            else:
                i += 1
        return intervals
    if not sorted_ci:
        return []
    return [(sorted_ci[0]["time"], sorted_ci[-1]["time"])]


def _overlap_hours(start, end, win_start, win_end):
    """Hours of overlap between a worked interval [start, end] and a window."""
    return max(0.0, (min(end, win_end) - max(start, win_start)).total_seconds() / 3600)


def _window_hours(intervals, start_t, end_t):
    """
    Sum of worked hours that fall inside the recurring daily clock window
    [start_t, end_t]. Windows that cross midnight (start_t > end_t) are
    handled by anchoring the window on the interval start date and, when
    the interval spills past midnight, again on the interval end date.
    """
    crosses = start_t > end_t
    total = 0.0
    for start, end in intervals:
        d0 = start.date()
        if crosses:
            ws = datetime.combine(d0, start_t)
            we = datetime.combine(d0 + timedelta(days=1), end_t)
        else:
            ws = datetime.combine(d0, start_t)
            we = datetime.combine(d0, end_t)
        total += _overlap_hours(start, end, ws, we)

        # Interval crosses midnight — also anchor the window on the end date
        if end.date() > d0:
            d1 = end.date()
            if crosses:
                ws = datetime.combine(d1, start_t)
                we = datetime.combine(d1 + timedelta(days=1), end_t)
            else:
                ws = datetime.combine(d1, start_t)
                we = datetime.combine(d1, end_t)
            total += _overlap_hours(start, end, ws, we)
    return total


def _overlap_after(intervals, after_dt, win_start_t, win_end_t):
    """
    Overlap of worked intervals with the recurring clock window
    [win_start_t, win_end_t], restricted to times on/after after_dt.
    Mirrors _window_hours anchoring for windows that cross midnight
    (win_start_t > win_end_t).
    """
    crosses = win_start_t > win_end_t
    total = 0.0
    for start, end in intervals:
        d0 = start.date()
        if crosses:
            ws = datetime.combine(d0, win_start_t)
            we = datetime.combine(d0 + timedelta(days=1), win_end_t)
        else:
            ws = datetime.combine(d0, win_start_t)
            we = datetime.combine(d0, win_end_t)
        ws = max(ws, after_dt)
        total += _overlap_hours(start, end, ws, we)

        # Interval crosses midnight — also anchor the window on the end date
        if end.date() > d0:
            d1 = end.date()
            if crosses:
                ws = datetime.combine(d1, win_start_t)
                we = datetime.combine(d1 + timedelta(days=1), win_end_t)
            else:
                ws = datetime.combine(d1, win_start_t)
                we = datetime.combine(d1, win_end_t)
            ws = max(ws, after_dt)
            total += _overlap_hours(start, end, ws, we)
    return total


def _calc_ot_after_shift_end(day_checkins, shift, method, work_date):
    """
    Overtime for working days per the 'After Shift End Time' method:
      - Day OT   : worked hours after the shift's End Time, excluding any that
                   fall inside the Night OT window (avoids double counting). 
      - Night OT : worked hours inside the shift's Night OT Start/End window
                   that occur after the standard core ends
                   (Start Time + Standard Daily Hours).
    """
    if work_date is None:
        work_date = day_checkins[0]["time"].date() if day_checkins else getdate(nowdate())
    start_dt, end_dt = _shift_window_datetimes(shift, work_date)
    if start_dt is None:
        return 0.0, 0.0

    std_hours      = flt(shift.get("standard_working_hours") or 8)
    core_end       = start_dt + timedelta(hours=std_hours)
    night_start_t  = _coerce_time(shift.get("night_ot_start_time") or "22:00:00")
    night_end_t    = _coerce_time(shift.get("night_ot_end_time") or "06:00:00")

    intervals = _worked_intervals(day_checkins, method)

    after_end       = sum(_overlap_hours(s, e, end_dt, e) for s, e in intervals)
    night_after_end = _overlap_after(intervals, end_dt, night_start_t, night_end_t)
    night_ot        = _overlap_after(intervals, core_end, night_start_t, night_end_t)

    day_ot = max(0.0, after_end - night_after_end)
    return day_ot, night_ot


def _calc_ot_punches_only(day_checkins, method):
    """
    Overtime from explicit OT punches only — Employee Checkins flagged
    `is_overtime` (device punch codes 4/5 when enabled on the device).
    """
    sorted_ci = sorted(day_checkins, key=lambda c: c["time"])
    total = 0.0
    if method == "Actual Pairs (IN-OUT)":
        i = 0
        while i < len(sorted_ci) - 1:
            if sorted_ci[i]["log_type"] == "IN" and sorted_ci[i + 1]["log_type"] == "OUT":
                if sorted_ci[i].get("is_overtime") or sorted_ci[i + 1].get("is_overtime"):
                    total += (sorted_ci[i + 1]["time"] - sorted_ci[i]["time"]).total_seconds() / 3600
                i += 2
            else:
                i += 1
    elif any(c.get("is_overtime") for c in sorted_ci):
        total = (sorted_ci[-1]["time"] - sorted_ci[0]["time"]).total_seconds() / 3600
    return total


def calc_overtime_hours(day_checkins, shift, total_hours, day_type="working",
                        method="First IN - Last OUT", work_date=None):
    """
    Calculate overtime hours for one day, split into four explicit categories:
      {
        overtime_hours:   total overtime hours,
        day_ot_hours:     hours worked beyond the shift on working days,
        night_ot_hours:   hours in the shift's Night OT window on working days,
        weekend_ot_hours: hours worked on the weekly rest day (Sunday),
        holiday_ot_hours: hours worked on official public holidays,
      }
    Returns the zeros dict if overtime is disabled on the shift.

    The working-day split follows the shift's Overtime Calculation Method:
      - 'After Standard Hours' (default): Day OT = day-window hours
        (06:00-22:00) beyond Standard Daily Hours; Night OT = all night-window
        hours (22:00-06:00 next day).
      - 'After Shift End Time': Day OT = hours worked past the shift's End
        Time; Night OT = hours in the shift's Night OT Start/End window after
        the standard core (Start Time + Standard Daily Hours) ends.
      - 'OT Punches Only': only explicit OT punches (is_overtime) count.

    day_type is one of "working" | "weekend" | "holiday":
      - weekend: every hour worked counts as weekend OT (00:00-24:00).
      - holiday: every hour worked counts as holiday OT (00:00-24:00).

    The OT Threshold (minutes) is always applied — total OT at or below it is
    discarded — and the daily Max OT cap is applied proportionally.
    """
    zero = {"overtime_hours": 0.0, "day_ot_hours": 0.0, "night_ot_hours": 0.0,
            "weekend_ot_hours": 0.0, "holiday_ot_hours": 0.0}
    if not shift or not shift.get("enable_overtime"):
        return zero
    if not day_checkins:
        return zero

    std_hours       = flt(shift.get("standard_working_hours") or 8)
    threshold_hours = flt(shift.get("overtime_threshold_minutes") or 0) / 60.0
    max_ot          = flt(shift.get("max_overtime_hours_per_day") or 0)
    ot_method       = (shift.get("overtime_calculation_method") or "After Standard Hours").strip()

    day_ot = night_ot = weekend_ot = holiday_ot = 0.0

    if day_type == "holiday":
        holiday_ot = total_hours
    elif day_type == "weekend":
        weekend_ot = total_hours
    elif ot_method == "After Shift End Time":
        day_ot, night_ot = _calc_ot_after_shift_end(day_checkins, shift, method, work_date)
    elif ot_method == "OT Punches Only":
        day_ot = _calc_ot_punches_only(day_checkins, method)
    else:
        intervals = _worked_intervals(day_checkins, method)
        # Use the shift's configurable Night OT window; fall back to
        # the fixed defaults when the fields are not set.
        night_start_t = (
            _coerce_time(shift.get("night_ot_start_time"))
            if shift.get("night_ot_start_time")
            else NIGHT_OT_START
        )
        night_end_t = (
            _coerce_time(shift.get("night_ot_end_time"))
            if shift.get("night_ot_end_time")
            else NIGHT_OT_END
        )
        day_win   = _window_hours(intervals, DAY_OT_START, DAY_OT_END)
        night_win = _window_hours(intervals, night_start_t, night_end_t)
        # For First IN - Last OUT, the span includes lunch break time
        # which should not count toward overtime.  Deduct lunch break
        # so day_ot reflects actual worked hours beyond standard.
        lunch_break = flt(shift.get("lunch_break_hours") or 0) if method == "First IN - Last OUT" else 0
        day_ot    = max(0.0, day_win - std_hours - lunch_break)
        night_ot  = night_win

    total_ot = day_ot + night_ot + weekend_ot + holiday_ot

    # Apply threshold — OT is counted only after the threshold period.
    # If total OT is at or below the threshold, no OT is paid.
    # Otherwise, the threshold is deducted proportionally from all
    # OT categories so only the excess beyond the threshold counts.
    if total_ot <= threshold_hours:
        return zero

    remaining = total_ot
    scale = remaining / total_ot if total_ot > 0 else 0
    day_ot     *= scale
    night_ot   *= scale
    weekend_ot *= scale
    holiday_ot *= scale
    total_ot    = remaining

    # Apply daily cap (proportionally across categories)
    if max_ot and total_ot > max_ot:
        scale = max_ot / total_ot
        day_ot     *= scale
        night_ot   *= scale
        weekend_ot *= scale
        holiday_ot *= scale
        total_ot    = max_ot

    return {
        "overtime_hours":   round(total_ot, 2),
        "day_ot_hours":     round(day_ot,   2),
        "night_ot_hours":   round(night_ot, 2),
        "weekend_ot_hours": round(weekend_ot, 2),
        "holiday_ot_hours": round(holiday_ot, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Daily status classification
# ─────────────────────────────────────────────────────────────────────────────

def classify_day(day_checkins, shift, doc_method, doc_missing_action,
                 is_saturday=False, day_type="working", work_date=None):
    """
    Returns a dict with attendance status, hours, and overtime breakdown.
    When is_saturday=True, applies Saturday-specific hour thresholds.

    On working days the shift's grace periods are enforced: minutes beyond
    Late Entry Grace / Early Exit Grace are deducted from the day's hours
    (so a late arrival or early departure can drop Present → Half Day →
    Absent), and the day is flagged is_late / is_early_exit.

    Status rules (working days):
      - Invalid    — checkins are missing an IN or OUT pair
      - Present    — worked hours > half_day_hours
      - Half Day   — 0 < worked hours <= half_day_hours
      - Absent     — worked hours = 0 or no checkins

    day_type is one of "working" | "weekend" | "holiday":
      - weekend (Sunday weekly rest day): status is "Weekly Off" when no
        checkins, "Present" when worked (all hours = weekend OT).
      - holiday (public holiday): status is "Holiday" when no checkins,
        "Present" when worked (all hours = holiday OT).
    """
    half_hours = flt(shift.get("half_day_hours") or 4)
    std_hours  = flt(shift.get("standard_working_hours") or 8)
    method     = doc_method or shift.get("working_hours_method") or "First IN - Last OUT"
    lunch_break = flt(shift.get("lunch_break_hours") or 0)

    # Saturday overrides
    saturday_mode = shift.get("saturday_mode") or "Full Day"

    empty_ot = {"overtime_hours": 0.0, "day_ot_hours": 0.0, "night_ot_hours": 0.0,
                "weekend_ot_hours": 0.0, "holiday_ot_hours": 0.0}
    flags    = {"is_late": False, "is_early_exit": False,
                "late_minutes": 0.0, "early_minutes": 0.0}

    # No checkins at all
    if not day_checkins:
        if day_type == "holiday":
            return {"status": "Holiday", "hours": 0.0, "absent_hours": 0.0, **empty_ot, **flags}
        if day_type == "weekend":
            return {"status": "Weekly Off", "hours": 0.0, "absent_hours": 0.0, **empty_ot, **flags}
        if is_saturday and saturday_mode == "Half Day":
            # Saturday Half Day, no checkins → Absent
            return {"status": "Absent", "hours": 0.0, "absent_hours": std_hours, **empty_ot, **flags}
        return {"status": "Absent", "hours": 0.0, "absent_hours": std_hours, **empty_ot, **flags}

    if work_date is None:
        work_date = day_checkins[0]["time"].date()

    has_in  = any(c["log_type"] == "IN"  for c in day_checkins)
    has_out = any(c["log_type"] == "OUT" for c in day_checkins)

    if not (has_in and has_out):
        # Unpaired checkins (only IN or only OUT) are always invalid,
        # regardless of the Missing Check-In/Out Action setting.
        return {"status": "Invalid", "hours": 0.0, "absent_hours": 0.0, **empty_ot, **flags}

    hours = calc_working_hours(day_checkins, method, lunch_break_hours=lunch_break)
    ot    = calc_overtime_hours(day_checkins, shift, hours, day_type=day_type, method=method,
                                work_date=work_date)

    # Weekly rest day / public holiday worked — every hour is OT
    if day_type == "holiday":
        return {"status": "Present", "hours": hours, "absent_hours": 0.0, **ot, **flags}
    if day_type == "weekend":
        return {"status": "Present", "hours": hours, "absent_hours": 0.0, **ot, **flags}

    # Saturday Half Day — no lunch break deduction for the threshold
    # (lunch doesn't count against the half-day presence requirement),
    # but Late Entry Grace / Early Exit Grace DO apply so that a late
    # arrival or early departure reduces effective hours.
    if is_saturday and saturday_mode == "Half Day":
        # Span without lunch (lunch excluded from threshold comparison)
        raw_hours = calc_working_hours(day_checkins, method, lunch_break_hours=0)
        # Apply grace periods — late / early beyond grace reduce hours
        late_min, early_min = _late_early_minutes(day_checkins, shift, work_date)
        sat_class_hours = max(0.0, raw_hours - (late_min + early_min) / 60.0)

        # Overtime is calculated on the actual worked hours (after grace deductions) minus the minimum required for half-day credit.
        overtime_threshold_minutes = flt(shift.get("overtime_threshold_minutes") or 0)
        if (sat_class_hours - overtime_threshold_minutes / 60.0) > half_hours:
            sat_ot_hours = max(0.0, sat_class_hours - half_hours)
        else:
            sat_ot_hours = 0.0
        sat_ot = {"overtime_hours": round(sat_ot_hours, 2),
                  "day_ot_hours": round(sat_ot_hours, 2),
                  "night_ot_hours": 0.0,
                  "weekend_ot_hours": 0.0,
                  "holiday_ot_hours": 0.0}
        sat_flags = {"is_late": late_min > 0, "is_early_exit": early_min > 0,
                     "late_minutes": round(late_min, 1),
                     "early_minutes": round(early_min, 1)}
        # Saturday Half Day: any worked hours → Present, none → Absent
        if sat_class_hours > 0:
            return {"status": "Present", "hours": sat_class_hours, "absent_hours": 0.0, **sat_ot, **sat_flags}
        return {"status": "Absent", "hours": 0.0, "absent_hours": std_hours, **sat_ot, **sat_flags}

    # Working day — enforce the shift's grace periods
    late_min, early_min = _late_early_minutes(day_checkins, shift, work_date)
    eff_hours = max(0.0, hours - (late_min + early_min) / 60.0)

    # For classification, use the raw span (before lunch deduction) so that
    # full_day_hours / half_day_hours represent the time span the employee
    # must cover, not net working time after lunch.  Lunch is a break and
    # shouldn't penalise attendance status.
    raw_hours = calc_working_hours(day_checkins, method, lunch_break_hours=0)
    class_hours = max(0.0, raw_hours - (late_min + early_min) / 60.0)

    flags = {"is_late": late_min > 0, "is_early_exit": early_min > 0,
             "late_minutes": round(late_min, 1), "early_minutes": round(early_min, 1)}

    if class_hours > half_hours:
        return {"status": "Present", "hours": eff_hours, "absent_hours": 0.0, **ot, **flags}
    elif class_hours > 0:
        return {"status": "Half Day", "hours": eff_hours, "absent_hours": std_hours / 2, **ot, **flags}
    else:
        return {"status": "Absent", "hours": eff_hours, "absent_hours": std_hours, **ot, **flags}


# ─────────────────────────────────────────────────────────────────────────────
# Per-employee calculation
# ─────────────────────────────────────────────────────────────────────────────

def process_employee(employee, from_date, to_date,
                     checkin_list, default_shift_name,
                     doc_method, doc_missing_action):
    from_date = getdate(from_date)
    to_date   = getdate(to_date)

    shift = get_shift_for_employee(employee, from_date, default_shift_name)
    saturday_mode = (shift.get("saturday_mode") or "Full Day") if shift else "Full Day"
    holidays = get_holidays_in_range(employee, from_date, to_date)

    daily_checkins = group_checkins_by_date(
        checkin_list, shift,
        default_method=doc_method,
        from_date=from_date, to_date=to_date
    )

    working_days       = 0.0
    absent_days        = 0.0
    half_days          = 0.0
    total_hours        = 0.0
    absent_hours       = 0.0
    overtime_hours     = 0.0
    day_ot_hours       = 0.0
    night_ot_hours     = 0.0
    weekend_ot_hours   = 0.0
    holiday_ot_hours   = 0.0
    overtime_days      = 0
    invalid_days       = 0
    manual_review_days = 0
    remarks_list       = []

    current = from_date
    while current <= to_date:
        work_date    = current
        day_shift    = get_shift_for_employee(employee, work_date, default_shift_name) or shift
        all_day_checkins = daily_checkins.get(work_date, [])
        # Filter out ignored checkins — they should not affect attendance processing
        day_checkins = [c for c in all_day_checkins if not c.get("ignored", False)]
        is_saturday  = (work_date.weekday() == 5)

        if work_date in holidays:
            day_type = "holiday"
        elif work_date.weekday() == 6:
            day_type = "weekend"
        elif is_saturday and saturday_mode == "Off":
            current += timedelta(days=1)
            continue
        else:
            day_type = "working"

        result = classify_day(day_checkins, day_shift or {}, doc_method, doc_missing_action,
                               is_saturday=is_saturday, day_type=day_type, work_date=work_date)

        if result.get("is_late"):
            remarks_list.append("{}: late entry ({} min)".format(
                work_date, int(result.get("late_minutes") or 0)))
        if result.get("is_early_exit"):
            remarks_list.append("{}: early exit ({} min)".format(
                work_date, int(result.get("early_minutes") or 0)))

        total_hours    += result["hours"]
        absent_hours   += result["absent_hours"]
        overtime_hours += result.get("overtime_hours", 0.0)
        day_ot_hours   += result.get("day_ot_hours", 0.0)
        night_ot_hours += result.get("night_ot_hours", 0.0)
        weekend_ot_hours += result.get("weekend_ot_hours", 0.0)
        holiday_ot_hours += result.get("holiday_ot_hours", 0.0)

        if result.get("overtime_hours", 0.0) > 0:
            overtime_days += 1

        if day_type == "weekend":
            # Sunday (weekly rest day) never counts toward working / absent days.
            current += timedelta(days=1)
            continue

        if day_type == "holiday":
            # Public holidays count as a paid day off (Present) — same as
            # Sunday Weekly Off but included in the working-days total so
            # the employee is not penalised for an official holiday.
            working_days += 1.0
            current += timedelta(days=1)
            continue

        if result["status"] == "Present":
            working_days += 1.0
        elif result["status"] == "Half Day":
            working_days += 0.5
            half_days    += 1
            absent_days  += 0.5
        elif result["status"] == "Absent":
            absent_days  += 1.0
        elif result["status"] == "Invalid":
            invalid_days += 1
            remarks_list.append("{}: invalid checkin".format(work_date))
        elif result["status"] == "Manual Review":
            manual_review_days += 1
            remarks_list.append("{}: needs review".format(work_date))

        current += timedelta(days=1)

    return {
        "working_days":        round(working_days, 1),
        "absent_days":         round(absent_days, 1),
        "half_days":           half_days,
        "total_working_hours": round(total_hours, 2),
        "absent_hours":        round(absent_hours, 2),
        "overtime_hours":      round(overtime_hours, 2),
        "day_ot_hours":        round(day_ot_hours, 2),
        "night_ot_hours":      round(night_ot_hours, 2),
        "weekend_ot_hours":    round(weekend_ot_hours, 2),
        "holiday_ot_hours":    round(holiday_ot_hours, 2),
        "overtime_days":       overtime_days,
        "invalid_days":        invalid_days,
        "manual_review_days":  manual_review_days,
        "remarks":             ", ".join(remarks_list[:5]) if remarks_list else "",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Per-employee, per-day breakdown (for the Daily Checkins dashboard)
# ─────────────────────────────────────────────────────────────────────────────

def get_employee_daily_breakdown(employee, from_date, to_date,
                                  checkin_list, default_shift_name,
                                  doc_method, doc_missing_action):
    from_date = getdate(from_date)
    to_date   = getdate(to_date)

    shift = get_shift_for_employee(employee, from_date, default_shift_name)
    saturday_mode = (shift.get("saturday_mode") or "Full Day") if shift else "Full Day"
    holidays = get_holidays_in_range(employee, from_date, to_date)

    daily_checkins = group_checkins_by_date(
        checkin_list, shift,
        default_method=doc_method,
        from_date=from_date, to_date=to_date
    )

    breakdown = []
    current = from_date
    while current <= to_date:
        work_date    = current
        day_shift    = get_shift_for_employee(employee, work_date, default_shift_name) or shift or {}
        all_day_checkins = sorted(daily_checkins.get(work_date, []), key=lambda c: c["time"])
        # Filter out ignored checkins — they should not affect attendance processing
        day_checkins = [c for c in all_day_checkins if not c.get("ignored", False)]
        is_saturday  = (work_date.weekday() == 5)

        if work_date in holidays:
            day_type = "holiday"
        elif work_date.weekday() == 6:
            day_type = "weekend"
        elif is_saturday and saturday_mode == "Off":
            current += timedelta(days=1)
            continue
        else:
            day_type = "working"

        result = classify_day(day_checkins, day_shift, doc_method, doc_missing_action,
                               is_saturday=is_saturday, day_type=day_type, work_date=work_date)

        breakdown.append({
            "date":             str(work_date),
            "weekday":          work_date.strftime("%A"),
            "is_saturday":      is_saturday,
            "is_weekend":       day_type == "weekend",
            "is_holiday":       day_type == "holiday",
            "holiday_name":     holidays.get(work_date, "") if day_type == "holiday" else "",
            "status":           result["status"],
            "hours":            result["hours"],
            "effective_hours":  result["hours"],
            "is_late":          bool(result.get("is_late")),
            "is_early_exit":    bool(result.get("is_early_exit")),
            "late_minutes":     result.get("late_minutes", 0.0),
            "early_minutes":    result.get("early_minutes", 0.0),
            "overtime_hours":   result.get("overtime_hours", 0.0),
            "day_ot_hours":     result.get("day_ot_hours", 0.0),
            "night_ot_hours":   result.get("night_ot_hours", 0.0),
            "weekend_ot_hours": result.get("weekend_ot_hours", 0.0),
            "holiday_ot_hours": result.get("holiday_ot_hours", 0.0),
            "checkins": [
                {
                    "name":           c.get("name") or "",
                    "time":           c["time"].strftime("%H:%M:%S"),
                    "log_type":       c["log_type"],
                    "is_overtime":    bool(c.get("is_overtime")),
                    "manually_edited": bool(c.get("manually_edited")),
                    "edited_by":      c.get("edited_by") or "",
                    "edited_at":      c.get("edited_at") or "",
                    "ignored":        bool(c.get("ignored")),
                    "remark":         c.get("remark") or "",
                }
                for c in all_day_checkins
            ],
        })

        current += timedelta(days=1)

    return breakdown


def get_daily_checkins_data(attendance_summary=None, from_date=None, to_date=None,
                            employee_list=None, company=None, biometric_device=None,
                            project=None):
    """
    Build the full payload for the Daily Checkins dashboard.

    Can be driven by an Attendance Summary (which provides from_date,
    to_date, employees, and processing settings) OR by a direct date range
    + employee list (for standalone use where no summary exists yet).
    """
    if attendance_summary:
        doc = frappe.get_doc("Attendance Summary", attendance_summary)
        # Use summary dates as defaults, but let user-provided dates win
        if not from_date:
            from_date = str(doc.from_date)
        if not to_date:
            to_date = str(doc.to_date)
        employee_list  = [row.employee for row in doc.details]
        company        = doc.company
        doc_method     = doc.working_hours_method
        doc_missing    = doc.missing_checkin_action
        default_shift  = doc.shift_type
    else:
        doc_method    = "First IN - Last OUT"
        doc_missing   = "Mark as Invalid"
        default_shift = None

    if not employee_list:
        # Standalone mode (no Attendance Summary): auto-fetch employees that
        # are mapped to a biometric device (attendance_device_id AND
        # zk_biometric_device set), so the page works without a summary.
        if not attendance_summary:
            # "is set" excludes both empty strings and NULL in Frappe
            filters = {
                "status": "Active",
                "attendance_device_id": ["is", "set"],
                "zk_biometric_device": ["is", "set"],
            }
            if company:
                filters["company"] = company
            if biometric_device:
                filters["zk_biometric_device"] = biometric_device
            if project:
                filters["project"] = project
            employee_list = [
                e["name"]
                for e in frappe.get_all("Employee", filters=filters, fields=["name"])
            ]

        if not employee_list:
            return {
                "attendance_summary": attendance_summary or "",
                "company": company or "",
                "from_date": from_date or "",
                "to_date": to_date or "",
                "employees": [],
            }

    # ── Biometric Device filter ─────────────────────────────────────────────
    # The dropdown must actually narrow the results. Because Employee Checkins
    # carry no device of their own, this filters the employee list — and with
    # it each employee's check-in rows. Match against the same source the card
    # headers display: the Attendance Summary detail row in summary mode, the
    # Employee master otherwise. Explicit list order is preserved.
    if biometric_device and employee_list:
        if attendance_summary:
            employee_list = [
                row.employee for row in doc.details
                if (getattr(row, "zk_biometric_device", None) or "") == biometric_device
            ]
        else:
            device_rows = frappe.get_all(
                "Employee",
                filters={
                    "name": ["in", employee_list],
                    "zk_biometric_device": biometric_device,
                },
                fields=["name"],
            )
            device_emps = {r["name"] for r in device_rows}
            employee_list = [e for e in employee_list if e in device_emps]

        if not employee_list:
            return {
                "attendance_summary": attendance_summary or "",
                "company": company or "",
                "from_date": from_date or "",
                "to_date": to_date or "",
                "employees": [],
            }

    # ── Project filter ────────────────────────────────────────────────────
    # Narrows the results to employees whose Project (Employee master) matches
    # the selected project — the same field the Attendance Summary
    # "Fetch Employees" dialog filters on. Explicit list order is preserved.
    if project and employee_list:
        project_rows = frappe.get_all(
            "Employee",
            filters={
                "name": ["in", employee_list],
                "project": project,
            },
            fields=["name"],
        )
        project_emps = {r["name"] for r in project_rows}
        employee_list = [e for e in employee_list if e in project_emps]

        if not employee_list:
            return {
                "attendance_summary": attendance_summary or "",
                "company": company or "",
                "from_date": from_date or "",
                "to_date": to_date or "",
                "employees": [],
            }

    checkins_by_employee = fetch_checkins(employee_list, from_date, to_date)

    # Resolve employee names for standalone mode
    # `full_name` only exists on Employee in newer HRMS versions; fall back to
    # employee_name otherwise so the Daily Checkins page header always gets
    # the employee's display name under `fullname`.
    has_full_name_col = bool(employee_list) and has_column("Employee", "fullname")
    emp_info = {}
    if employee_list:
        emp_filters = {"name": ["in", employee_list]}
        if biometric_device:
            emp_filters["zk_biometric_device"] = biometric_device
        emp_fields = ["name", "employee_name", "department",
                      "designation", "zk_biometric_device",
                      "attendance_device_id"]
        if has_full_name_col:
            emp_fields.append("fullname")
        rows = frappe.get_all("Employee",
                              filters=emp_filters,
                              fields=emp_fields)
        emp_info = {r.name: r for r in rows}

    employees = []
    for emp_id in employee_list:
        emp_checkins = checkins_by_employee.get(emp_id, [])

        # Get name/dept info from summary row or direct lookup
        if attendance_summary:
            row = next((r for r in doc.details if r.employee == emp_id), None)
            emp_name  = row.employee_name if row else emp_id
            dept      = row.department if row else ""
            desig     = row.designation if row else ""
            info = emp_info.get(emp_id, {})
            zk_device = getattr(row, 'zk_biometric_device', None) or info.get("zk_biometric_device") or ""
            att_dev_id = getattr(row, 'attendance_device_id', None) or info.get("attendance_device_id") or ""
        else:
            info = emp_info.get(emp_id, {})
            emp_name  = info.get("employee_name") or emp_id
            dept      = info.get("department") or ""
            desig     = info.get("designation") or ""
            zk_device = info.get("zk_biometric_device") or ""
            att_dev_id = info.get("attendance_device_id") or ""

        fullname = (info.get("fullname") or "").strip() or emp_name

        days = get_employee_daily_breakdown(
            employee=emp_id,
            from_date=from_date,
            to_date=to_date,
            checkin_list=emp_checkins,
            default_shift_name=default_shift,
            doc_method=doc_method,
            doc_missing_action=doc_missing,
        )

        # Resolve the employee's shift name for the card header
        shift_doc = get_shift_for_employee(emp_id, from_date, default_shift)
        shift_name = shift_doc.get("name") if shift_doc else ""

        employees.append({
            "employee":             emp_id,
            "employee_name":        emp_name,
            "fullname":             fullname,
            "department":           dept,
            "designation":          desig,
            "zk_biometric_device":  zk_device,
            "attendance_device_id": att_dev_id,
            "shift_type":           shift_name,
            "days":                 days,
        })

    return {
        "attendance_summary": attendance_summary or "",
        "company":    company or "",
        "from_date":  from_date,
        "to_date":    to_date,
        "employees":  employees,
    }


def save_manual_checkin_record(employee, checkin_time, log_type,
                            checkin_name=None, is_overtime=0, remark=None):
    """
    Create or update an Employee Checkin manually, without needing an
    Attendance Summary (standalone Daily Checkins mode).

    Records edited_by / edited_at / manually_edited flags.
    Returns {"name": ..., "action": "created" | "updated"}.
    """
    from frappe.utils import now_datetime as _now

    if not employee or not checkin_time or not log_type:
        frappe.throw(_("Employee, Check-in Time, and Log Type are required."))

    editor = frappe.session.user
    now    = _now()
    ot_val = cint(is_overtime)
    remark_col = has_column("Employee Checkin", "zk_remark")

    if checkin_name and frappe.db.exists("Employee Checkin", checkin_name):
        # Update existing
        doc = frappe.get_doc("Employee Checkin", checkin_name)
        doc.time            = checkin_time
        doc.log_type        = log_type
        if has_column("Employee Checkin", "is_overtime"):
            doc.is_overtime   = ot_val
        if remark_col:
            doc.zk_remark     = remark or ""
        doc.manually_edited = 1
        doc.edited_by       = editor
        doc.edited_at       = now
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        return {"name": doc.name, "action": "updated"}
    else:
        # Create new
        emp_doc = frappe.db.get_value("Employee", employee, ["employee_name"], as_dict=True)
        data = {
            "doctype":        "Employee Checkin",
            "employee":       employee,
            "employee_name":  emp_doc.employee_name if emp_doc else employee,
            "time":           checkin_time,
            "log_type":       log_type,
            "manually_edited": 1,
            "edited_by":      editor,
            "edited_at":      now,
        }
        if has_column("Employee Checkin", "is_overtime"):
            data["is_overtime"] = ot_val
        if remark_col:
            data["zk_remark"] = remark or ""
        doc = frappe.get_doc(data)
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        return {"name": doc.name, "action": "created"}


def get_employee_shift_info(employee, work_date=None):
    """
    Return the employee's shift details for a given date.
    Used by the checkin dialog to display shift context.
    """
    if not work_date:
        from frappe.utils import nowdate
        work_date = nowdate()
    shift = get_shift_for_employee(employee, work_date)
    if not shift:
        return None
    return {
        "name":               shift.get("name") or "",
        "start_time":         str(shift.get("start_time") or ""),
        "end_time":           str(shift.get("end_time") or ""),
        "is_night_shift":     bool(shift.get("is_night_shift")),
        "full_day_hours":     flt(shift.get("full_day_hours") or 8),
        "half_day_hours":     flt(shift.get("half_day_hours") or 4),
        "standard_working_hours": flt(shift.get("standard_working_hours") or 8),
        "lunch_break_hours":  flt(shift.get("lunch_break_hours") or 0),
        "saturday_mode":      shift.get("saturday_mode") or "Full Day",
        "saturday_half_day_hours": flt(shift.get("saturday_half_day_hours") or 4),
        "enable_overtime":    bool(shift.get("enable_overtime")),
        "overtime_calculation_method": shift.get("overtime_calculation_method") or "After Standard Hours",
    }
