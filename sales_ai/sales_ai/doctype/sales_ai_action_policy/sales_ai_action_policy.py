# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from sales_ai import tools


class SalesAIActionPolicy(Document):
	def validate(self) -> None:
		if self.tool not in set(tools.names()):
			frappe.throw(
				_("{0} is not a registered tool.").format(frappe.bold(self.tool)),
				title=_("Unknown Tool"),
			)
		if self.mode == "Deny" and not (self.message or "").strip():
			# A refusal the agent cannot explain is a refusal the user cannot act on.
			frappe.throw(_("A denial needs a message saying why."))
