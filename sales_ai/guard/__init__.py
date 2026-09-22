# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Everything the model asks for passes through here first.

The model never touches the database. It names a DocType from a fixed list, and this
module decides what that actually means: which fields come back, which filters are legal,
how many rows, and whether this user is allowed to see any of it at all.

Reads go through `frappe.get_list` / `check_permission` rather than SQL, so role
permissions and User Permission records (which is how ERPNext scopes company and
territory) apply exactly as they do in the desk UI. The agent can never see more than the
person talking to it can see.

Role-based access is enforced by the central permission service in
``sales_ai.guard.permissions``. Every read and write entry point in this module calls
``check_ai_permission`` first, which applies the chatbot's own RBAC matrix *before*
ERPNext's native checks run. The matrix can only narrow access, never widen it.
"""

from __future__ import annotations

from typing import Any, Literal

import frappe
from frappe.utils import strip_html_tags
from pydantic import BaseModel, Field

from sales_ai.guard.permissions import check_ai_permission, get_user_scope
from sales_ai.guard.specs import SPECS, ReadSpec
from sales_ai.llm.tool import ToolError
from sales_ai.sales_ai.doctype.sales_ai_action_log.sales_ai_action_log import record_denial

MAX_LIMIT = 50
DEFAULT_LIMIT = 20
# Long free text is where a prompt injection would hide, and where a context window goes
# to die. Neither is worth the tokens.
MAX_TEXT = 1200

OPERATORS = ("=", "!=", ">", "<", ">=", "<=", "in", "not in", "like", "not like", "between", "is")

# Said for a record that does not exist and for one this user may not see, deliberately
# without distinguishing them. "Available to you" is the honest reading of both: from where
# the user stands the two are the same, and saying which would answer a question they were
# not entitled to ask. The DocType and name are safe to echo — the model supplied them.
NO_SUCH_RECORD = "No {doctype} called {name!r} is available to you."

Operator = Literal["=", "!=", ">", "<", ">=", "<=", "in", "not in", "like", "not like", "between", "is"]
Value = str | float | bool | list[str | float] | None


class Filter(BaseModel):
	"""One condition. Only fields listed for the DocType may be used."""

	field: str = Field(description="Fieldname to filter on.")
	operator: Operator = Field(default="=", description="Comparison to apply.")
	value: Value = Field(
		default=None,
		description=(
			"Value to compare against. A list for 'in', 'not in' and 'between'; "
			"'set' or 'not set' for the 'is' operator."
		),
	)


class GuardError(ToolError):
	"""A request the model is not allowed to make. The message is written for the model.

	A `ToolError` because of that last sentence: the agent loop shows these verbatim and
	replaces every other exception with a fixed phrase.
	"""


def deny(action: str, doctype: str, name: str | None, reason: str) -> GuardError:
	"""Record a refused attempt and hand back the error to raise.

	Returns rather than raises so the call site reads `raise deny(...)`, which keeps the
	refusal visible at the point it is decided instead of hiding a control-flow jump inside
	a function that looks like it only writes a log row.

	The tool is read from the ambient call rather than passed in: this is reached from read
	paths that have no reason to know which tool invoked them, and the flag is set by
	`policy.gate`, which runs before every single tool call.
	"""
	record_denial(
		action=action,
		tool=frappe.flags.get("sales_ai_tool"),
		reference_doctype=doctype,
		reference_name=name,
		reason=reason,
	)
	return GuardError(reason)


def read_list(
	doctype: str,
	*,
	query: str | None = None,
	filters: list[Filter] | None = None,
	order_by: str | None = None,
	limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
	spec = _spec(doctype)

	# RBAC pre-check: does this user's role allow reading this DocType at all?
	result = check_ai_permission(doctype=doctype, action="read")
	if not result.allowed:
		raise GuardError(result.reason)

	frappe.has_permission(doctype, "read", throw=True)

	user_filters = [_condition(spec, f) for f in filters or []]

	# Scope enforcement: when the RBAC matrix says "own" and no User Permissions
	# already restrict this DocType, only return records created by the current user.
	if _scope_blocks_list(doctype):
		user_filters.append(["owner", "=", frappe.session.user])

	rows = frappe.get_list(
		doctype,
		fields=list(spec.list_fields),
		filters=user_filters,
		or_filters=_search(spec, query),
		order_by=_order_by(spec, order_by),
		limit_page_length=max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT)),
	)

	return {
		"doctype": doctype,
		"count": len(rows),
		"records": [_clean(spec, row) for row in rows],
	}


def read_document(doctype: str, name: str) -> dict[str, Any]:
	spec = _spec(doctype)

	# RBAC pre-check: does this user's role allow reading this DocType?
	# Deliberately no document_name here — the document-level check belongs to _may_read
	# below, which conflates "no access" and "does not exist" on purpose.
	result = check_ai_permission(doctype=doctype, action="read")
	if not result.allowed:
		raise GuardError(result.reason)

	# Ask before fetching, so a name the user may not see and a name that does not exist
	# fail the same way. Left to `get_doc`, the first raises PermissionError and the second
	# DoesNotExistError, and the difference is the answer to a question they cannot ask.
	_may_read(doctype, name)
	doc = frappe.get_doc(doctype, name)
	# Checks the role permission and the User Permissions attached to this record.
	doc.check_permission("read")

	# Scope enforcement: "own" means only records the user created.
	if _scope_blocks(doctype, doc.owner):
		raise GuardError(NO_SUCH_RECORD.format(doctype=doctype, name=name))

	record = _clean(spec, {fieldname: doc.get(fieldname) for fieldname in spec.detail_fields})
	for table, fields in spec.children.items():
		rows = [_clean(spec, {f: row.get(f) for f in fields}) for row in doc.get(table) or []]
		if rows:
			record[table] = rows
	return record


def readable_doctypes() -> list[str]:
	return sorted(SPECS)


def describe(doctype: str) -> str:
	"""One line per DocType for the tool description: what it is, then what may be filtered
	or sorted on. Kept terse because it is resent on every model call of every iteration."""
	spec = _spec(doctype)
	return f"{doctype} — {spec.purpose} fields: {','.join(spec.filter_fields)}"


# -- scope enforcement ---------------------------------------------------------------


def _has_user_permissions(doctype: str) -> bool:
	"""Whether ERPNext User Permissions already restrict the current user for this DocType.

	If User Permissions exist (e.g. the user is restricted to specific Customers or
	Territories), ERPNext's ``get_list`` / ``check_permission`` already applies them.
	In that case we rely on ERPNext's restriction and don't add an ``owner`` filter —
	the User Permissions are the admin's chosen scoping mechanism.

	When no User Permissions exist, the ``owner`` filter is the only thing preventing
	Sales User A from seeing Sales User B's records.
	"""
	from frappe.permissions import get_user_permissions

	user_perms = get_user_permissions(frappe.session.user)
	# ERPNext restricts records when there's a User Permission for ANY linked DocType.
	# For example, a User Permission on Customer restricts Quotations too.
	# We check if the user has any User Permissions at all for sales-relevant DocTypes.
	if not user_perms:
		return False

	# Check if any User Permission applies to this DocType or its linked DocTypes.
	meta = frappe.get_meta(doctype)
	linked_doctypes = {doctype}
	for field in meta.get_link_fields():
		linked_doctypes.add(field.options)

	return bool(linked_doctypes & set(user_perms.keys()))


def _scope_blocks_list(doctype: str) -> bool:
	"""Whether to add an ``owner`` filter to list queries for this DocType."""
	scope = get_user_scope(doctype=doctype)
	if scope != "own":
		return False
	return not _has_user_permissions(doctype)


def _scope_blocks(doctype: str, doc_owner: str) -> bool:
	"""Whether the scope prevents the current user from accessing a specific record."""
	scope = get_user_scope(doctype=doctype)
	if scope != "own":
		return False
	if _has_user_permissions(doctype):
		# ERPNext's check_permission already enforced access.
		return False
	return doc_owner != frappe.session.user


# -- internals -----------------------------------------------------------------------


def _spec(doctype: str) -> ReadSpec:
	spec = SPECS.get(doctype)
	if spec is None:
		raise GuardError(
			f"{doctype!r} cannot be read. Available: {', '.join(readable_doctypes())}."
		)
	return spec


def _condition(spec: ReadSpec, item: Filter) -> list[Any]:
	if item.field not in spec.filter_fields:
		raise GuardError(
			f"Cannot filter on {item.field!r}. Allowed: {', '.join(spec.filter_fields)}."
		)
	if item.operator not in OPERATORS:
		raise GuardError(f"Unsupported operator {item.operator!r}.")

	value = item.value
	if item.operator in ("in", "not in", "between"):
		if not isinstance(value, list) or not value:
			raise GuardError(f"Operator {item.operator!r} needs a non-empty list of values.")
	elif item.operator == "is":
		if value not in ("set", "not set"):
			raise GuardError("Operator 'is' takes either 'set' or 'not set'.")
	elif isinstance(value, list):
		raise GuardError(f"Operator {item.operator!r} takes a single value, not a list.")

	return [item.field, item.operator, value]


def _search(spec: ReadSpec, query: str | None) -> list[list[Any]] | None:
	"""Free text becomes a LIKE across the DocType's own search fields, nothing wider."""
	text = (query or "").strip()
	if not text:
		return None
	return [[fieldname, "like", f"%{text}%"] for fieldname in spec.search_fields]


