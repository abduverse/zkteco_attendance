# ZKTeco Attendance for ERPNext / Frappe

Connects ZKTeco biometric devices to ERPNext: pulls attendance punches,
creates **Employee Checkin** records, and processes them into attendance
summaries with overtime support. Compatible with Frappe/ERPNext v14, v15,
and v16.

---

## 1. Installation

### Fresh install

Make sure the `pyzk` Python library is available (used to talk to the
device over the network):

```bash
pip install pyzk --break-system-packages
```

```bash
cd frappe-bench
bench get-app https://github.com/abducodespro/zkteco_attendance
bench --site frappe.com install-app zkteco_attendance
bench --site frappe.com migrate
bench restart
```

### To Uninstall and Clean up if needed
```bash
bench --site frappe.com uninstall-app zkteco_attendance 2>/dev/null; true
rm -rf ~/frappe-bench/apps/zkteco_attendance
sed -i '/zkteco_attendance/d' ~/frappe-bench/sites/apps.txt
```

Installation automatically:
- Creates **Biometric Device Manager** and **Checkin Editor** roles
- Adds **Biometric Device** (Link) and **Biometric Attendance ID** (Data)
  fields to Employee
- Adds **Biometric Device**, **ZK Device Record ID**, **Overtime Punch**,
  manual-edit tracking (**Manually Edited** / **Edited By** /
  **Edited At**), and **Ignored** fields to Employee Checkin
- Adds the **Biometric Attendance** workspace with a **Check-ins
  (Last 7 Days)** chart

> The two Employee fields above are created automatically on install —
> only create them yourself if you need them before installing the app.

---

## 2. Initial Setup

### 2.1 Checkin Editor Role

Assign the **Checkin Editor** role to users who should be able to add,
edit, or ignore checkins from the Employee Daily Checkins page. Users
without this role can view checkins but cannot modify them.

### 2.2 Map employees to the device
On each **Employee** record, fill in **Biometric Device** and
**Biometric Attendance ID** — the attendance ID must match the User
ID/Badge Number enrolled on the ZKTeco device for that person.

### 2.3 Add a Biometric Device
Go to **Biometric Device** (new) and fill in:

| Field | Notes |
|---|---|
| Device Name | Any label, must be unique |
| Device IP / Port | Device's network address (default port 4370) |
| Company | Company this device belongs to |
| Device Time Zone | Informational only — for troubleshooting. Device timestamps are stored as-is (see Clock Offset below) |
| Connection Password | Only if the device has a comm key/password set |
| Status | Set to **Active** once configured |
| Auto Sync Enabled / Sync Frequency | 5 Min / 15 Min / 30 Min / Hourly / Daily — for the background scheduler |
| Fetch Mode | **New Records Only** (recommended) or **All Records** |
| Clear Device Logs After Sync | Frees device memory after each pull — use with care |
| Device Clock Offset (minutes) | Leave at **0** unless this specific device's clock is known to be wrong. Device timestamps are taken as-is (assumed to already be correct local time) |
| Treat OT Punch Codes (4/5) as Overtime | If enabled, device punch codes 4 (OT In) / 5 (OT Out) are recorded as overtime punches |

Click **Test Connection** to verify the device responds and to see its
serial number, firmware, enrolled users, and stored log count.

### 2.4 Set up Shift Types
Create one or more **ZK Shift Type** records:

- **Timing**: Start Time, End Time, Is Night Shift (for shifts crossing
  midnight — early-morning checkouts are attributed to the previous day)
- **Hours**: Full Day Minimum Hours, Half Day Minimum Hours, Standard
  Daily Hours (used for absent-hours and day-overtime calculations)
- **Calculation**: Working Hours Method (First IN–Last OUT or Actual
  Pairs), Missing Check-In/Out Action (Mark Invalid / Present / Manual
  Review), and Grace Periods (Late Entry / Early Exit in minutes)
