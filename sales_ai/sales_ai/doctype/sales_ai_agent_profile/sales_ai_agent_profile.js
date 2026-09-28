// Copyright (c) 2026, Admionix and contributors
// For license information, please see license.txt

frappe.ui.form.on("Sales AI Agent Profile", {
	onload(frm) {
		// A profile with no tools can call nothing, so a new one starts with
		// every registered tool enabled. Existing rows are never touched.
		if (!frm.is_new()) return;
		if ((frm.doc.tools || []).length) return;

		frappe.call({
			method: "sales_ai.sales_ai.doctype.sales_ai_agent_profile.sales_ai_agent_profile.list_tools",
			callback: (r) => {
				if (!frm.is_new()) return;
				if ((frm.doc.tools || []).length) return;
				for (const tool of r.message || []) {
					frm.add_child("tools", { tool: tool, enabled: 1 });
				}
				frm.refresh_field("tools");
			},
		});
	},
});
