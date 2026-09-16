# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Reading the scores the intelligence layer wrote, on behalf of whoever is asking.

Two permission checks happen here, not one. Role permission on `Sales AI Insight` decides
whether this user reads scores at all. Then every row is checked against its *subject*,
because a churn score is a statement about a customer — its summary quotes that customer's
ordering history — and a user with no access to the customer has no business reading it
second-hand through a score. Checking only the insight doctype would turn this layer into a
way around every User Permission on the site.

The factors come back with the score, always. A number the model can cite but not explain
is a number it will paraphrase into a claim nobody can check.
"""

from __future__ import annotations

from typing import Any

import frappe

from sales_ai.guard import GuardError

MAX_LIMIT = 50
DEFAULT_LIMIT = 20

DOCTYPE = "Sales AI Insight"
FACTOR_DOCTYPE = "Sales AI Insight Factor"


def read_insights(
	kind: str,
	*,
	subject_doctype: str,
	subject_name: str | None = None,
	band: str | None = None,
	limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
	frappe.has_permission(DOCTYPE, "read", throw=True)

	filters: dict[str, Any] = {"kind": kind, "subject_doctype": subject_doctype}
	if subject_name:
		filters["subject_name"] = subject_name
	if band:
		if band not in ("Low", "Medium", "High"):
			raise GuardError(f"{band!r} is not a band. Use Low, Medium or High.")
		filters["band"] = band

	rows = frappe.get_list(
		DOCTYPE,
		fields=["name", "subject_doctype", "subject_name", "value", "band", "summary", "model_version", "computed_on"],
		filters=filters,
		order_by="value desc",
		limit=max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT)),
	)
	rows = [row for row in rows if _may_see_subject(row)]

	factors = _factors([row["name"] for row in rows])
	return {
		"kind": kind,
		"count": len(rows),
		"insights": [_shape(row, factors.get(row["name"], [])) for row in rows],
	}


def _may_see_subject(row: dict[str, Any]) -> bool:
	return bool(
		frappe.has_permission(row["subject_doctype"], "read", doc=row["subject_name"])
	)


def _factors(names: list[str]) -> dict[str, list[dict[str, Any]]]:
	if not names:
		return {}

	rows = frappe.get_all(
		FACTOR_DOCTYPE,
		filters={"parent": ["in", names], "parenttype": DOCTYPE},
		fields=["parent", "label", "contribution", "detail"],
		order_by="parent asc, idx asc",
		parent_doctype=DOCTYPE,
	)

	grouped: dict[str, list[dict[str, Any]]] = {}
	for row in rows:
		grouped.setdefault(row["parent"], []).append(
			{"factor": row["label"], "points": row["contribution"], "detail": row["detail"]}
		)
	return grouped


def _shape(row: dict[str, Any], factors: list[dict[str, Any]]) -> dict[str, Any]:
	return {
		"subject": row["subject_name"],
		"value": row["value"],
		"band": row["band"],
		"summary": row["summary"],
		"why": factors,
		"computed_on": row["computed_on"],
		"model_version": row["model_version"],
	}