def _order_by(spec: ReadSpec, order_by: str | None) -> str:
	if not order_by:
		return "modified desc"

	parts = order_by.strip().split()
	fieldname = parts[0]
	direction = parts[1].lower() if len(parts) > 1 else "desc"

	if len(parts) > 2 or direction not in ("asc", "desc"):
		raise GuardError("Sort must be '<fieldname> asc' or '<fieldname> desc'.")
	if fieldname not in spec.filter_fields and fieldname not in spec.list_fields:
		raise GuardError(
			f"Cannot sort on {fieldname!r}. Allowed: {', '.join(spec.filter_fields)}."
		)
	return f"{fieldname} {direction}"


def _clean(spec: ReadSpec, row: dict[str, Any]) -> dict[str, Any]:
	"""Drop empties and flatten any text that could carry instructions or markup."""
	cleaned: dict[str, Any] = {}
	for key, value in row.items():
		if value is None or value == "":
			continue
		if isinstance(value, str) and (key in spec.untrusted or "<" in value):
			value = _plain_text(value)
		cleaned[key] = value
	return cleaned


def _may_read(doctype: str, name: str) -> str:
	"""A record the model named has to exist, and be one this user could have picked itself.

	Both halves are checked, and both give the same answer, because the difference between
	them is itself worth hiding. If a name the user is not allowed to see says "no access"
	while a name nobody has says "does not exist", then guessing names until the wording
	changes confirms which records are real — the customer list, roughly, one guess at a
	time. So the two are one phrase, and the check runs before the lookup rather than after.
	"""
	if not frappe.db.exists(doctype, name) or not (
		frappe.has_permission(doctype, "read", doc=name)
		or frappe.has_permission(doctype, "select", doc=name)
	):
		raise deny("Read", doctype, name, NO_SUCH_RECORD.format(doctype=doctype, name=name))
	return name


