# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Every change the agent made with nobody watching.

This is the review a manager does rather than the gate that stops anything, and it exists
because the autonomy dial is only safe if somebody looks at what it let through. Turning
`Act on Low Risk` on is a decision to stop being asked; this is where the cost of that
decision shows up, one row per change nobody approved.

It reads `Sales AI Run Step` rather than `Sales AI Action Log`, which is the more obvious
place to look. The action log records what changed, but not whether a human said yes —
that is on the step, because the step is where the approval happened. Copying the bit onto
the log as well would give two answers that could disagree, and the one written at a
distance from the decision is the one that would be wrong.

Which tools count as writes is asked of the registry rather than listed here. A tool added
next year is covered the day it is registered, and a report that has to be remembered is a
report that eventually lies.

One thing to be explicit about: this query does not apply row-level permissions. A user
reading `Sales AI Run` in the desk sees only their own, because the doctype grants `All`
read with `if_owner`. The query builder honours none of that, so whoever opens this report
sees every run on the site. That is the point of it — a review that showed a manager only
their own work would review nothing — and it is why access is restricted at the report's
roles instead. Widen those roles and you have widened this.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.utils import add_days, today

from sales_ai import tools
from sales_ai.guard import policy


def execute(filters: dict[str, Any] | None = None) -> tuple[list[dict], list[dict]]:
	filters = frappe._dict(filters or {})
	writes = _write_tools()
	if not writes:
		return _columns(), []
	return _columns(), _rows(filters, writes)


def _write_tools() -> set[str]:
	return {tool.name for tool in tools.select(tools.names()) if tool.meta.get("writes")}


def _allowed_now() -> set[str]:
	"""Write tools the policy would let run unsupervised as it is configured right now.

	Compared against what actually ran, so that an action taken under a looser rule that
	has since been tightened stands out. That is the interesting row: not that the agent
	misbehaved, but that the site's idea of acceptable has moved and this is what was done
	before it did.
	"""
	return {
		tool.name
		for tool in tools.select(tools.names())
		if tool.meta.get("writes") and policy.decide(tool).mode == policy.ALLOW
	}


def _rows(filters: frappe._dict, writes: set[str]) -> list[dict]:
	step = DocType("Sales AI Run Step")
	run = DocType("Sales AI Run")

	query = (
		frappe.qb.from_(step)
		.inner_join(run)
		.on(step.parent == run.name)
		.select(
			run.started_at.as_("started_at"),
			run.name.as_("run"),
			run.user.as_("user"),
			run.background.as_("background"),
			run.agent_profile.as_("agent_profile"),
			step.tool.as_("tool"),
			step.arguments.as_("arguments"),
		)
		.where(step.approved_by_human == 0)
		.where(step.tool.isin(list(writes)))
		# A write that threw changed nothing, so it does not belong in a list of changes.
		# It is a bug report, not a governance finding.
		.where((step.error == "") | step.error.isnull())
		.where(run.started_at >= (filters.from_date or add_days(today(), -7)))
		.orderby(run.started_at, order=frappe.qb.desc)
	)

	if filters.to_date:
		query = query.where(run.started_at <= f"{filters.to_date} 23:59:59")
	if filters.user:
		query = query.where(run.user == filters.user)
	if filters.tool:
		query = query.where(step.tool == filters.tool)

	allowed = _allowed_now()
	rows = query.run(as_dict=True)
	for row in rows:
		row["still_allowed"] = int(row["tool"] in allowed)
		row["arguments"] = frappe.as_json(frappe.parse_json(row["arguments"] or "{}"))
	return rows


def _columns() -> list[dict]:
	return [
		{
			"fieldname": "started_at",
			"label": _("When"),
			"fieldtype": "Datetime",
			"width": 160,
		},
		{
			"fieldname": "tool",
			"label": _("Tool"),
			"fieldtype": "Data",
			"width": 150,
		},
		{
			"fieldname": "user",
			"label": _("On Behalf Of"),
			"fieldtype": "Link",
			"options": "User",
			"width": 160,
		},
		{
			"fieldname": "background",
			"label": _("Unattended"),
			"fieldtype": "Check",
			"width": 90,
		},
		{
			"fieldname": "still_allowed",
			"label": _("Still Allowed"),
			"fieldtype": "Check",
			"width": 100,
		},
		{
			"fieldname": "run",
			"label": _("Run"),
			"fieldtype": "Link",
			"options": "Sales AI Run",
			"width": 160,
		},
		{
			"fieldname": "agent_profile",
			"label": _("Profile"),
			"fieldtype": "Link",
			"options": "Sales AI Agent Profile",
			"width": 140,
		},
		{
			"fieldname": "arguments",
			"label": _("Arguments"),
			"fieldtype": "Small Text",
			"width": 300,
		},
	]
