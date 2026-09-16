// Copyright (c) 2026, Admionix and contributors
// For license information, please see license.txt

frappe.query_reports["Sales AI Unsupervised Actions"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			// A week, because this is meant to be read weekly. Left as a filter rather
			// than fixed so that looking further back after an incident is one change.
			default: frappe.datetime.add_days(frappe.datetime.get_today(), -7),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "user",
			label: __("On Behalf Of"),
			fieldtype: "Link",
			options: "User",
		},
		{
			fieldname: "tool",
			label: __("Tool"),
			fieldtype: "Data",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		// The row worth stopping on: something ran unsupervised that the policy would no
		// longer permit, so the rule was loosened at the time and has since been tightened.
		if (column.fieldname === "still_allowed" && data && !data.still_allowed) {
			value = `<span style="color: var(--red-600)">${value}</span>`;
		}
		return value;
	},
};