- **Saturday Configuration**: Saturday Working Mode (**Full Day** /
  **Half Day** / **Off**) and Saturday Half Day Min Hours — Sundays are
  always the weekly rest day
- **Overtime Management** (optional, see section 5)

### 2.5 Assign shifts
Use **ZK Shift Assignment** to assign a Shift Type to a group of employees
for a date range (From Date / To Date, Status = Active).

---

## 3. Pulling Attendance (Pull Checkins)

Open a **Biometric Device** record and click **Pull Checkins** (under
*Actions*):

1. Confirm the dialog — this connects to the device right away.
2. A progress dialog shows live stages: connecting → fetching records →
   processing → creating Employee Checkins → done.
3. When finished, you'll see a summary: **Total Pulled**, **New**,
   **Duplicates**, **Failed**, **Overtime Punches**, and overall **Status**
   (Success / Partial / Failed).

Each pull creates **Employee Checkin** records with `log_type` of `IN` or
`OUT`. Devices that send the same punch code for every tap are handled
automatically — punches for each employee/day are alternated IN, OUT, IN,
OUT in chronological order. Explicit overtime punches (codes 4/5, if
enabled) are flagged with the **Overtime Punch** checkbox instead.

**Test Connection** can be run at any time to re-check connectivity without
pulling data.

**View Sync Logs** opens the **Attendance Sync Log** list filtered to that
device — useful history of every pull (manual or scheduled), including
counts and any errors.

### Automatic syncing
If **Auto Sync Enabled** is checked, the background scheduler pulls
checkins automatically at the configured **Sync Frequency**, independent of
the manual button above. Logs from scheduled pulls also appear in
**Attendance Sync Log** (Triggered By = Scheduler).

---

## 4. Processing Attendance (Attendance Summary)

Use **Attendance Summary** to turn raw checkins into a per-employee
attendance report for a date range:

1. Create a new **Attendance Summary**, set **Company**, **From Date**,
   **To Date**, and optionally a default **Shift Type** (used as a fallback
   if an employee has no Shift Assignment).
2. The **Processing Settings** section lets you override **Working Hours
   Method** and **Missing Check-In/Out Action** for this summary; leave
   blank to use each employee's shift settings.
3. Click **Fetch Employees** — choose to fetch all active employees, or
   filter by Department / Designation / Project.
4. Click **Process Attendance**. This runs in the background; the form
   polls automatically and reloads when done.
5. Each row in **Details** shows: Working Days, Absent Days, Half Days,
   Total Hours, Absent Hours, **Overtime Hours** (split into **Day OT**,
   **Night OT**, **Weekend OT**, **Holiday OT**), OT Days, Invalid Days,
   and Manual Review flags.
6. The summary totals show **Total Employees**, **Working Days in
   Period**, **Total Overtime Hours**, and the Day / Night / Weekend /
   Holiday OT subtotals.

**Working days**: Monday–Friday are working days, Sunday is always the
weekly rest day, and Saturday follows the shift's **Saturday Working Mode**
(Full Day / Half Day / Off). Public holidays come from the employee's
**Holiday List** (or the company default).

**Working Hours Method**:
- *First IN – Last OUT*: total span between the first and last punch of the
  day.
- *Actual Pairs (IN-OUT)*: sums each matched IN→OUT pair (more accurate if
  employees punch for breaks too).

**Missing Check-In/Out Action** (deprecated): days with only one punch
(unpaired checkins — an IN without an OUT, or an OUT without an IN) are
always marked **Invalid**, regardless of this setting.

**Status rules (working days):**
- **Present** — worked hours are greater than **Half Day Minimum Hours**.
- **Half Day** — worked hours are less than or equal to Half Day Minimum
  Hours but greater than zero.
- **Absent** — worked hours are zero or there are no checkins at all.
- **Invalid** — unpaired checkins (a punch is missing its IN or OUT pair).

Full Day Minimum Hours no longer affects the status — any hours above the
half-day threshold already count as a full Present day.

