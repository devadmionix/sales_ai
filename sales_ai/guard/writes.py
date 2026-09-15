# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Changing records, on the user's behalf and within their permissions.

Every write goes through an ERPNext controller — `insert()`, `save()`, `add_comment()` —
never through SQL. That is what makes the agent's writes behave like a person's: mandatory
fields, link validation, naming series, status logic and every `validate` hook in every
installed app all run exactly as they do in the desk.

Three things this module refuses to do:

- set a field that is not on the DocType's write allowlist, even if ERPNext would accept it
- decide which company a record belongs to; that comes from the user's own defaults
- touch anything the user could not touch themselves

Whether the agent was allowed to get this far at all is `sales_ai.guard.policy`'s job.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import escape_html

from sales_ai.guard import GuardError, read_document
from sales_ai.guard.specs import SPECS, WRITE_SPECS, WriteSpec
from sales_ai.sales_ai.doctype.sales_ai_action_log.sales_ai_action_log import record_action

MAX_NOTE = 2000


def create_record(doctype: str, values: dict[str, Any], *, tool: str) -> dict[str, Any]:
	spec = _spec(doctype)
	clean = _checked(spec, values, creating=True)

	missing = [f for f in spec.required if not clean.get(f)]
	if missing:
		raise GuardError(f"A new {spec.label} needs {', '.join(missing)}.")

	frappe.has_permission(doctype, "create", throw=True)

	doc = frappe.new_doc(doctype)
	doc.update(clean)
	_fill_company(doc)
	doc.insert()

	doc.add_comment("Info", _("Created by Sales AI for {0}.").format(frappe.session.user))
	record_action(
		action="Create",
		tool=tool,
		reference_doctype=doctype,
		reference_name=doc.name,
		changes=clean,
	)

	return {"created": doctype, "name": doc.name, "record": read_document(doctype, doc.name)}


def update_record(doctype: str, name: str, values: dict[str, Any], *, tool: str) -> dict[str, Any]:
	spec = _spec(doctype)
	clean = _checked(spec, values, creating=False)
	if not clean:
		raise GuardError("No fields to change were given.")

	doc = frappe.get_doc(doctype, name)
	doc.check_permission("write")

	changes = {
		field: {"from": doc.get(field), "to": value}
		for field, value in clean.items()
		if doc.get(field) != value
	}
	if not changes:
		return {"unchanged": doctype, "name": doc.name, "note": "Every value was already set."}

	doc.update({field: change["to"] for field, change in changes.items()})
	doc.save()

	doc.add_comment(
		"Info",
		_("Changed by Sales AI for {0}: {1}.").format(
			frappe.session.user, ", ".join(sorted(changes))
		),
	)
	record_action(
		action="Update",
		tool=tool,
		reference_doctype=doctype,
		reference_name=doc.name,
		changes=changes,
	)

	return {
		"updated": doctype,
		"name": doc.name,
		"changed": sorted(changes),
		"record": read_document(doctype, doc.name),
	}


def add_note(doctype: str, name: str, note: str, *, tool: str) -> dict[str, Any]:
	"""Put a note on a record's timeline. Notes are additive, so nothing is overwritten."""
	if doctype not in SPECS:
		raise GuardError(f"{doctype!r} is not a record the assistant works with.")

	text = (note or "").strip()
	if not text:
		raise GuardError("The note is empty.")
	if len(text) > MAX_NOTE:
		raise GuardError(f"The note is too long; keep it under {MAX_NOTE} characters.")

	doc = frappe.get_doc(doctype, name)
	doc.check_permission("write")

	# The timeline renders comments as HTML, and this text was composed by a model that
	# has been reading customer-supplied data. It goes in as text, not markup.
	comment = doc.add_comment("Comment", escape_html(text))

	record_action(
		action="Note",
		tool=tool,
		reference_doctype=doctype,
		reference_name=doc.name,
		changes={"note": text},
	)

	return {"noted_on": f"{doctype} {doc.name}", "comment": comment.name}


def create_follow_up(
	doctype: str, name: str, date: str, description: str, *, tool: str
) -> dict[str, Any]:
	"""Put a dated reminder in the user's own to-do list.

	It is always allocated to the person the agent is working for. Handing work to someone
	else is a different action with different consequences, and the model does not get it.
	"""
	if doctype not in SPECS:
		raise GuardError(f"{doctype!r} is not a record the assistant works with.")

	text = (description or "").strip()
	if not text:
		raise GuardError("A follow-up needs a description.")

	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")

	todo = frappe.get_doc(
		{
			"doctype": "ToDo",
			"allocated_to": frappe.session.user,
			"date": date,
			"description": escape_html(text[:MAX_NOTE]),
			"reference_type": doctype,
			"reference_name": doc.name,
		}
	).insert()

	record_action(
		action="Follow Up",
		tool=tool,
		reference_doctype=doctype,
		reference_name=doc.name,
		changes={"date": date, "description": text, "allocated_to": todo.allocated_to},
	)

	return {"follow_up": todo.name, "on": f"{doctype} {doc.name}", "date": todo.date}


def writable_doctypes() -> list[str]:
	return sorted(WRITE_SPECS)


def describe_writable(doctype: str, creating: bool) -> str:
	spec = _spec(doctype)
	return f"{doctype}: {', '.join(spec.allowed(creating))}."


# -- internals -----------------------------------------------------------------------


def _spec(doctype: str) -> WriteSpec:
	spec = WRITE_SPECS.get(doctype)
	if spec is None:
		raise GuardError(
			f"{doctype!r} cannot be changed by the assistant. "
			f"Available: {', '.join(writable_doctypes())}."
		)
	return spec


def _checked(spec: WriteSpec, values: dict[str, Any], *, creating: bool) -> dict[str, Any]:
	"""Keep the allowed fields, refuse the rest by name so the model can correct itself."""
	allowed = spec.allowed(creating)
	unknown = [field for field in values if field not in allowed]
	if unknown:
		raise GuardError(
			f"Cannot set {', '.join(sorted(unknown))} on a {spec.label}. "
			f"Allowed: {', '.join(allowed)}."
		)
	return {field: value for field, value in values.items() if value is not None}


def _fill_company(doc) -> None:
	"""Company comes from the user's defaults, never from the model.

	A model that can name its own company can create records in one it was never meant to
	see. ERPNext would still reject it at save time, but it should never get that far.
	"""
	company = frappe.defaults.get_user_default("Company")
	if company and doc.meta.has_field("company"):
		doc.company = company