def may_change(doctype: str, name: str, ptype: str, action: str):
	"""Fetch a record the user is allowed to act on, or refuse without saying which.

	There are two refusals here and the difference between them is the point. A record the
	user cannot even read is answered exactly as a record that does not exist, because
	telling those apart is how somebody finds out what exists. A record they can read but
	not change is told so plainly — they can already see it, so there is nothing left to
	give away, and the specific answer is the one that stops them asking again.

	`get_doc` and `check_permission` still run afterwards. This decides what the user is
	told; ERPNext decides what actually happens.

	Public, and living here rather than beside the tools that use it, because every write
	path in the app has to answer this question the same way. Two copies would eventually
	disagree, and the disagreement would be the leak.
	"""
	# RBAC pre-check: does the chatbot's role matrix allow this action on this DocType?
	# No document_name — the existence/access check below intentionally hides whether the
	# record is real or just invisible, and a document-level denial from the RBAC layer
	# would leak that distinction.
	result = check_ai_permission(doctype=doctype, action=ptype)
	if not result.allowed:
		raise deny(action, doctype, name, result.reason)

	if not frappe.db.exists(doctype, name) or not frappe.has_permission(doctype, "read", doc=name):
		raise deny(action, doctype, name, NO_SUCH_RECORD.format(doctype=doctype, name=name))

	# Scope enforcement: "own" means only records the user created.
	if _scope_blocks(doctype, frappe.db.get_value(doctype, name, "owner")):
		raise deny(action, doctype, name, NO_SUCH_RECORD.format(doctype=doctype, name=name))

	if not frappe.has_permission(doctype, ptype, doc=name):
		raise deny(action, doctype, name, f"You cannot change the {doctype} {name!r}.")

	doc = frappe.get_doc(doctype, name)
	doc.check_permission(ptype)
	return doc


def _plain_text(value: str) -> str:
	text = " ".join(strip_html_tags(value).split())
	if len(text) > MAX_TEXT:
		text = text[:MAX_TEXT] + " …[truncated]"
	return text
