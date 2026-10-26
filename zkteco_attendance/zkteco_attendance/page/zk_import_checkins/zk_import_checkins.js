frappe.pages["zk-import-checkins"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Import Checkins"),
		single_column: true,
	});

	const me = this;
	// Primary action only — validation runs by itself as soon as a file is
	// picked (see runPreview), so there is no separate dry-run button.
	page.set_primary_action(__("Import"), () => me.doImport(), "fa fa-upload");

	me.page = page;
	me.frm = wrapper; // keeps naming consistent with field helpers below
	me.rows = [];

	me.renderIntro();
	me.bindFileInput();
};

frappe.pages["zk-import-checkins"].on_page_show = function (wrapper) {
	// nothing extra for now
};

Object.assign(frappe.pages["zk-import-checkins"], {
	renderIntro() {
		const me = this;
		this.page.main.html(`
			<div class="zk-import-wrap">
				<div class="zk-import-intro">
					<i class="fa fa-cloud-upload"></i>
					<div>
						<b>${__("Import punches from a file")}</b><br>
						<span class="text-muted">${__("Use this when punches come from a branch device the server cannot reach directly — e.g. logs exported at the branch, or a collector file. Rows are deduplicated against existing checkins for the selected device.")}</span>
					</div>
				</div>
				<div class="zk-import-device-row">
					<div id="zk-import-device"></div>
				</div>
				<div class="zk-import-form-row">
					<div class="zk-import-file-col">
						<div class="zk-import-file-drop">
							<i class="fa fa-file-text-o"></i>
							<div>${__("Click to choose an Excel or CSV file, or drop it here")}</div>
						</div>
						<input type="file" accept=".xlsx,.xlsm,.csv,.tsv,.txt,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" class="zk-import-file-input" />
						<div class="zk-import-file-hint text-muted">
							${__("Columns (header row required, any order):")} <code>user_id</code>, <code>time</code>, <code>log_type</code> ${__("(optional — leave blank to alternate IN/OUT by time)")}
						</div>
						<div class="zk-import-template-row">
							<button class="btn btn-xs btn-default zk-import-template-btn">
								<i class="fa fa-download"></i> ${__("Download Template")}
							</button>
						</div>
					</div>
					<div class="zk-import-summary-col">
						<div class="zk-import-card">
							<div class="zk-import-summary text-muted small">${__("Select a device first.")}</div>
						</div>
					</div>
				</div>
				<div class="zk-import-preview"></div>
				<div class="zk-import-problems"></div>
				<div class="zk-import-result"></div>
			</div>
		`);

		// wiring: click the dropzone→hidden input, and drag/drop support
		// NOTE: the hidden input is OUTSIDE .zk-import-file-drop on purpose —
		// if it were inside, the programmatic $input.click() would bubble up
		// to the dropzone handler and re-enter mid-gesture; browsers ignore
		// that second click, so the file dialog would never open.
		const $drop = this.page.main.find(".zk-import-file-drop");
		const $input = this.page.main.find(".zk-import-file-input");
		$drop.on("click", (e) => {
			e.preventDefault();
			$input.get(0).click();
		});
		$drop.on("dragenter", function (e) {
			e.preventDefault();
			$(this).addClass("zk-dragover");
		});
		$drop.on("dragover", function (e) {
			e.preventDefault();
			$(this).addClass("zk-dragover");
		});
		$drop.on("dragleave", function (e) {
			e.preventDefault();
			$(this).removeClass("zk-dragover");
		});
		$drop.on("drop", function (e) {
			e.preventDefault();
			$(this).removeClass("zk-dragover");
			const f = e.originalEvent.dataTransfer.files && e.originalEvent.dataTransfer.files[0];
			if (f) me.parseFile(f);
		});
		$input.on("change", function () {
			const f = this.files && this.files[0];
			if (f) me.parseFile(f);
		});

		// ── Device selector (Link via make_control) ──────────────────────
		// Built with frappe.ui.form.make_control (same pattern as the device
		// filter on the Employee Daily Checkins page) instead of page.add_field:
		// add_field renders the control input-only in the page head, where the
		// link-search dropdown does not attach reliably, so devices could not
		// be selected. Placed in page.main so it renders with the page cards.
		me.setupDeviceField();

		// Download Template — generates the CSV in the browser (no server call)
		this.page.main.find(".zk-import-template-btn").on("click", (e) => {
			e.preventDefault();
			const header = "user_id,time,log_type";
			const samples = [
				"1001,2026-02-01 08:02:11,IN",
				"1001,2026-02-01 17:31:45,OUT",
				"1002,2026-02-01 08:15:03,IN",
			];
			const csv = [header].concat(samples).join("\n");
			const blob = new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8;" }); // BOM so Excel opens it cleanly
			const link = document.createElement("a");
			link.href = URL.createObjectURL(blob);
			link.download = "checkin_import_template.csv";
			document.body.appendChild(link);
			link.click();
			document.body.removeChild(link);
			URL.revokeObjectURL(link.href);
		});
	},

	bindFileInput() {
		// no-op placeholder; bind happens inside renderIntro
	},

	setupDeviceField() {
		const me = this;
		this.device_field = frappe.ui.form.make_control({
			df: {
				fieldtype: "Link",
				fieldname: "device",
				options: "Biometric Device",
				label: __("Device"),
				change() {
					me.updateHints();
					// duplicates and unmapped employees are device-specific, so
					// re-check the loaded rows against the newly picked device
					me.runPreview();
				},
			},
			parent: this.page.main.find("#zk-import-device"),
			render_input: true,
		});
		this.device_field.refresh();
	},

	updateHints() {
		// reflect the selected device in the intro
		if (!this.device_field) return;
		const d = this.device_field.get_value();
		this.page.main.find(".zk-import-summary").html(
			d
				? `<span class="text-muted small">${__("Importing into:")} <b>${frappe.utils.escape_html(d)}</b></span>`
				: `<span class="text-muted small">${__("Select a device first.")}</span>`
		);
	},

	// ── File reading ────────────────────────────────────────────────────
	// The file is parsed on the server (parse_checkins_file) rather than
	// here: an .xlsx workbook is a zip of XML, not text, and one parser for
	// both formats keeps the header rules in a single place. The raw bytes
	// are posted base64-encoded.
	parseFile(file) {
		const me = this;
		const name = file.name || "";
		const fail = (title, message) => {
			me.clearPreview();
			me.updateHints();
			frappe.msgprint({ title: __(title), indicator: "red", message });
		};

		if (/\.xls$/i.test(name)) {
			fail("Unsupported File",
				__("Old .xls workbooks cannot be read. Open the file in Excel and save it as .xlsx or CSV, then upload again."));
			return;
		}
		if (file.size > 15 * 1024 * 1024) {
			fail("File Too Large",
				__("This file is larger than 15 MB. Split it into smaller files and import them one at a time."));
			return;
		}

		me.clearPreview();
		me.page.main.find(".zk-import-summary").html(
			`<span class="text-muted small">${__("Reading {0}…", [frappe.utils.escape_html(name)])}</span>`
		);

		const seq = (me._parse_seq = (me._parse_seq || 0) + 1);
		const reader = new FileReader();
		reader.onload = function (e) {
			const dataUrl = String(e.target.result || "");
			const content = dataUrl.slice(dataUrl.indexOf(",") + 1);
			frappe.call({
				method: "zkteco_attendance.zkteco_attendance.page.zk_import_checkins.zk_import_checkins.parse_checkins_file",
				args: { filename: name, content },
				callback(r) {
					if (seq !== me._parse_seq) return; // a newer file was picked
					const out = (r && r.message) || {};
					if (!out.success) {
						const found = (out.header || []).filter(h => String(h || "").trim());
						const detail = found.length
							? `<br><br>${__("First row of the file")}: <code>${frappe.utils.escape_html(found.join(", "))}</code>`
							: "";
						fail("Could Not Read File", `${frappe.utils.escape_html(out.error || __("Unknown error."))}${detail}`);
						return;
					}

					const rows = out.rows || [];
					me.rows = rows;
					if (!rows.length) {
						fail("No Rows", __("No data rows found in the file."));
						return;
					}
					me.showSummary(rows.length, name);
					me.showPreview(name, rows);
					me.runPreview();
				},
				error() {
					if (seq !== me._parse_seq) return;
					fail("Could Not Read File", __("Could not read the uploaded file."));
				},
			});
		};
		reader.onerror = function () {
			if (seq !== me._parse_seq) return;
			fail("Could Not Read File", __("Could not read the uploaded file."));
		};
		reader.readAsDataURL(file);
	},

	showSummary(n, fileName) {
		const $sum = this.page.main.find(".zk-import-summary");
		$sum.html(`
			<div class="zk-import-stats">
				${this.stat_row("File", frappe.utils.escape_html(fileName || "—"), "")}
				${this.stat_row("Rows parsed", n, "ok")}
				<div class="zk-import-hint text-muted small">${__("Rows are checked automatically — problems appear with the preview below.")}</div>
			</div>
		`);
		// tint the dropzone to reflect that a file has been loaded
		this.page.main.find(".zk-import-file-drop").addClass("drag-ok");
	},

	clearPreview() {
		this.rows = [];
		this._preview_file = "";
		this._local_issues = [];
		this._validation = null;
		this._validation_error = "";
		this.preview_pending = false;
		// bump the sequence so a dry run still in flight for the old file is
		// ignored when it comes back
		this._preview_seq = (this._preview_seq || 0) + 1;
		this.page.main.find(".zk-import-preview").empty();
		this.page.main.find(".zk-import-file-drop").removeClass("drag-ok");
	},

	showPreview(fileName, rows) {
		this._preview_file = fileName || "";
		this._validation = null;
		this._validation_error = "";
		this._local_issues = this.localIssues(rows);
		this.renderPreview();
	},

	// ── Row checks (client side) ─────────────────────────────────────────
	// Everything detectable without the server. Returns one array of
	// {text, severity} per row, in file order, so a finding can be tied back
	// to the row number the server also uses (data row 1 = first CSV line).
	localIssues(rows) {
		return rows.map(r => {
			const issues = [];
			if (!String(r.user_id || "").trim()) {
				issues.push({ text: __("Missing user_id"), severity: "danger" });
			}
			const dt = this.splitDateTime(r.timestamp);
			if (!dt.date) issues.push({ text: __("Missing date"), severity: "danger" });
			else if (!dt.time) issues.push({ text: __("Missing time"), severity: "danger" });
			if (r.log_type) {
				const lt = String(r.log_type).trim().toUpperCase();
				if (lt !== "IN" && lt !== "OUT") {
					issues.push({ text: __("{0} is not IN/OUT", [r.log_type]), severity: "warn" });
				}
			}
			return issues;
		});
	},

	// Merge local + server findings into rowIndex → worst issue, so every
	// preview row can carry a single status chip.
	rowIssueMap() {
		const map = new Map();
		const put = (idx, text, severity) => {
			const cur = map.get(idx);
			if (!cur) {
				map.set(idx, { text, severity, count: 1 });
				return;
			}
			cur.count += 1;
			// the chip shows the worst finding for that row
			if (cur.severity === "warn" && severity === "danger") {
				cur.text = text;
				cur.severity = severity;
			}
		};

		(this._local_issues || []).forEach((issues, i) =>
			issues.forEach(it => put(i, it.text, it.severity)));

		const out = this._validation;
		if (!out) return map;

		// Server row problems come back as already-translated text
		// ("Row 3: unreadable time …"), so the leading row number is the only
		// machine-readable part: take the first integer and range-check it
		// before tagging a row. The full message is listed in the findings
		// panel regardless, so nothing is lost if this does not match.
		(out.problems || []).forEach(p => {
			const m = /(\d+)/.exec(String(p));
			const idx = m ? parseInt(m[1], 10) - 1 : -1;
			if (idx >= 0 && idx < this.rows.length) put(idx, String(p), "danger");
		});

		// Biometric IDs the device has no Employee mapped for — the server
		// reports the IDs, so rows can be flagged by their user_id.
		const unmatched = new Set((out.unmatched_ids || []).map(String));
		if (unmatched.size) {
			this.rows.forEach((r, i) => {
				if (unmatched.has(String(r.user_id || "").trim())) {
					put(i, __("No Employee mapped to this biometric ID"), "warn");
				}
			});
		}
		return map;
	},

	// Group the same finding across rows so a file with hundreds of bad rows
	// stays readable: "Missing user_id — rows 3, 7, 12 (+9 more)".
	buildFindings() {
		const groups = new Map();
		const add = (severity, text, rowNo, meta) => {
			const key = `${severity}|${text}`;
			const g = groups.get(key) || { severity, text, rows: [], meta: "" };
			if (rowNo) g.rows.push(rowNo);
			if (meta) g.meta = meta;
			groups.set(key, g);
		};

		(this._local_issues || []).forEach((issues, i) =>
			issues.forEach(it => add(it.severity, it.text, i + 1)));

		const out = this._validation;
		if (out) {
			(out.problems || []).forEach(p => add("danger", String(p), 0));
			const unmatched = (out.unmatched_ids || []).map(String);
			if (unmatched.length) {
				const n = this.rows.filter(r => unmatched.includes(String(r.user_id || "").trim())).length;
				add("warn", __("No Employee mapped to this biometric ID"), 0,
					`${unmatched.join(", ")} — ${__("{0} row(s)", [n])}`);
			}
		}

		// errors first, then warnings
		return Array.from(groups.values()).sort((a, b) =>
			a.severity === b.severity ? 0 : (a.severity === "danger" ? -1 : 1));
	},

	renderPreview() {
		const me = this;
		const rows = me.rows;
		if (!rows.length) return;

		const MAX_ROWS = 25;
		const emptyCell = '<span class="text-muted">—</span>';
		const pill = (text, cls) => `<span class="zk-preview-pill ${cls || ""}">${text}</span>`;
		const issueMap = me.rowIssueMap();
		const local = me._local_issues || [];
		const countOf = (text) => local.reduce((n, list) => n + (list.some(i => i.text === text) ? 1 : 0), 0);
		// date / time split is display-only — me.rows keeps the raw timestamp
		// so the server still receives exactly what the file contained
		const shown = rows.slice(0, MAX_ROWS).map(r => Object.assign({}, me.splitDateTime(r.timestamp), r));

		// ── Validation status strip ──────────────────────────────────────
		let status;
		if (me.preview_pending) {
			status = { cls: "loading", icon: "fa-circle-o-notch fa-spin", text: __("Checking rows against the device…") };
		} else if (me._validation_error) {
			status = { cls: "danger", icon: "fa-times-circle", text: me._validation_error };
		} else if (!me._validation) {
			status = { cls: "idle", icon: "fa-info-circle", text: __("Select a device above to check these rows for duplicates and unmapped employees.") };
		} else {
			const flagged = rows.reduce((n, _r, i) => n + (issueMap.has(i) ? 1 : 0), 0);
			const danger = rows.some((_r, i) => (issueMap.get(i) || {}).severity === "danger");
			status = flagged
				? {
					cls: danger ? "danger" : "warn",
					icon: "fa-exclamation-triangle",
					text: __("{0} of {1} row(s) need attention.", [flagged, rows.length]),
				}
				: { cls: "ok", icon: "fa-check-circle", text: __("No issues found — {0} row(s) ready to import.", [rows.length]) };
		}

		// ── Dry-run counts (only returned when the rows resolved) ────────
		const out = me._validation || {};
		const metric = (label, val, cls) => `
			<div class="zk-preview-metric ${cls || ""}">
				<span class="metric-value">${val}</span>
				<span class="metric-label">${__(label)}</span>
			</div>`;
		const metrics = out.success ? `
			<div class="zk-preview-metrics">
				${metric("Would create", out.created, "ok")}
				${metric("Duplicates", out.duplicates, out.duplicates ? "warn" : "")}
				${metric("Double punches", out.double_punches, out.double_punches ? "warn" : "")}
				${metric("No employee", out.skipped_no_employee, out.skipped_no_employee ? "danger" : "")}
				${out.failed ? metric("Failed", out.failed, "danger") : ""}
			</div>` : "";

		// ── Findings (errors / warnings) ─────────────────────────────────
		const findings = me.buildFindings();
		const MAX_FINDINGS = 12;
		const rowsLabel = (g) => {
			if (g.meta) return g.meta;
			if (!g.rows.length) return "";
			const list = g.rows.slice(0, 8).join(", ");
			const more = g.rows.length > 8 ? __(" (+{0} more)", [g.rows.length - 8]) : "";
			return g.rows.length === 1 ? __("Row {0}", [g.rows[0]]) : __("Rows {0}{1}", [list, more]);
		};
		const issues = findings.length ? `
			<div class="zk-preview-issues">
				<div class="zk-preview-issues-head">
					<i class="fa fa-exclamation-triangle"></i>
					<span>${__("Issues found")}</span>
					<span class="zk-preview-count">${findings.length}</span>
				</div>
				${findings.slice(0, MAX_FINDINGS).map(g => `
					<div class="zk-preview-issue ${g.severity}">
						<i class="fa ${g.severity === "danger" ? "fa-times-circle" : "fa-exclamation-circle"}"></i>
						<div class="zk-preview-issue-body">
							<div class="zk-preview-issue-text">${frappe.utils.escape_html(g.text)}</div>
							${rowsLabel(g) ? `<div class="zk-preview-issue-meta">${frappe.utils.escape_html(rowsLabel(g))}</div>` : ""}
						</div>
					</div>`).join("")}
				${findings.length > MAX_FINDINGS ? `
					<div class="zk-preview-issue-meta text-muted">
						${__("… and {0} more kind(s) of issue.", [findings.length - MAX_FINDINGS])}
					</div>` : ""}
			</div>` : "";

		// ── Per-row status chip ─────────────────────────────────────────
		const statusCell = (i) => {
			const it = issueMap.get(i);
			if (!it) return `<span class="zk-prev-state ok"><i class="fa fa-check"></i>${__("OK")}</span>`;
			const more = it.count > 1 ? ` <span class="zk-prev-state-more">+${it.count - 1}</span>` : "";
			return `<span class="zk-prev-state ${it.severity}" title="${frappe.utils.escape_html(it.text)}">
				<i class="fa ${it.severity === "danger" ? "fa-times-circle" : "fa-exclamation-circle"}"></i>
				${frappe.utils.escape_html(it.text)}${more}
			</span>`;
		};
		const rowClass = (i) => {
			const it = issueMap.get(i);
			if (!it) return "";
			return it.severity === "danger" ? "zk-prev-row-danger" : "zk-prev-row-warn";
		};

		this.page.main.find(".zk-import-preview").html(`
			<div class="zk-import-card zk-import-preview-card">
				<div class="zk-import-preview-head">
					<div class="zk-import-preview-title">
						<i class="fa fa-eye"></i>
						<b>${__("File preview")}</b>
						<span class="text-muted small">${frappe.utils.escape_html(me._preview_file || "")}</span>
					</div>
					<div class="zk-import-preview-pills">
						${pill(`${__("Rows")}: ${rows.length}`)}
						${pill(`${__("No log_type")}: ${rows.filter(r => !r.log_type).length}`, rows.some(r => !r.log_type) ? "warn" : "")}
						${pill(`${__("Missing user_id")}: ${countOf(__("Missing user_id"))}`, countOf(__("Missing user_id")) ? "danger" : "")}
						${pill(`${__("Missing date")}: ${countOf(__("Missing date"))}`, countOf(__("Missing date")) ? "danger" : "")}
						${pill(`${__("Missing time")}: ${countOf(__("Missing time"))}`, countOf(__("Missing time")) ? "danger" : "")}
					</div>
				</div>
				<div class="zk-preview-status ${status.cls}">
					<i class="fa ${status.icon}"></i>
					<span>${frappe.utils.escape_html(status.text)}</span>
				</div>
				${metrics}
				${issues}
				<div class="zk-import-preview-scroll">
					<table class="zk-import-preview-table">
						<thead>
							<tr>
								<th class="zk-prev-col-idx">${__("#")}</th>
								<th><code>user_id</code></th>
								<th>${__("Date")}</th>
								<th>${__("Time")}</th>
								<th><code>log_type</code></th>
								<th class="zk-prev-col-status">${__("Status")}</th>
							</tr>
						</thead>
						<tbody>
							${shown.map((r, i) => `
							<tr class="${rowClass(i)}">
								<td class="zk-prev-col-idx">${i + 1}</td>
								<td class="zk-prev-cell-id">${frappe.utils.escape_html(r.user_id) || emptyCell}</td>
								<td class="zk-prev-cell-date">${frappe.utils.escape_html(r.date) || emptyCell}</td>
								<td class="zk-prev-cell-time">${frappe.utils.escape_html(r.time) || emptyCell}</td>
								<td>${r.log_type
										? `<span class="zk-prev-log ${r.log_type.toUpperCase() === "IN" ? "log-in" : "log-out"}">${frappe.utils.escape_html(r.log_type)}</span>`
										: `<span class="zk-prev-alternate">${__("alternate")}</span>`}</td>
								<td class="zk-prev-col-status">${statusCell(i)}</td>
							</tr>`).join("")}
						</tbody>
					</table>
				</div>
				<div class="zk-import-preview-more">
					${rows.length > MAX_ROWS
						? __("Showing first {0} of {1} rows.", [MAX_ROWS, rows.length])
						: __("{0} row(s) in this file.", [rows.length])}
				</div>
			</div>
		`);
	},

	// Validation runs automatically (there is no Preview button): the dry-run
	// pass on the server reports counts + per-row problems, which are merged
	// into the preview card. Re-run whenever the file or the device changes.
	runPreview() {
		const me = this;
		if (!me.rows.length) return;

		const device = me.device_field && me.device_field.get_value();
		me._validation = null;
		me._validation_error = "";
		if (!device) {
			// no device yet — nothing to resolve rows against; the preview
			// still shows the local row checks
			me.preview_pending = false;
			me.renderPreview();
			return;
		}

		const seq = (me._preview_seq = (me._preview_seq || 0) + 1);
		me.preview_pending = true;
		me.renderPreview();

		frappe.call({
			method: "zkteco_attendance.zkteco_attendance.page.zk_import_checkins.zk_import_checkins.import_checkins",
			args: {
				device_name: device,
				rows: JSON.stringify(me.rows),
				dry_run: 1,
			},
			callback(r) {
				// a newer file / device selection won the race — drop this result
				if (seq !== me._preview_seq) return;
				me.preview_pending = false;
				const out = (r && r.message) || {};
				me._validation = out;
				me._validation_error = out.success ? "" : (out.error || __("Unknown error."));
				me.renderPreview();
			},
			error() {
				if (seq !== me._preview_seq) return;
				me.preview_pending = false;
				me._validation = null;
				me._validation_error = __("Could not check the rows against the device.");
				me.renderPreview();
			},
		});
	},

	// shared stat-row renderer (label is translated here, once)
	stat_row(label, val, cls) {
		return `
			<div class="zk-import-stat ${cls || ""}">
				<span class="stat-label">${__(label)}</span>
				<span class="stat-value">${val}</span>
			</div>`;
	},

	// split a raw timestamp ("2026-02-01 08:02:11", "2026-02-01T08:02:11",
	// "2026-02-01,08:02:11" …) into its date and time parts for display.
	// Falls back gracefully: a date-only value returns {date, time: ""},
	// and anything unparsable lands wholly in as-is so the user can see it.
	splitDateTime(ts) {
		if (!ts) return { date: "", time: "" };
		const s = String(ts).replace(/[,T]/, " ").trim();
		const idx = s.indexOf(" ");
		if (idx === -1) {
			// no separator: date covers the day portion, time the rest of
			// a single-token value like "YYYY-MM-DD" (no time at all)
			return { date: s, time: "" };
		}
		return { date: s.slice(0, idx), time: s.slice(idx + 1) };
	},

	// ── Import (commit) ─────────────────────────────────────────────────
	// Validation lives in runPreview() now, so this always writes.
	doImport() {
		const me = this;
		const device = me.device_field.get_value();
		if (!device) {
			frappe.msgprint(__("Select a device first."));
			return;
		}
		if (!me.rows.length) {
			frappe.msgprint(__("Choose a file first."));
			return;
		}

		frappe.call({
			method: "zkteco_attendance.zkteco_attendance.page.zk_import_checkins.zk_import_checkins.import_checkins",				args: {
					device_name: device,
					rows: JSON.stringify(me.rows),
					dry_run: 0,
				},
				freeze: true,
				freeze_message: __("Importing checkins…"),
				callback(r) {
					const out = r.message || {};
					if (!out.success) {
						frappe.msgprint({
							title: __("Import Failed"),
							indicator: "red",
							message: frappe.utils.escape_html(out.error || __("Unknown error.")),
						});
						return;
					}
					me.renderResult(out);
				},
		});
	},

	renderResult(out) {
		const me = this;
		const $res = me.page.main.find(".zk-import-result");
		const $prob = me.page.main.find(".zk-import-problems");
		const stat = (label, val, cls) => me.stat_row(label, val, cls);

		$res.html(`
			<div class="zk-import-card">
				<b>${__("Import Result")}</b>
				<div class="zk-import-stats">
					${stat("Rows in file", out.total_rows, "")}
					${stat("Created", out.created, "ok")}
					${stat("Duplicates skipped", out.duplicates, "warn")}
					${stat("Double punches", out.double_punches, "warn")}
					${stat("No employee", out.skipped_no_employee, "danger")}
					${out.failed ? stat("Failed", out.failed, "danger") : ""}
				</div>
				${out.unmatched_ids && out.unmatched_ids.length ? `
					<div class="text-muted small zk-import-unmatched">
						${__("Biometric IDs with no Employee mapping:")} <code>${out.unmatched_ids.join(", ")}</code>
						<br>${__("Map these on the device (Browse Employees On Device) and import again to pick them up.")}
					</div>` : ""}
				${out.created ? `
					<div class="zk-import-actions">
						<button class="btn btn-xs btn-secondary" onclick="frappe.set_route('List', 'Employee Checkin', {device_id: '${frappe.utils.escape_html(out.device || "")}'});">
							${__("View Employee Checkins")}
						</button>
					</div>` : ""}
			</div>
		`);

		// show row-level problems if any (bad rows in file), styled like the
		// findings list shown with the preview
		if (out.problems && out.problems.length) {
			$prob.html(`
				<div class="zk-import-card zk-import-problems-card">
					<div class="zk-preview-issues-head">
						<i class="fa fa-exclamation-triangle"></i>
						<span>${__("Row problems")}</span>
						<span class="zk-preview-count">${out.problems.length}</span>
					</div>
					${out.problems.map(p => `
						<div class="zk-preview-issue danger">
							<i class="fa fa-times-circle"></i>
							<div class="zk-preview-issue-body">
								<div class="zk-preview-issue-text">${frappe.utils.escape_html(p)}</div>
							</div>
						</div>`).join("")}
				</div>
			`);
		} else {
			$prob.empty();
		}
	},
});
