# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Who decides whether the agent may act on its own.

The agent loop asks this module about every tool call before running it. There are three
answers: run it, ask a human first, or refuse.

The default is the cautious one. A tool that writes and has no rule covering it always
asks a human — so installing the app, or adding a new write tool later, cannot quietly
grant the agent permission to act unsupervised. Relaxing that is an explicit act: someone
has to create a `Sales AI Action Policy` row saying so.

This is the last gate, not the only one. ERPNext's own permission checks still run when
the tool executes, so approving something the user is not allowed to do still fails.

A run started by a trigger or a schedule has nobody watching it. That is read from
`frappe.flags` rather than passed in, because the agent loop is deliberately ignorant of
where a run came from. Such a run is not treated as pre-approved: a rule can say what to
do instead via `autonomous_override`, and by default asking a human still means asking a
human — the run parks until they answer.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import frappe
from frappe import _
from frappe.utils import flt, fmt_money

from sales_ai import budget
from sales_ai.llm.agent import Question, Refusal
from sales_ai.llm.tool import Tool
from sales_ai.llm.types import ToolCall

ALLOW = "Allow"
REQUIRE_APPROVAL = "Require Approval"
DENY = "Deny"
THRESHOLD = "Require Approval Above Amount"

SAME_AS_MODE = "Same as Mode"


@dataclass(frozen=True)
class Rule:
	"""A matching `Sales AI Action Policy` row, or the fallback when none matched."""

	mode: str
	message: str = ""
	name: str | None = None
	# Why a threshold rule landed where it did, in words, so the person being asked knows
	# whether they are the right person to ask. Empty for every other kind of rule.
	note: str = ""


def gate(tool: Tool, call: ToolCall) -> Question | Refusal | None:
	"""The callback the agent loop and the playbook engine both use. `None` means go ahead."""
	if tool.meta.get("writes") and budget.writes_left(_write_scope()) == 0:
		# Refused rather than thrown: the agent is told, so it stops changing things but
		# can still finish the turn and say what it did and what it did not get to.
		return Refusal(
			message=_("This run has changed as many records as it is allowed to. Stop here.")
		)

	# Worked out once and used twice: to decide whether this is small enough to act on
	# alone, and to show the human the figure if it is not. One computation means the
	# number a threshold was judged against is the number on the card, always.
	facts = _preview(tool, call.arguments)
	rule = decide(tool, facts)

	if rule.mode == ALLOW:
		return None
	if rule.mode == DENY:
		return Refusal(message=rule.message or _("This action is not allowed."))
	return question(tool, call, rule, facts)


def decide(tool: Tool, facts: dict[str, Any] | None = None) -> Rule:
	"""Find the rule that governs this tool, for this user, for this particular call.

	`facts` is what the call would come to — see `_preview`. It is optional because a
	caller may be asking the general question "what governs this tool", but a rule that
	depends on the specifics and is given none falls back to asking a human.
	"""
	unattended = bool(frappe.flags.get("sales_ai_unattended"))

	for row in _policies(tool.name):
		if row.role and row.role not in frappe.get_roles():
			continue
		if row.for_company and row.for_company != (facts or {}).get("company"):
			# Including when the company is simply unknown: a rule that was written about
			# one company must not decide a call we cannot place.
			continue

		mode = row.mode
		if unattended and row.autonomous_override and row.autonomous_override != SAME_AS_MODE:
			mode = row.autonomous_override
		if mode == THRESHOLD:
			return _against_threshold(row, facts)
		return Rule(mode=mode, message=row.message or "", name=row.name)

	# Nothing matched. Reads are ordinary; anything that changes a record is not.
	return Rule(mode=REQUIRE_APPROVAL if tool.meta.get("writes") else ALLOW)


def question(tool: Tool, call: ToolCall, rule: Rule, facts: dict[str, Any] | None = None) -> Question:
	prompt = rule.message or _("Let the assistant {0}?").format(_describe(tool, call.arguments))
	if rule.note:
		prompt = f"{prompt} {rule.note}"
	return Question(
		tool_call_id=call.id,
		tool_name=tool.name,
		arguments=call.arguments,
		prompt=prompt,
		preview=facts if facts is not None else _preview(tool, call.arguments),
	)


# -- internals -----------------------------------------------------------------------


def _write_scope() -> dict[str, str] | None:
	"""What the write cap counts against, read from the ambient run rather than passed in.

	A tool does not know, and should not know, whether the model asked for it or a
	playbook step did. Both are capped; they are just counted in different columns.
	"""
	if run := frappe.flags.get("sales_ai_run"):
		return {"run": run}
	if playbook_run := frappe.flags.get("sales_ai_playbook_run"):
		return {"playbook_run": playbook_run}
	return None