**Grace Periods** (per shift, on working days):
- A first IN after **Start Time + Late Entry Grace** is a *late entry*; the
  minutes beyond the grace are deducted from the day's working hours.
- A last OUT before **End Time − Early Exit Grace** is an *early exit*; the
  minutes beyond the grace are deducted from the day's working hours.
- The deduction can drop the status **Present → Half Day → Absent**, and the
  day is flagged **LATE / EARLY** on the Daily Checkins page and noted in the
  summary's Remarks. Arriving early or leaving after the shift end never
  counts against the employee.

---

## 5. Overtime Management

Enable overtime per shift on **ZK Shift Type** → **Overtime Management**:

- **Enable Overtime Calculation** — turns OT on for this shift.
- **OT Threshold (minutes)** — minimum extra time before OT is counted
  (avoids paying OT for a few minutes of rounding).
- **Max OT Hours per Day** — optional daily cap (0 = no cap); when
  exceeded, the cap is applied proportionally across OT categories.

Overtime is calculated from the actual worked hours (per the shift's
Working Hours Method) and split into four explicit categories:

| Category | Rule |
|---|---|
| **Day OT** | Working days — per the shift's **Overtime Calculation Method** (below) |
| **Night OT** | Working days — hours inside the shift's **Night OT Start/End** window beyond the standard core |
| **Weekend OT** | Sunday (weekly rest day), 00:00–24:00 — all hours worked |
| **Holiday OT** | Official public holidays (Holiday List), 00:00–24:00 — all hours worked |

The **Overtime Calculation Method** on the shift decides how working-day OT
is measured:

- **After Standard Hours** (default) — Day OT = day-window hours
  (06:00–22:00) **beyond the Standard Daily Hours**; Night OT = night-window
  hours (22:00–06:00 next day).
- **After Shift End Time** — Day OT = hours worked **past the shift's End
  Time**; Night OT = hours inside the shift's **Night OT Start Time / Night
  OT End Time** window that fall **after the standard core** (Start Time +
  Standard Daily Hours). Example: a guard on a 17:00→06:00 night shift with
  8 standard hours gets the 01:00–06:00 tail counted as Night OT.
- **OT Punches Only** — only explicit device OT punches (codes 4/5, if
  enabled on the device) count as overtime.

In every case the **OT Threshold (minutes)** is enforced — total OT at or
below it is discarded (no OT for a few minutes of rounding) — and the
**Max OT Hours per Day** cap is applied proportionally across categories.

So a guard who works 17:00→06:00 on a night shift gets their
Night OT window hours counted as Night OT, and anyone who works a Sunday or
a public holiday is paid all of it as OT.

Resulting overtime hours/days (Day / Night / Weekend / Holiday splits
included) appear automatically in **Attendance Summary Detail** and
**Attendance Summary** after processing.

---

## 6. Employee Daily Checkins Page

The **Employee Daily Checkins** page (searchable in the awesome bar, or
from an **Attendance Summary** via **View Daily Checkins**) shows a
per-employee, per-day breakdown of raw punches.

- **Standalone mode**: set **From Date** / **To Date** and click **Load**.
  Employees mapped to a biometric device (Biometric Device + Biometric
  Attendance ID set, Status Active) are fetched automatically.
- Optionally link an **Attendance Summary** to pre-fill the dates and use
  exactly the employees in that summary's Details.
- Each employee appears as a collapsible card with a running OT total.
  Expanding it shows a table with one row per day: date, weekday, status
  (Present / Half Day / Absent / Invalid / Manual Review / Weekly Off /
  Holiday), total hours, an **OT breakdown** (chips: **D**ay / **N**ight /
  **W**eekend / **H**oliday), and a chip for every check-in (time + IN/OUT,
  with overtime punches highlighted).
- **Fix punches on the spot**: use the **+** button to add a check-in, or
  the ✎ icon to edit an existing one. Manually added/edited records
  are marked with a ✎ badge (showing who edited and when) so they're easy
  to spot before finalizing payroll.
