# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

import json
from typing import Any

import frappe
from frappe import _
from frappe.model.document import Document


class SalesAISession(Document):
	def before_insert(self) -> None:
		# The owner of a conversation is always the session user; never taken from input.
		self.user = frappe.session.user

	def validate(self) -> None:
		if self.user != frappe.session.user and "System Manager" not in frappe.get_roles():
			frappe.throw(_("You cannot change the owner of a session."), frappe.PermissionError)

	def get_transcript(self) -> list[dict[str, Any]]:
		if not self.transcript:
			return []
		messages = json.loads(self.transcript) if isinstance(self.transcript, str) else self.transcript
		return messages if isinstance(messages, list) else []

	def set_transcript(self, messages: list[dict[str, Any]]) -> None:
		self.db_set("transcript", json.dumps(messages), update_modified=True)
