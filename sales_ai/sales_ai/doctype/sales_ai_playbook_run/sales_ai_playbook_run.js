// Copyright (c) 2026, Admionix and contributors
// For license information, please see license.txt

frappe.ui.form.on("Sales AI Playbook Run", {
	refresh(frm) {
		draw(frm);

		if (frm.doc.status === "Paused" && frm.doc.question) {
			frm.dashboard.set_headline(__("This run is waiting for you."));
			frm.add_custom_button(__("Answer"), () => ask(frm)).addClass("btn-primary");
		}
		if (frm.doc.status === "Waiting" && frm.doc.resume_at) {
			frm.dashboard.set_headline(
				__("Waiting until {0}.", [frappe.datetime.str_to_user(frm.doc.resume_at)])
			);
		}
	},
});

function draw(frm) {
	const wrapper = frm.get_field("graph").$wrapper;
	frappe.call({
		method: "sales_ai.api.playbook_graph",
		args: { playbook_run: frm.doc.name },
		callback: (r) =>
			sales_ai.graph.render(wrapper, r.message, { on_click: (key) => inspect(frm, key) }),
	});
}

// -- the step inspector -----------------------------------------------------------------

// A step inside a loop has a trace row per pass, so all of them are shown. What you usually
// want to know is which pass went wrong.
function inspect(frm, key) {
	const passes = (frm.doc.trace || []).filter((row) => row.step_key === key);
	if (!passes.length) {
		frappe.msgprint({
			title: __("Not Reached"),
			message: __("The run never got to {0}.", [key]),
			indicator: "gray",
		});
		return;
	}

	new frappe.ui.Dialog({
		title: key,
		size: "large",
		fields: [{ fieldtype: "HTML", options: passes.map(describe).join("<hr>") }],
	}).show();
}

const TONE = { Done: "green", Skipped: "gray", Refused: "orange", Failed: "red" };

function describe(row) {
	const facts = [
		`<span class="indicator-pill ${TONE[row.status] || "gray"}">${frappe.utils.escape_html(
			row.status
		)}</span>`,
		row.iteration ? __("pass {0}", [row.iteration]) : "",
		`${row.duration_ms} ms`,
		frappe.datetime.str_to_user(row.started_at),
	].filter(Boolean);

	return `
		<div class="mb-2">${facts.join(" &nbsp;·&nbsp; ")}</div>
		${row.note ? `<p>${frappe.utils.escape_html(row.note)}</p>` : ""}
		${block(__("Given"), row.inputs)}
		${block(__("Produced"), row.output)}`;
}

function block(label, value) {
	if (!value) return "";
	return `
		<div class="text-muted small mt-3">${label}</div>
		<pre class="small" style="white-space:pre-wrap; max-height:220px; overflow:auto"
			>${frappe.utils.escape_html(pretty(value))}</pre>`;
}

function pretty(value) {
	try {
		return JSON.stringify(JSON.parse(value), null, 2);
	} catch (e) {
		return value;
	}
}

// -- answering --------------------------------------------------------------------------

function ask(frm) {
	const question = JSON.parse(frm.doc.question);

	const dialog = new frappe.ui.Dialog({
		title: __("Approval"),
		fields: [
			{ fieldtype: "HTML", options: `<p>${frappe.utils.escape_html(question.prompt)}</p>` },
			question.arguments
				? {
						fieldtype: "HTML",
						options: `<pre class="small" style="white-space:pre-wrap">${frappe.utils.escape_html(
							JSON.stringify(question.arguments, null, 2)
						)}</pre>`,
					}
				: { fieldtype: "HTML", options: "" },
			{
				fieldname: "reply",
				fieldtype: "Select",
				label: __("Reply"),
				options: question.options.join("\n"),
				default: question.options[0],
				reqd: 1,
			},
		],
		primary_action_label: __("Send"),
		primary_action: ({ reply }) => {
			dialog.hide();
			frappe.call({
				method: "sales_ai.api.answer_playbook",
				args: { playbook_run: frm.doc.name, reply },
				freeze: true,
				callback: () => {
					frappe.show_alert({ message: __("Carrying on."), indicator: "green" });
					frm.reload_doc();
				},
			});
		},
	});
	dialog.show();
}
