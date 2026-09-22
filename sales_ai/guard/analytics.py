# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Counting, summing and grouping — the questions a list of rows cannot answer.

A search returns records and is capped at fifty of them, which is right for "show me the
leads from Delhi" and useless for "what is the pipeline worth". This module answers the
second kind: a number, optionally broken down by something, optionally over time.

The model does not write an aggregate. It picks a measure and a grouping from the closed
set declared in `specs.AGGREGATES`, exactly as it picks a filter field today. Everything
still goes through `frappe.get_list`, so a salesperson restricted to one territory gets a
pipeline total for that territory and nobody else's — the numbers are scoped by the same
User Permissions as the desk.
"""

from __future__ import annotations

from typing import Any, Literal

import frappe

from sales_ai.guard import MAX_LIMIT, Filter, GuardError, _condition
from sales_ai.guard.permissions import check_ai_permission
from sales_ai.guard.specs import SPECS, AggregateSpec

DEFAULT_GROUPS = 20

# A period a sales question is actually asked in. There is no WEEK to group by — Frappe's
# query builder exposes YEAR, QUARTER and MONTH and nothing finer — and a day bucket is
# only honest for a field that stores a date rather than a timestamp.
Bucket = Literal["day", "month", "quarter", "year"]
BUCKETS = ("day", "month", "quarter", "year")


def aggregate(
	doctype: str,
	*,
	measures: list[str],
	group_by: list[str] | None = None,
	date_field: str | None = None,
	date_bucket: str | None = None,
	date_from: str | None = None,
	date_to: str | None = None,
	filters: list[Filter] | None = None,
	order_by: str | None = None,
	limit: int = DEFAULT_GROUPS,
) -> dict[str, Any]:
	spec = _spec(doctype)
	base = spec.parent or doctype

	# RBAC pre-check: does the chatbot's role matrix allow reading this DocType?
	result = check_ai_permission(doctype=base, action="read")
	if not result.allowed:
		raise GuardError(result.reason)

	frappe.has_permission(base, "read", throw=True)

	selected = _measures(spec, doctype, measures)
	groups = _groups(spec, doctype, group_by)
	period, period_fields, period_keys = _period(spec, doctype, date_field, date_bucket)

	conditions = _filters(spec, doctype, filters)
	conditions += _date_range(spec, doctype, date_field, date_from, date_to)

	rows = frappe.get_list(
		doctype,
		parent_doctype=spec.parent,
		fields=[*period_fields, *groups, *selected.values()],
		filters=conditions,
		group_by=", ".join([*period_keys, *groups]) or None,
		order_by=_order_by(selected, groups, period_keys, order_by),
		limit_page_length=max(1, min(int(limit or DEFAULT_GROUPS), MAX_LIMIT)),
	)

	return {
		"doctype": doctype,
		"measures": {key: spec.measures[key].label or key for key in selected},
		"group_by": groups,
		"period": period,
		"count": len(rows),
		"rows": [_row(row, period, period_keys) for row in rows],
	}


def aggregatable_doctypes() -> list[str]:
	from sales_ai.guard.specs import AGGREGATES

	return sorted(AGGREGATES)


def describe(doctype: str) -> str:
	"""One line per DocType for the tool description, in the compact notation the tool
	explains: `measures / by / dates`.

	Terse on purpose. This text is not documentation somebody reads once — it is resent on
	every model call of every iteration, so each label word spelled out in full is a cost
	paid thousands of times to say something the punctuation already says. The field names
	themselves stay complete: those the model has to get exactly right first time, and a
	wrong guess costs a whole extra round trip."""
	spec = _spec(doctype)
	return (
		f"{doctype} — {spec.purpose} "
		f"measures: {','.join(spec.measures)} / "
		f"by: {','.join(spec.group_by)} / "
		f"dates: {','.join(spec.date_fields)}"
	)


# -- internals -----------------------------------------------------------------------


def _spec(doctype: str) -> AggregateSpec:
	from sales_ai.guard.specs import AGGREGATES

	spec = AGGREGATES.get(doctype)
	if spec is None:
		raise GuardError(
			f"{doctype!r} cannot be counted or summed. "
			f"Available: {', '.join(aggregatable_doctypes())}."
		)
	return spec


def _measures(spec: AggregateSpec, doctype: str, wanted: list[str]) -> dict[str, dict[str, Any]]:
	"""Turn measure names into the dict form Frappe's query builder wants.

	`{"SUM": "base_grand_total", "as": "value"}` rather than a string: v16 refuses SQL
	functions written as text, which is how it keeps a SELECT list from becoming a place to
	smuggle a subquery.
	"""
	if not wanted:
		raise GuardError(f"Ask for at least one measure. Available on {doctype}: {', '.join(spec.measures)}.")

	selected: dict[str, dict[str, Any]] = {}
	for key in wanted:
		measure = spec.measures.get(key)
		if measure is None:
			raise GuardError(
				f"{doctype} has no measure {key!r}. Available: {', '.join(spec.measures)}."
			)
		# COUNT(*) rather than COUNT(name): the question is how many rows, and a named
		# column invites the reader to think it is counting distinct values, which it is not.
		argument = "*" if measure.op == "count" else measure.fieldname
		selected[key] = {measure.op.upper(): argument, "as": key}
	return selected


def _groups(spec: AggregateSpec, doctype: str, wanted: list[str] | None) -> list[str]:
	for fieldname in wanted or []:
		if fieldname not in spec.group_by:
			raise GuardError(
				f"Cannot group {doctype} by {fieldname!r}. Allowed: {', '.join(spec.group_by)}."
			)
	return list(wanted or [])


def _period(
	spec: AggregateSpec, doctype: str, date_field: str | None, bucket: str | None
) -> tuple[str | None, list[Any], list[str]]:
	"""The time axis, as (bucket name, extra select fields, group-by keys)."""
	if not bucket:
		return None, [], []
	if not date_field:
		raise GuardError("A date_bucket needs a date_field to bucket.")
	if bucket not in BUCKETS:
		raise GuardError(f"Unknown date_bucket {bucket!r}. Use one of: {', '.join(BUCKETS)}.")

	field = _date(spec, doctype, date_field)
	qualified = _qualified(spec, field)

	if bucket == "day":
		# Grouping a timestamp by its raw value gives one group per second, which is not a
		# day and would quietly produce nonsense. Only a Date field can answer this.
		if _fieldtype(spec.parent or doctype, field) != "Date":
			raise GuardError(
				f"{field!r} stores a time as well as a date, so it cannot be grouped by day. "
				f"Use a date_bucket of 'month', 'quarter' or 'year'."
			)
		return "day", [qualified + " as period"], ["period"]

	parts: list[Any] = [{"YEAR": qualified, "as": "period_year"}]
	keys = ["period_year"]
	if bucket == "quarter":
		parts.append({"QUARTER": qualified, "as": "period_quarter"})
		keys.append("period_quarter")
	elif bucket == "month":
		parts.append({"MONTH": qualified, "as": "period_month"})
		keys.append("period_month")
	return bucket, parts, keys


def _filters(spec: AggregateSpec, doctype: str, filters: list[Filter] | None) -> list[list[Any]]:
	"""Conditions, resolved against the child or its parent depending on the fieldname.

	On a child table there are two sets of fields in play: the line's own (item, warehouse)
	and the document's (customer, date, company). A filter naming a grouping field belongs
	to the line; anything else has to be a document field, and is checked against what the
	parent may be filtered on.
	"""
	conditions = []
	for item in filters or []:
		if spec.parent and item.field in spec.group_by:
			# Validated by membership in `group_by`, an allowlist of this child's own
			# fields, so it never reaches the parent-shaped check below.
			conditions.append([doctype, item.field, item.operator, item.value])
		else:
			condition = _condition(SPECS[spec.parent or doctype], item)
			conditions.append([spec.parent, *condition] if spec.parent else condition)
	return conditions


def _date_range(
	spec: AggregateSpec, doctype: str, date_field: str | None, start: str | None, end: str | None
) -> list[list[Any]]:
	if not (start or end):
		return []
	if not date_field:
		raise GuardError("A date range needs a date_field to apply it to.")

	field = _date(spec, doctype, date_field)
	base = spec.parent or ""
	conditions = []
	if start:
		conditions.append([base, field, ">=", start] if base else [field, ">=", start])
	if end:
		conditions.append([base, field, "<=", end] if base else [field, "<=", end])
	return conditions


def _date(spec: AggregateSpec, doctype: str, date_field: str) -> str:
	if date_field not in spec.date_fields:
		raise GuardError(
			f"{doctype} cannot be measured over {date_field!r}. "
			f"Allowed: {', '.join(spec.date_fields)}."
		)
	return date_field


def _qualified(spec: AggregateSpec, fieldname: str) -> str:
	"""On a child table the date belongs to the document, not to the line.

	"Sold last quarter" is a fact about when the order was placed. The line has its own
	`creation`, which is the same instant for every line on the order and means nothing on
	its own, so the parent's column is always the one addressed.
	"""
	return f"`tab{spec.parent}`.`{fieldname}`" if spec.parent else fieldname


def _fieldtype(doctype: str, fieldname: str) -> str:
	if fieldname in ("creation", "modified"):
		return "Datetime"
	return frappe.get_meta(doctype).get_field(fieldname).fieldtype


def _order_by(
	measures: dict[str, Any], groups: list[str], period_keys: list[str], order_by: str | None
) -> str:
	"""Sort by a measure or by a grouping, and never by a column that is not in the query.

	The default matters more than it looks: a time series reads forwards, and a breakdown
	reads biggest-first. Getting this wrong makes a correct answer look wrong.
	"""
	if order_by:
		parts = order_by.strip().split()
		fieldname = parts[0]
		direction = parts[1].lower() if len(parts) > 1 else "desc"
		if len(parts) > 2 or direction not in ("asc", "desc"):
			raise GuardError("Sort must be '<measure or group> asc' or '<measure or group> desc'.")
		if fieldname == "period":
			return ", ".join(f"{key} {direction}" for key in period_keys) or f"period {direction}"
		if fieldname not in measures and fieldname not in groups:
			allowed = ", ".join([*measures, *groups, *(["period"] if period_keys else [])])
			raise GuardError(f"Cannot sort on {fieldname!r}. Allowed: {allowed}.")
		return f"{fieldname} {direction}"

	if period_keys:
		return ", ".join(f"{key} asc" for key in period_keys)
	if measures:
		return f"{next(iter(measures))} desc"
	return ""


def _row(row: dict[str, Any], period: str | None, period_keys: list[str]) -> dict[str, Any]:
	"""Fold the year/month columns back into one label a person would recognise."""
	if not period:
		return dict(row)

	shaped = {key: value for key, value in row.items() if key not in period_keys}
	if period == "day":
		# A date object survives the trip to the model as whatever repr it happens to have.
		# Sending the ISO string means the answer never contains "datetime.date(2026, 1, 3)".
		return {**row, "period": str(row.get("period") or "")}

	year = row.get("period_year")
	if period == "year":
		label = str(year)
	elif period == "quarter":
		label = f"{year}-Q{row.get('period_quarter')}"
	else:
		label = f"{year}-{int(row.get('period_month') or 0):02d}"
	return {"period": label, **shaped}
