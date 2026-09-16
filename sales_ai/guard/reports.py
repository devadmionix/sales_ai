# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Running ERPNext's own reports, with the agent holding only the filters.

The agent does not get to name a report. It picks one from `specs.REPORTS`, and for each
filter it supplies a value that is checked to be the shape the report's own form would have
produced: a date is parsed as a date, a choice is one of the listed choices, and a link is
a record that exists and that this user is allowed to select.

Frappe's own `query_report.run` then does the rest, including refusing the whole thing if
the user may not run this report or may not report on its DocType. Nothing here reaches
around that check — this module's job is to make sure the arguments are sane before it.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from frappe.utils import cint, cstr, getdate

from sales_ai.guard import GuardError, _may_read, _plain_text
from sales_ai.guard.specs import REPORTS, ReportFilter, ReportSpec

# A report is written for a screen somebody scrolls. The model gets the top of it and is
# told how much it did not get, so it can say "the first 50 of 300" rather than inventing a
# total from a truncated list.
MAX_ROWS = 50


def run_report(name: str, filters: dict[str, Any] | None = None) -> dict[str, Any]:
	spec = _spec(name)
	checked = _filters(spec, name, filters or {})

	from frappe.desk.query_report import run

	# `ignore_prepared_report` because a prepared report is queued and returns nothing to
	# look at; the agent is mid-conversation and needs the rows now.
	result = run(name, filters=checked, ignore_prepared_report=True, are_default_filters=False)

	columns = [
		{"fieldname": column.get("fieldname"), "label": column.get("label")}
		for column in result.get("columns") or []
		if column.get("fieldname")
	]
	rows = result.get("result") or []
	shown = [_row(row, columns) for row in rows[:MAX_ROWS]]

	return {
		"report": name,
		"filters": checked,
		"columns": columns,
		"total_rows": len(rows),
		"rows": shown,
		"truncated": len(rows) > len(shown),
	}


def report_names() -> list[str]:
	return sorted(REPORTS)


def describe(name: str) -> str:
	"""One line per report for the tool description, in the compact notation the tool
	explains: `*` required, `=` one of a fixed set, `:` a link to a DocType.

	Spelling each filter's shape out in English — "(a Company)", "('Monthly' or
	'Quarterly')" — cost more than every report name and purpose put together, and it is
	resent on every model call of every iteration. The filter names and the choices are
	kept whole, because those have to be right the first time; only the scaffolding around
	them is dropped."""
	spec = _spec(name)
	return (
		f"{name} — {spec.purpose} "
		f"filters: {', '.join(_shape(key, item) for key, item in spec.filters.items())}"
	)


# -- internals -----------------------------------------------------------------------


def _spec(name: str) -> ReportSpec:
	spec = REPORTS.get(name)
	if spec is None:
		raise GuardError(f"{name!r} is not a report this agent can run. Available: {', '.join(report_names())}.")
	return spec


def _shape(key: str, item: ReportFilter) -> str:
	name = f"{key}*" if item.required else key
	if item.kind == "choice":
		return f"{name}={'|'.join(item.options)}"
	if item.kind == "link":
		return f"{name}:{item.options[0]}"
	return name


def _filters(spec: ReportSpec, name: str, given: dict[str, Any]) -> dict[str, Any]:
	for key in given:
		if key not in spec.filters:
			raise GuardError(
				f"{name} has no filter {key!r}. Available: {', '.join(spec.filters)}."
			)

	checked: dict[str, Any] = {}
	for key, item in spec.filters.items():
		value = given.get(key)
		if value is None or value == "":
			if item.required:
				raise GuardError(f"{name} needs a value for {key!r} ({_shape(item)}).")
			if item.default is None:
				continue
			value = item.default
		checked[key] = _value(name, key, item, value)
	return checked


def _value(report: str, key: str, item: ReportFilter, value: Any) -> Any:
	if isinstance(value, list | dict):
		raise GuardError(f"{report}'s {key!r} takes a single value, not a list.")

	if item.kind == "date":
		try:
			return str(getdate(value))
		except Exception:
			raise GuardError(f"{report}'s {key!r} must be a date as YYYY-MM-DD, not {value!r}.")

	if item.kind == "number":
		try:
			return cint(value)
		except Exception:
			raise GuardError(f"{report}'s {key!r} must be a whole number, not {value!r}.")

	if item.kind == "flag":
		return 1 if value in (True, 1, "1", "true", "True", "yes") else 0

	if item.kind == "choice":
		if cstr(value) not in item.options:
			raise GuardError(
				f"{report}'s {key!r} must be one of: {', '.join(item.options)}. Got {value!r}."
			)
		return cstr(value)

	# A link. The name has to be a record that exists, and one this user could have picked
	# from the form's own dropdown — otherwise a filter would be a way to confirm that a
	# record exists by watching the report change.
	return _may_read(item.options[0], cstr(value))


def _row(row: Any, columns: list[dict[str, str]]) -> dict[str, Any]:
	"""Reports answer with dicts or with bare lists, depending on how they were written."""
	if isinstance(row, dict):
		values = {column["fieldname"]: row.get(column["fieldname"]) for column in columns}
	else:
		values = dict(zip((column["fieldname"] for column in columns), row, strict=False))
	return {key: _cell(value) for key, value in values.items() if value not in (None, "")}


def _cell(value: Any) -> Any:
	"""A report cell is written for a screen, so it may be a date object or a chunk of HTML.

	Both have to be flattened. A date rendered by `repr` reaches the model as
	`datetime.date(2026, 8, 29)`, and a cell carrying markup is a place for an instruction
	to hide — the same reason free text is stripped everywhere else in the guard.
	"""
	if isinstance(value, datetime | date):
		return str(value)
	if isinstance(value, str) and "<" in value:
		return _plain_text(value)
	return value
