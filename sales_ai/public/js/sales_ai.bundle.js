// Copyright (c) 2026, Admionix and contributors
// For license information, please see license.txt

// Mounts the assistant panel into the desk. It lives in its own element at the end of
// <body>, so it never competes with a page's own layout.

import { createApp } from "vue";
import ChatPanel from "./panel/ChatPanel.vue";

frappe.provide("frappe.sales_ai");

function mount() {
	if (frappe.sales_ai.panel) return;

	const wrapper = document.createElement("div");
	document.body.appendChild(wrapper);

	const app = createApp(ChatPanel);
	SetVueGlobals(app);
	frappe.sales_ai.panel = app.mount(wrapper);

	frappe.ui.keys.add_shortcut({
		shortcut: "ctrl+shift+a",
		action: () => frappe.sales_ai.panel.toggle(),
		description: __("Toggle Sales AI"),
	});

	watch_background_runs();
}

// An unattended run has no window to report into, so it says what it is doing here. The
// approval itself goes to the notification bell and the user's to-do list, which survive
// a closed tab; this is only the nudge.
function watch_background_runs() {
	frappe.realtime.on("sales_ai:run", (data) => {
		if (data.state === "paused") {
			frappe.show_alert(
				{
					message: __("Sales AI needs your approval"),
					indicator: "orange",
				},
				10
			);
		} else if (data.state === "completed") {
			frappe.show_alert({ message: __("Sales AI finished a task"), indicator: "green" }, 5);
		}
	});
}

$(document).on("app_ready", () => {
	// The flag comes from the bootinfo, so turning Sales AI off hides it for everyone
	// on their next desk load rather than letting them start a run that will be refused.
	if (frappe.boot.sales_ai && frappe.boot.sales_ai.enabled) mount();
});
