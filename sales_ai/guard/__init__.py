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
"""

from __future__ import annotations

from typing import Any, Literal

import frappe
from frappe.utils import strip_html_tags
from pydantic import BaseModel, Field

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
	frappe.has_permission(doctype, "read", throw=True)

	rows = frappe.get_list(
		doctype,
		fields=list(spec.list_fields),
		filters=[_condition(spec, f) for f in filters or []],
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
	# Ask before fetching, so a name the user may not see and a name that does not exist
	# fail the same way. Left to `get_doc`, the first raises PermissionError and the second
	# DoesNotExistError, and the difference is the answer to a question they cannot ask.
	_may_read(doctype, name)
	doc = frappe.get_doc(doctype, name)
	# Checks the role permission and the User Permissions attached to this record.
	doc.check_permission("read")

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


def _plain_text(value: str) -> str:
	text = " ".join(strip_html_tags(value).split())
	if len(text) > MAX_TEXT:
		text = text[:MAX_TEXT] + " …[truncated]"
	return text
