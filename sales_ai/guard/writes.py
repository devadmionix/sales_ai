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
from frappe.desk.form import assign_to
from frappe.utils import escape_html

from sales_ai.guard import NO_SUCH_RECORD, GuardError, deny, read_document
from sales_ai.guard.specs import SPECS, WRITE_SPECS, WriteSpec
from sales_ai.sales_ai.doctype.sales_ai_action_log.sales_ai_action_log import record_action

MAX_NOTE = 2000


def create_record(doctype: str, values: dict[str, Any], *, tool: str) -> dict[str, Any]:
	spec = _spec(doctype)
	clean = _checked(spec, values, creating=True)

	missing = [f for f in spec.required if not clean.get(f)]
	if missing:
		raise GuardError(f"A new {spec.label} needs {', '.join(missing)}.")

	_not_a_duplicate(doctype, spec, clean)
	if not frappe.has_permission(doctype, "create"):
		# No record is named, so nothing is given away by being specific about this one.
		raise deny("Create", doctype, None, f"You cannot create a {spec.label}.")

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
	# Access first, arguments second. Reversed, a user who may not touch the record is
	# still told which fields it has and which of them are writable, and the refusal they
	# eventually get is about their arguments rather than about their not being allowed.
	doc = _may_change(doctype, name, "write", "Update")

	clean = _checked(spec, values, creating=False)
	if not clean:
		raise GuardError("No fields to change were given.")

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

	doc = _may_change(doctype, name, "write", "Note")

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

	doc = _may_change(doctype, name, "read", "Follow Up")

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


def assign_lead(name: str, to_user: str, *, note: str | None = None, tool: str) -> dict[str, Any]:
	"""Put a lead on somebody's list, alongside whoever is already on it.

	Adding rather than replacing, and deliberately. "Give this lead to Priya" usually means
	Priya should pick it up, not that Raj should find out later that it was taken off him.
	Taking work away from somebody is a different sentence, and the model does not get it.
	"""
	doc = _may_change("Lead", name, "write", "Assign")
	assignee = _assignable(to_user, "Lead", name)

	if frappe.db.exists(
		"ToDo",
		{
			"reference_type": "Lead",
			"reference_name": doc.name,
			"allocated_to": assignee,
			"status": "Open",
		},
	):
		return {"unchanged": "Lead", "name": doc.name, "note": f"{assignee} already has this lead."}

	assign_to.add(
		{
			"doctype": "Lead",
			"name": doc.name,
			"assign_to": [assignee],
			# The text lands on a ToDo that renders as HTML, and it was written by a model
			# that has been reading whatever the lead's own notes say.
			"description": escape_html(note.strip()[:MAX_NOTE]) if note and note.strip() else None,
		}
	)

	record_action(
		action="Assign",
		tool=tool,
		reference_doctype="Lead",
		reference_name=doc.name,
		changes={"assigned_to": assignee, "note": note},
	)

	return {
		"assigned": "Lead",
		"name": doc.name,
		"to": assignee,
		"note": "Added to their list. Anyone it was already assigned to still has it.",
	}


def writable_doctypes() -> list[str]:
	return sorted(WRITE_SPECS)


def describe_writable(doctype: str, creating: bool) -> str:
	"""One line per DocType for the tool description, `*` marking a field that must be
	supplied. Without the mark every field reads as equally expected, and a model that
	cannot tell asks the user for all of them."""
	spec = _spec(doctype)
	fields = [
		f"{field}*" if creating and field in spec.required else field
		for field in spec.allowed(creating)
	]
	return f"{doctype}: {', '.join(fields)}."


# -- internals -----------------------------------------------------------------------


def _may_change(doctype: str, name: str, ptype: str, action: str):
	"""Fetch a record the user is allowed to act on, or refuse without saying which.

	There are two refusals here and the difference between them is the point. A record the
	user cannot even read is answered exactly as a record that does not exist, because
	telling those apart is how somebody finds out what exists. A record they can read but
	not change is told so plainly — they can already see it, so there is nothing left to
	give away, and the specific answer is the one that stops them asking again.

	`get_doc` and `check_permission` still run afterwards. This decides what the user is
	told; ERPNext decides what actually happens.
	"""
	if not frappe.db.exists(doctype, name) or not frappe.has_permission(doctype, "read", doc=name):
		raise deny(action, doctype, name, NO_SUCH_RECORD.format(doctype=doctype, name=name))

	if not frappe.has_permission(doctype, ptype, doc=name):
		raise deny(action, doctype, name, f"You cannot change the {doctype} {name!r}.")

	doc = frappe.get_doc(doctype, name)
	doc.check_permission(ptype)
	return doc


def _assignable(to_user: str, doctype: str, name: str) -> str:
	"""Somebody who can be given this record, or a refusal.

	The second check is the one that matters. `assign_to.add` shares a document with an
	assignee who cannot see it (`frappe/desk/form/assign_to.py`, "if assignee does not have
	permissions, share or inform"), which would make assigning a lead a way to grant read
	access to it — through a tool nobody would think to audit for that. The agent does not
	get to widen anyone's access as a side effect of tidying a queue, so it refuses and says
	what needs doing instead.

	The first check runs three ways into one answer for the usual reason: a refusal that
	distinguishes "no such account" from "disabled" from "customer login" is a way to go
	fishing for staff email addresses.
	"""
	details = frappe.db.get_value("User", to_user, ["enabled", "user_type"], as_dict=True)
	if not details or not details.enabled or details.user_type != "System User":
		raise deny(
			"Assign", doctype, name, f"There is nobody called {to_user!r} you can give work to."
		)

	if not frappe.has_permission(doctype, "read", doc=name, user=to_user):
		raise deny(
			"Assign",
			doctype,
			name,
			f"{to_user!r} cannot see this {doctype}, and assigning it would give them "
			f"access they do not have. Someone will need to grant that first.",
		)

	return to_user


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


def _not_a_duplicate(doctype: str, spec: WriteSpec, values: dict[str, Any]) -> None:
	"""Refuse to create a second one of something that already exists.

	The model is told to search first, but an instruction in a tool description is a hope,
	not a check — and afterwards a second "ABC Medical Store" is indistinguishable from the
	first, with nothing in ERPNext that would have objected. Naming the record it should
	have found lets it use that one instead of asking the user to sort out the mess later.

	The lookup deliberately ignores permissions, because a duplicate the user cannot see is
	still a duplicate. What it does not do is hand back a name the user has no business
	knowing: that half of the answer is gated, for the same reason `_may_read` is.
	"""
	if not spec.identity:
		return
	value = values.get(spec.identity)
	if not value:
		return

	existing = frappe.db.get_value(doctype, {spec.identity: value}, "name")
	if not existing:
		return
	if not frappe.has_permission(doctype, "read", doc=existing):
		raise GuardError(
			f"A {spec.label} with {spec.identity} {value!r} already exists, "
			f"but is not one you have access to."
		)
	raise GuardError(
		f"{doctype} {existing!r} already has {spec.identity} {value!r}. "
		f"Work with that one, or create this with a different {spec.identity}."
	)


def _fill_company(doc) -> None:
	"""Company comes from the user's defaults, never from the model.

	A model that can name its own company can create records in one it was never meant to
	see. ERPNext would still reject it at save time, but it should never get that far.
	"""
	company = frappe.defaults.get_user_default("Company")
	if company and doc.meta.has_field("company"):
		doc.company = company
