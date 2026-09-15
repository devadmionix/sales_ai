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

MAX_LIMIT = 50
DEFAULT_LIMIT = 20
# Long free text is where a prompt injection would hide, and where a context window goes
# to die. Neither is worth the tokens.
MAX_TEXT = 1200

OPERATORS = ("=", "!=", ">", "<", ">=", "<=", "in", "not in", "like", "not like", "between", "is")

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


class GuardError(ValueError):
	"""A request the model is not allowed to make. The message is written for the model."""


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
	"""A one-paragraph summary of a DocType for the tool description."""
	spec = _spec(doctype)
	return (
		f"{doctype}: {spec.purpose} "
		f"Filter or sort on: {', '.join(spec.filter_fields)}."
	)


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

	Both halves matter. Without the first, the failure is ERPNext's and says something the
	model cannot act on. Without the second, naming a record and watching the answer change
	is a way to confirm that record exists — so a refusal has to come before the lookup, not
	after it.
	"""
	if not frappe.db.exists(doctype, name):
		raise GuardError(f"There is no {doctype} called {name!r}.")
	if not (
		frappe.has_permission(doctype, "read", doc=name)
		or frappe.has_permission(doctype, "select", doc=name)
	):
		raise GuardError(f"You do not have access to the {doctype} {name!r}.")
	return name


def _plain_text(value: str) -> str:
	text = " ".join(strip_html_tags(value).split())
	if len(text) > MAX_TEXT:
		text = text[:MAX_TEXT] + " …[truncated]"
	return text
