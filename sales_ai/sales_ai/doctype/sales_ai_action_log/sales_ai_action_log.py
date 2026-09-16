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
	_write(
		{
			"action": action,
			"outcome": "Allowed",
			"tool": tool,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"changes": json.dumps(changes, default=str),
		}
	)


def record_denial(
	*,
	action: str | None,
	tool: str | None,
	reference_doctype: str | None,
	reference_name: str | None,
	reason: str,
) -> None:
	"""Log an attempt that was refused.

	Refusals are worth more than the changes on their own. One is a user asking about
	something that turned out not to be theirs; the same one fifty times in an afternoon is
	somebody working out where the edges are, and nothing else in the system would show it.

	`reason` is the wording the user was given rather than the internal cause, so reading
	this row alongside the conversation does not require translating between the two.
	"""
	_write(
		{
			"action": action,
			"outcome": "Denied",
			"tool": tool,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"reason": reason,
		}
	)


def _write(values: dict[str, Any]) -> None:
	"""Insert one row, or give up quietly.

	Never raises, for the same reason in both directions: an allowed change must not be
	undone because its audit row failed, and a refusal must not turn into a different error
	than the one the user was about to be shown.
	"""
	try:
		doc = frappe.get_doc(
			{
				"doctype": "Sales AI Action Log",
				"user": frappe.session.user,
				"run": frappe.flags.get("sales_ai_run"),
				"playbook_run": frappe.flags.get("sales_ai_playbook_run"),
				**values,
			}
		)
		# A refused attempt often names a record that does not exist — that is frequently
		# why it was refused. The name is kept as typed, so link validation has to be off.
		doc.flags.ignore_links = True
		doc.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Sales AI: could not write the action log")
