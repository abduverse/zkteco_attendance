frappe.pages["zk-import-checkins"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Import Checkins"),
		single_column: true,
	});

	const me = this;
	page.set_primary_action(__("Import"), () => me.doImport(true), "fa fa-upload");
	page.add_menu_item(__("Preview (dry run)"), () => me.doImport(false));

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
					<i class="fa fa-cloud-download"></i>
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
							<div>${__("Click to choose a CSV file, or drop it here")}</div>
						</div>
						<input type="file" accept=".csv,text/csv" class="zk-import-file-input" />
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
			if (f) me.parseCsv(f);
		});
		$input.on("change", function () {
			const f = this.files && this.files[0];
			if (f) me.parseCsv(f);
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
				},
				get_query() {
					return { filters: { enable: 1 } };
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

	// ── CSV parsing ─────────────────────────────────────────────────────
	parseCsv(file) {
		const me = this;
		const reader = new FileReader();
		reader.onload = function (e) {
			const text = String(e.target.result || "");
			const lines = text.split(/\r?\n/).filter(l => l.trim());
			if (!lines.length) {
				frappe.msgprint(__("The file is empty."));
				return;
			}

			// naive CSV split — good enough for the expected schema
			// (no embedded commas / quotes in device ids or timestamps)
			const splitLine = (line) => {
				const delim = line.indexOf(";") !== -1 && line.indexOf(",") === -1 ? ";" : ",";
				return line.split(delim).map(s => s.trim().replace(/^"|"$/g, ""));
			};

			const header = splitLine(lines[0]).map(h => h.toLowerCase().replace(/[^a-z_]/g, ""));
			const idxUserId = header.findIndex(h => h === "user_id" || h === "userid" || h === "id" || h === "badge" || h === "badge_number");
			const idxTime = header.findIndex(h => h === "time" || h === "timestamp" || h === "datetime" || h === "date_time" || h === "punch");
			const idxLog = header.findIndex(h => h === "log_type" || h === "logtype" || h === "type" || h === "status");

			if (idxUserId === -1 || idxTime === -1) {
				frappe.msgprint({
					title: __("Bad File Header"),
					indicator: "red",
					message: __("Could not find user_id and time columns. Expected a header row like: user_id, time, log_type"),
				});
				return;
			}

			const rows = [];
			for (let i = 1; i < lines.length; i++) {
				const parts = splitLine(lines[i]);
				const uid = parts[idxUserId] || "";
				const ts = parts[idxTime] || "";
				const lt = idxLog >= 0 ? (parts[idxLog] || "") : "";
				if (!uid && !ts) continue;
				rows.push({ user_id: uid, timestamp: ts, log_type: lt });
			}

			me.rows = rows;
			me.showSummary(rows.length, file.name);
		};
		reader.readAsText(file);
	},

	showSummary(n, fileName) {
		const $sum = this.page.main.find(".zk-import-summary");
		$sum.html(`
			<div class="zk-import-stats">
				${this.stat_row("File", frappe.utils.escape_html(fileName || "—"), "")}
				${this.stat_row("Rows parsed", n, "ok")}
				<div class="zk-import-hint text-muted small">${__("Choose Preview to check for problems before importing.")}</div>
			</div>
		`);
	},

	// shared stat-row renderer (label is translated here, once)
	stat_row(label, val, cls) {
		return `
			<div class="zk-import-stat ${cls || ""}">
				<span class="stat-label">${__(label)}</span>
				<span class="stat-value">${val}</span>
			</div>`;
	},

	// ── Preview / Import ────────────────────────────────────────────────
	doImport(commit) {
		const me = this;
		const device = me.device_field.get_value();
		if (!device) {
			frappe.msgprint(__("Select a device first."));
			return;
		}
		if (!me.rows.length) {
			frappe.msgprint(__("Choose a CSV file first."));
			return;
		}

		frappe.call({
			method: "zkteco_attendance.zkteco_attendance.page.zk_import_checkins.zk_import_checkins.import_checkins",
			args: {
				device_name: device,
				rows: JSON.stringify(me.rows),
				dry_run: commit ? 0 : 1,
			},
			freeze: true,
			freeze_message: commit ? __("Importing checkins…") : __("Checking rows…"),
			callback(r) {
				const out = r.message || {};
				if (!out.success) {
					frappe.msgprint({
						title: commit ? __("Import Failed") : __("Preview Failed"),
						indicator: "red",
						message: frappe.utils.escape_html(out.error || __("Unknown error.")),
					});
					return;
				}
				me.renderResult(out, commit);
			},
		});
	},

	renderResult(out, commit) {
		const me = this;
		const $res = me.page.main.find(".zk-import-result");
		const $prob = me.page.main.find(".zk-import-problems");
		const stat = (label, val, cls) => me.stat_row(label, val, cls);

		const title = out.dry_run ? __("Preview Result (nothing imported)") : __("Import Result");
		$res.html(`
			<div class="zk-import-card">
				<b>${title}</b>
				<div class="zk-import-stats">
					${stat("Rows in file", out.total_rows, "")}
					${stat(commit ? "Created" : "Would create", out.created, "ok")}
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
				${commit && out.created ? `
					<div class="zk-import-actions">
						<button class="btn btn-xs btn-secondary" onclick="frappe.set_route('List', 'Employee Checkin', {device_id: '${frappe.utils.escape_html(out.device || "")}'});">
							${__("View Employee Checkins")}
						</button>
					</div>` : ""}
			</div>
		`);

		// show row-level problems if any (bad rows in file)
		if (out.problems && out.problems.length) {
			$prob.html(`
				<div class="alert alert-warning">
					<b>${__("Row problems")}</b>
					<ul class="zk-import-problem-list small">
						${out.problems.map(p => `<li>${frappe.utils.escape_html(p)}</li>`).join("")}
					</ul>
				</div>
			`);
		} else {
			$prob.empty();
		}
	},
});
