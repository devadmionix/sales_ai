# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Reminders: making them, listing them, moving them and closing them.

A follow-up is a ToDo. That is not an implementation detail worth hiding — it is the same
row the desk shows in the sidebar and the same row an Assignment Rule writes, so a reminder
the agent makes is one a person can see and close, and vice versa.

Two rules run through everything here:

- **Somebody else's reminder is not yours to touch.** ToDo carries no company or territory,
  so ERPNext's usual scoping does nothing for it and a bare `write` permission would let one
  salesperson close another's list. Every path checks the row is the caller's own — theirs
  to do, or theirs because they raised it — before anything else.
- **Handing a reminder to a colleague goes through the same gate as handing a record over.**
  `guard.writes._assignable` refuses an assignee who cannot already see the record, because
  otherwise a reminder becomes a way to grant read access. A reminder pointing at a lead is
  no different, so it reuses that check rather than writing a laxer one.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import escape_html, getdate

from sales_ai.guard import GuardError, deny, may_change
from sales_ai.guard.specs import SPECS
from sales_ai.sales_ai.doctype.sales_ai_action_log.sales_ai_action_log import record_action

MAX_NOTE = 2000
MAX_LIST = 50

# "Cancelled" is the desk's word for a reminder that turned out not to be needed. It is
# offered alongside "Closed" because the distinction is real — done, versus never happening —
# and a model that only has "Closed" will mark things done that were not.
STATUSES = ("Open", "Closed", "Cancelled")


def list_follow_ups(
	*,
	doctype: str | None = None,
	name: str | None = None,
	user: str | None = None,
	status: str = "Open",
	limit: int = 20,
) -> dict[str, Any]:
	"""The reminders on a record, or the ones somebody is carrying.

	Read through `frappe.get_list`, so ToDo's own permission rules apply — an ordinary
	user sees their own and no one else's, and a manager sees the team's. Nothing here
	widens that; it only narrows it to the question asked.
	"""
	if status not in STATUSES:
		raise GuardError(f"Status must be one of {', '.join(STATUSES)}.")

	filters: dict[str, Any] = {"status": status}
	if doctype:
		if doctype not in SPECS:
			raise GuardError(f"{doctype!r} is not a record the assistant works with.")
		filters["reference_type"] = doctype
	if name:
		filters["reference_name"] = name
	if user:
		filters["allocated_to"] = user

	rows = frappe.get_list(
		"ToDo",
		filters=filters,
		fields=[
			"name",
			"date",
			"status",
			"priority",
			"allocated_to",
			"reference_type",
			"reference_name",
			"description",
		],
		order_by="date asc",
		limit=max(1, min(int(limit or 20), MAX_LIST)),
	)

	return {"count": len(rows), "follow_ups": [_summarise(row) for row in rows]}


def update_follow_up(
	name: str,
	*,
	date: str | None = None,
	description: str | None = None,
	status: str | None = None,
	allocate_to: str | None = None,
	tool: str,
) -> dict[str, Any]:
	"""Move a reminder, rewrite it, close it, or hand it to somebody else.

	One function rather than four, because they are one sentence in practice — "push the
	Sharma call to Friday and give it to Priya" — and because they share every check.
	"""
	if date is None and description is None and status is None and allocate_to is None:
		raise GuardError("Nothing to change was given. Say what to move, rewrite, close or hand over.")

	todo = _mine(name)

	changes: dict[str, Any] = {}

	if status is not None:
		if status not in STATUSES:
			raise GuardError(f"Status must be one of {', '.join(STATUSES)}.")
		if todo.status != status:
			changes["status"] = {"from": todo.status, "to": status}
			todo.status = status

	if date is not None:
		# Parsed rather than trusted: a malformed date reaches ERPNext as a save failure,
		# which the agent loop reports as its generic phrase and the user cannot act on.
		try:
			parsed = str(getdate(date))
		except Exception:
			raise GuardError(f"{date!r} is not a date. Use YYYY-MM-DD.") from None
		if str(todo.date) != parsed:
			changes["date"] = {"from": str(todo.date), "to": parsed}
			todo.date = parsed

	if description is not None:
		text = description.strip()
		if not text:
			raise GuardError("The description cannot be emptied.")
		# Renders as HTML in the desk sidebar, and was written by a model that has been
		# reading whatever the record's own notes say.
		todo.description = escape_html(text[:MAX_NOTE])
		changes["description"] = text[:MAX_NOTE]

	if allocate_to is not None:
		changes["allocated_to"] = {
			"from": todo.allocated_to,
			"to": _handover(todo, allocate_to),
		}
		todo.allocated_to = changes["allocated_to"]["to"]

	if not changes:
		return {"unchanged": "ToDo", "name": todo.name, "note": "Every value was already set."}

	todo.save()

	record_action(
		action="Follow Up",
		tool=tool,
		reference_doctype=todo.reference_type,
		reference_name=todo.reference_name,
		changes={"follow_up": todo.name, **changes},
	)

	return {"updated": "Follow Up", "name": todo.name, "changed": sorted(changes), **_summarise(todo)}


# -- internals -----------------------------------------------------------------------


def _mine(name: str):
	"""A reminder the caller is entitled to change, or the same refusal as a missing one.

	ToDo has no company and no territory, so a User Permission scopes nothing here and
	`frappe.has_permission` on its own would let anyone with the Sales User role close
	anyone else's list. Ownership is the only real boundary, so it is checked explicitly:
	the person who has to do it, or the person who asked for it.

	The refusal is the same phrase as "no such reminder" on purpose. Otherwise a model
	could count a colleague's workload by walking IDs and watching the wording change.
	"""
	todo = may_change("ToDo", name, "read", "Follow Up")

	if frappe.session.user not in (todo.allocated_to, todo.owner):
		raise deny(
			"Follow Up",
			"ToDo",
			name,
			f"No follow-up called {name!r} is available to you.",
		)

	if todo.reference_type and todo.reference_type not in SPECS:
		raise GuardError(
			f"That follow-up is about a {todo.reference_type}, which is not a record the "
			"assistant works with."
		)

	return todo


def _handover(todo, allocate_to: str) -> str:
	"""Give the reminder to somebody else, under the assignment rules.

	Reusing `guard.writes._assignable` rather than repeating it, because the leak it closes
	is the same one: a reminder pointing at a lead is visible to whoever holds it, so
	allocating one to somebody who cannot see the lead would tell them it exists.
	"""
	from sales_ai.guard.writes import _assignable

	if not todo.reference_type:
		# Nothing to leak, so only the "is this a real colleague" half applies. Checked
		# against ToDo itself so the function still has a DocType to name in the refusal.
		return _assignable(allocate_to, "ToDo", todo.name)

	return _assignable(allocate_to, todo.reference_type, todo.reference_name)


def _summarise(row) -> dict[str, Any]:
	return {
		"name": row.get("name"),
		"date": str(row.get("date")) if row.get("date") else None,
		"status": row.get("status"),
		"allocated_to": row.get("allocated_to"),
		"about": f"{row.get('reference_type')} {row.get('reference_name')}"
		if row.get("reference_type")
		else None,
		"description": row.get("description"),
	}
