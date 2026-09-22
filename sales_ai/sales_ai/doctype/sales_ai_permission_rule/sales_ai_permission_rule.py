# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Configurable RBAC rule for the Sales AI chatbot.

Each row says what one role may do with one DocType through the assistant.
When rows exist they override the hard-coded defaults in ``permissions.py``;
when none exist the defaults apply unchanged.
"""

import frappe
from frappe.model.document import Document


class SalesAIPermissionRule(Document):
	def validate(self):
		if not self.allow_read:
			# If read is off, nothing else makes sense.
			for action in ("allow_create", "allow_write", "allow_delete",
			               "allow_submit", "allow_cancel", "allow_report"):
				if self.get(action):
					frappe.throw(
						f"Cannot allow {action.replace('allow_', '')} when Read is disabled."
					)