- **Ignore checkins**: use the **○** button to ignore a checkin — ignored
  checkins are excluded from attendance processing (both the Daily Checkins
  page and Attendance Summary). Ignored checkins appear visually
  distinguished (dashed border, reduced opacity, strikethrough) and can be
  unignored with the **⊘** button. This requires the **Checkin Editor**
  role.
- **Shift info in dialog**: when adding or editing a checkin, the dialog
  displays a modern shift info card showing the assigned shift's timing,
  hours, Saturday mode, overtime settings, and lunch break at a glance.
- **Pull checkins**: the **Pull checkins** button syncs biometric devices
  without leaving the page. With a **Biometric Device** selected in the
  filter bar it connects to that device right away and shows a live
  progress dialog (same as the device form's **Pull Checkins**);
  when no device is selected it queues a background sync for **all active
  devices** — results land in **Attendance Sync Log**. After a foreground
  pull, a loaded report refreshes automatically so new punches appear
  immediately. Requires one of the sync roles (System Manager / HR Manager /
  Biometric Device Manager).

This is useful for spot-checking raw punches behind a Present/Absent/Half
Day result before finalizing payroll.

---

## 7. Dashboard & Workspace Chart

The **Biometric Attendance** workspace includes a built-in **Check-ins
(Last 7 Days)** bar chart at the top, showing daily Employee Checkin counts
for the past week — refreshable from the chart's own menu like any other
workspace chart.

The **ZKTeco Dashboard** page (search for it in the awesome bar, or open it
from the workspace's Device card) shows:

- Total / Online / Offline device counts
- Today's check-in count and failed syncs today
- Last sync time
- Charts: check-ins (last 7 days), sync results (last 7 days), device
  status, and today's IN/OUT/Overtime breakdown

Use the menu's **Sync All Devices** to queue a background sync for every
active device, or **Refresh** to reload the data. The page auto-refreshes
every 60 seconds.

---

## 8. Permissions

Four roles can access this app's doctypes:

| Role | Access |
|---|---|
| **System Manager** | Full access to all features |
| **HR Manager** | Full access to all features |
| **Biometric Device Manager** | Manage devices, syncs, and attendance summaries |
| **Checkin Editor** | Add, edit, and ignore checkins from the Daily Checkins page |

**Biometric Device Manager** and **Checkin Editor** are created
automatically on install. Assign them to users who need specific access
without full HR or System Manager privileges.

---

## 9. Troubleshooting

- **Connection failed** — check Device IP/Port, that the device is on the
  same network/VPN as the ERPNext server, and that no firewall blocks the
  port (default 4370).
- **No employee found for biometric ID** — make sure the **Biometric
  Attendance ID** on the Employee exactly matches the device's enrolled
  User ID, and that the employee's **Status** is Active.
- **Check-in times look wrong** — leave **Device Clock Offset (minutes)** at
  0 first; device timestamps are stored as-is. Only set an offset if you've
  confirmed the device's own clock is incorrect.
- **Pull takes a long time / times out** — for devices with thousands of
  stored logs, ensure your web server/reverse proxy timeout is generous
  (5–10 minutes), or use **Clear Device Logs After Sync** to keep device
  storage small.
- **Overtime numbers look unexpected** — confirm the shift's **Overtime
  Calculation Method** and its Standard Daily Hours / Night OT window in
  section 5; OT is based on clock windows or the shift end time, not on
  whether a punch was marked as overtime.
- **Ignore button not visible** — you need the **Checkin Editor** role to
  see and use the ignore/edit/add buttons on checkin chips.
- **Ignored checkin still affects attendance** — ignored checkins are
  excluded from both the Daily Checkins page display and Attendance Summary
  processing. If you see unexpected results, verify the checkin's ignored
  status (dashed border, strikethrough chip) and unignore if needed.
- Check **Attendance Sync Log** for a history of every sync attempt and any
  error details.

---

#### License

MIT
