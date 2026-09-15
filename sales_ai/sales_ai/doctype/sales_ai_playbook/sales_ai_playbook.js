// Copyright (c) 2026, Admionix and contributors
// For license information, please see license.txt

frappe.ui.form.on("Sales AI Playbook", {
	refresh(frm) {
		draw(frm);

		if (frm.is_new()) return;

		frm.add_custom_button(__("Dry Run"), () => ask_reference(frm, dry_run));
		frm.add_custom_button(__("Run Now"), () => ask_reference(frm, run_now)).addClass(
			"btn-primary"
		);

		if (!frm.doc.enabled) {
			frm.dashboard.set_headline(
				__("This playbook is disabled, so triggers will not start it. Run Now still works.")
			);
		}
	},
});

// The graph is drawn from the saved steps, so it shows what would run rather than what is
// half-typed in the grid.
function draw(frm) {
	const wrapper = frm.get_field("graph").$wrapper;
	if (frm.is_new()) {
		wrapper.html(`<div class="text-muted">${__("Save the playbook to see its shape.")}</div>`);
		return;
	}

	frappe.call({
		method: "sales_ai.api.playbook_graph",
		args: { playbook: frm.doc.name },
		callback: (r) => sales_ai.graph.render(wrapper, r.message, { on_click: (key) => open_step(frm, key) }),
	});
}

// Clicking a box opens that step in the grid, so the picture doubles as a way in.
function open_step(frm, key) {
	const step = (frm.doc.steps || []).find((row) => row.step_key === key);
	if (!step) return;
	frm.fields_dict.steps.grid.grid_rows_by_docname[step.name]?.toggle_view(true);
}

// -- running ----------------------------------------------------------------------------

// Most playbooks are written against a document, and a dry run against nothing tells you
// very little, so both actions ask what to point at first.
function ask_reference(frm, action) {
	const dialog = new frappe.ui.Dialog({
		title: __("Against which document?"),
		fields: [
			{
				fieldname: "reference_doctype",
				fieldtype: "Link",
				options: "DocType",
				label: __("Document Type"),
				get_query: () => ({ filters: { istable: 0, issingle: 0 } }),
			},
			{
				fieldname: "reference_name",
				fieldtype: "Dynamic Link",
				options: "reference_doctype",
				label: __("Document"),
			},
			{
				fieldtype: "HTML",
				options: `<p class="text-muted small">${__(
					"Leave both blank for a playbook that does not start from a document."
				)}</p>`,
			},
		],
		primary_action_label: __("Continue"),
		primary_action: (values) => {
			dialog.hide();
			action(frm, values);
		},
	});
	dialog.show();
}

function run_now(frm, args) {
	frappe.confirm(
		__("This runs for real, as you. Anything it is allowed to change, it will change."),
		() => {
			frappe.call({
				method: "sales_ai.api.run_playbook",
				args: { playbook: frm.doc.name, ...args },
				freeze: true,
				callback: () => {
					frappe.show_alert({ message: __("Queued."), indicator: "green" });
					frappe.set_route("List", "Sales AI Playbook Run", { playbook: frm.doc.name });
				},
			});
		}
	);
}

function dry_run(frm, args) {
	frappe.call({
		method: "sales_ai.api.dry_run_playbook",
		args: { playbook: frm.doc.name, ...args },
		freeze: true,
		freeze_message: __("Resolving every step…"),
		callback: (r) => show_dry_run(r.message || []),
	});
}

function show_dry_run(steps) {
	const rows = steps
		.map(
			(step) => `
			<tr>
				<td class="text-muted">${frappe.utils.escape_html(step.step_type)}</td>
				<td><b>${frappe.utils.escape_html(step.step_key)}</b></td>
				<td>
					<pre class="small" style="white-space:pre-wrap;margin:0;${
						step.ok ? "" : "color:var(--red-600)"
					}">${frappe.utils.escape_html(format_detail(step.detail))}</pre>
				</td>
			</tr>`
		)
		.join("");

	new frappe.ui.Dialog({
		title: __("Dry Run"),
		size: "large",
		fields: [
			{
				fieldtype: "HTML",
				options: `
					<p class="text-muted small">${__(
						"Nothing ran and nothing was written. Steps that read from earlier steps will report missing names, because those steps did not produce anything."
					)}</p>
					<table class="table table-sm">${rows}</table>`,
			},
		],
	}).show();
}

function format_detail(detail) {
	if (detail === null || detail === undefined) return "—";
	return typeof detail === "string" ? detail : JSON.stringify(detail, null, 2);
}
