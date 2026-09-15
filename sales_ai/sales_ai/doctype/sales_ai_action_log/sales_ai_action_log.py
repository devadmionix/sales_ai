# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

import json
from typing import Any

import frappe
from frappe.model.document import Document


class SalesAIActionLog(Document):
	pass


def record_action(
	*,
	action: str,
	tool: str,
	reference_doctype: str,
	reference_name: str,
	changes: dict[str, Any],
) -> None:
	"""Log a change the agent made.

	Never raises. A successful write should not be undone because its audit row failed to
	save; the failure is logged instead. Whether the change was approved by a human or
	allowed by a policy is on the run, which this row links to.
	"""
	try:
		frappe.get_doc(
			{
				"doctype": "Sales AI Action Log",
				"action": action,
				"tool": tool,
				"reference_doctype": reference_doctype,
				"reference_name": reference_name,
				"user": frappe.session.user,
				"run": frappe.flags.get("sales_ai_run"),
				"playbook_run": frappe.flags.get("sales_ai_playbook_run"),
				"changes": json.dumps(changes, default=str),
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Sales AI: could not write the action log")
