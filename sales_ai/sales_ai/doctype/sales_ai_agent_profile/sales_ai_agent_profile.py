# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from sales_ai import tools


class SalesAIAgentProfile(Document):
	def validate(self) -> None:
		self._check_tools()
		if self.max_iterations is not None and self.max_iterations < 1:
			frappe.throw(_("Max Iterations must be at least 1."))

	def _check_tools(self) -> None:
		"""A typo in a tool name should fail here, not silently disarm the agent at run time."""
		known = set(tools.names())
		seen: set[str] = set()
		for row in self.tools:
			if row.tool not in known:
				frappe.throw(
					_("Row {0}: {1} is not a registered tool.").format(row.idx, frappe.bold(row.tool)),
					title=_("Unknown Tool"),
				)
			if row.tool in seen:
				frappe.throw(_("Row {0}: {1} is listed twice.").format(row.idx, frappe.bold(row.tool)))
			seen.add(row.tool)