def _policies(tool: str) -> list[Any]:
	"""Enabled rules for one tool, most specific first.

	A rule naming a role or a company beats a general one at the same priority, so an
	exception for Sales Managers does not have to out-rank the rule it is an exception to.
	Unset narrowing sorts last under `desc`, which is what puts the general rule behind
	the specific ones.

	The company field is `for_company`, not `company`, and must stay that way. Frappe fills
	any field *named* `company` from the session's default company, so a rule an admin left
	blank meaning "every company" would silently become one company's rule — and a Deny
	written that way would stop matching everyone else's calls.
	"""
	return frappe.get_all(
		"Sales AI Action Policy",
		filters={"tool": tool, "enabled": 1},
		fields=[
			"name",
			"mode",
			"message",
			"role",
			"for_company",
			"threshold",
			"currency",
			"autonomous_override",
		],
		order_by="priority desc, for_company desc, role desc",
	)


def _against_threshold(row: Any, facts: dict[str, Any] | None) -> Rule:
	"""Small enough to act on alone, or big enough to be worth a person's attention.

	This is the one rule that can hand the agent permission to write unsupervised, so it
	only does so when it is certain: the value has to be known, and it has to be in the
	currency the limit was written in. Anything else — no facts, a preview that failed, a
	total that is missing, a different currency — asks a human. Converting currencies here
	would mean an exchange rate deciding whether something needed approval.
	"""
	ask = Rule(mode=REQUIRE_APPROVAL, message=row.message or "", name=row.name)

	amount, currency = _value_of(facts)
	if amount is None:
		return replace(ask, note=_("Its value could not be worked out, so it is being checked."))
	if currency != row.currency:
		return replace(
			ask,
			note=_("It is in {0}, and the {1} limit cannot be applied to it.").format(
				currency or _("an unknown currency"), row.currency
			),
		)

	limit = flt(row.threshold)
	if flt(amount) <= limit:
		return Rule(mode=ALLOW, name=row.name)
	return replace(
		ask,
		note=_("At {0} it is over the {1} that may go through unchecked.").format(
			fmt_money(amount, currency=currency), fmt_money(limit, currency=row.currency)
		),
	)


def _value_of(facts: dict[str, Any] | None) -> tuple[float | None, str | None]:
	"""What this call is worth, read from the same totals the approval card shows.

	`rounded_total` first, for the same reason the card leads with it: it is what would
	actually be charged. A preview that failed reports an error and no totals, which
	correctly yields no value rather than a zero that would slip under every limit.
	"""
	if not facts:
		return None, None
	totals = facts.get("totals") or {}
	for key in ("rounded_total", "grand_total", "total"):
		if totals.get(key) is not None:
			return flt(totals[key]), facts.get("currency")
	return None, facts.get("currency")


def _preview(tool: Tool, arguments: dict[str, Any]) -> dict[str, Any] | None:
	"""Work out what the call would come to, so the human approves a figure and not a guess.

	A tool opts in with ``preview=`` in its meta: a callable taking the model's arguments
	and returning something the card can render. It must be a read — it runs *before*
	anyone has approved anything, so a preview with a side effect would defeat the gate.

	The arguments have not been validated yet, so a preview builder is handed raw model
	output and is expected to refuse it the same way the tool would. A failure is reported
	rather than swallowed: a card that silently shows no prices reads like "there are no
	prices", which is worse than saying the sum could not be worked out.
	"""
	builder = tool.meta.get("preview")
	if not builder:
		return None

	try:
		return builder(arguments)
	except Exception:
		frappe.log_error(
			title=f"Sales AI: preview failed for {tool.name}", message=frappe.get_traceback()
		)
		return {
			"error": _("This could not be priced up first. Check the details before approving.")
		}


def _describe(tool: Tool, arguments: dict[str, Any]) -> str:
	"""A one-line summary for the approval prompt, in the user's words rather than JSON.

	A tool can phrase its own action via ``action="update {doctype} {name}"`` in its meta.
	The template is written by us; only the values come from the model, and they are
	substituted, never interpreted.
	"""
	template = tool.meta.get("action")
	if template:
		try:
			return template.format(**arguments)
		except (KeyError, IndexError):
			pass

	doctype = arguments.get("doctype")
	name = arguments.get("name")
	action = tool.name.replace("_", " ")
	if doctype and name:
		return f"{action} on {doctype} {name}"
	if doctype:
		return f"{action} ({doctype})"
	return action
