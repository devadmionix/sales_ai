# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Ceilings on what one run, and one person's day, may cost.

Two different failures need two different answers. A run that has gone wrong is stopped
by the write cap, which refuses further changes but lets the run finish and explain
itself. A person or a schedule that is simply spending too much is stopped by the token
budget, which refuses to start another run at all.

Both are off until someone sets them in `Sales AI Settings`. An unset budget means no
limit, not a limit of zero.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import today


def check_tokens(user: str | None = None) -> None:
	"""Refuse to start a run once today's token budget is spent.

	Checked before the run starts rather than during it, because stopping halfway leaves
	a half-finished job and still pays for the tokens already used.
	"""
	budget = frappe.db.get_single_value("Sales AI Settings", "daily_token_budget")
	if not budget:
		return

	user = user or frappe.session.user
	spent = _tokens_today(user)
	if spent < budget:
		return

	frappe.throw(
		_("{0} has used {1} of today's {2} token budget. It resets tomorrow.").format(
			user, spent, budget
		),
		title=_("Token Budget Reached"),
	)


def writes_left(scope: dict[str, str] | None) -> int | None:
	"""How many more records this run may change. `None` means no cap is set.

	`scope` says what to count against — `{"run": ...}` for a chat or unattended run,
	`{"playbook_run": ...}` for a playbook. Counted from the action log rather than from
	memory, so the cap still holds when a paused run is resumed in a different process
	hours later, and a write that failed does not use one up.
	"""
	cap = frappe.db.get_single_value("Sales AI Settings", "max_writes_per_run")
	if not cap:
		return None
	if not scope:
		# Nothing to attribute writes to, so there is nothing to count against.
		return cap
	return max(cap - frappe.db.count("Sales AI Action Log", scope), 0)


def _tokens_today(user: str) -> int:
	"""Everything the model was asked to do today on this person's behalf.

	Playbooks are counted alongside chat because they spend from the same provider
	account, and a schedule that fires hourly is exactly the thing this budget is for.
	"""
	runs, playbooks = frappe.db.sql(
		"""
		select
			(select coalesce(sum(total_tokens), 0) from `tabSales AI Run`
				where user = %(user)s and date(started_at) = %(today)s),
			(select coalesce(sum(total_tokens), 0) from `tabSales AI Playbook Run`
				where user = %(user)s and date(started_at) = %(today)s)
		""",
		{"user": user, "today": today()},
	)[0]
	return int(runs) + int(playbooks)
