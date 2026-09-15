# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from sales_ai import tools
from sales_ai.guard.policy import THRESHOLD


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

		if self.mode == THRESHOLD:
			self._validate_threshold()
		else:
			self.threshold = None
			self.currency = None

	def _validate_threshold(self) -> None:
		"""A threshold that lets the agent act alone has to be stated exactly.

		Every branch here would otherwise fail open in the admin's favour: a threshold with
		no currency would compare rupees to dollars, and a tool whose value cannot be worked
		out would sit under any threshold and never ask anyone.
		"""
		if not self.currency:
			frappe.throw(
				_("A threshold needs a currency, or there is no saying what {0} means.").format(
					frappe.bold(self.threshold or 0)
				)
			)
		if (self.threshold or 0) < 0:
			frappe.throw(_("A threshold cannot be negative."))

		handle = tools.get(self.tool)
		if handle and not handle.meta.get("preview"):
			frappe.throw(
				_(
					"{0} has no value to compare against a threshold, so this rule would send "
					"every call for approval. Use Require Approval instead."
				).format(frappe.bold(self.tool)),
				title=_("Cannot Be Priced"),
			)
