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

from dataclasses import dataclass
from typing import Any

import frappe
from frappe import _

from sales_ai import budget
from sales_ai.llm.agent import Question, Refusal
from sales_ai.llm.tool import Tool
from sales_ai.llm.types import ToolCall

ALLOW = "Allow"
REQUIRE_APPROVAL = "Require Approval"
DENY = "Deny"

SAME_AS_MODE = "Same as Mode"


@dataclass(frozen=True)
class Rule:
	"""A matching `Sales AI Action Policy` row, or the fallback when none matched."""

	mode: str
	message: str = ""
	name: str | None = None


def gate(tool: Tool, call: ToolCall) -> Question | Refusal | None:
	"""The callback the agent loop and the playbook engine both use. `None` means go ahead."""
	if tool.meta.get("writes") and budget.writes_left(_write_scope()) == 0:
		# Refused rather than thrown: the agent is told, so it stops changing things but
		# can still finish the turn and say what it did and what it did not get to.
		return Refusal(
			message=_("This run has changed as many records as it is allowed to. Stop here.")
		)

	rule = decide(tool)

	if rule.mode == ALLOW:
		return None
	if rule.mode == DENY:
		return Refusal(message=rule.message or _("This action is not allowed."))
	return question(tool, call, rule)


def decide(tool: Tool) -> Rule:
	"""Find the rule that governs this tool for the current user."""
	unattended = bool(frappe.flags.get("sales_ai_unattended"))

	for row in _policies(tool.name):
		if row.role and row.role not in frappe.get_roles():
			continue
		mode = row.mode
		if unattended and row.autonomous_override and row.autonomous_override != SAME_AS_MODE:
			mode = row.autonomous_override
		return Rule(mode=mode, message=row.message or "", name=row.name)

	# Nothing matched. Reads are ordinary; anything that changes a record is not.
	return Rule(mode=REQUIRE_APPROVAL if tool.meta.get("writes") else ALLOW)


def question(tool: Tool, call: ToolCall, rule: Rule) -> Question:
	prompt = rule.message or _("Let the assistant {0}?").format(_describe(tool, call.arguments))
	return Question(
		tool_call_id=call.id,
		tool_name=tool.name,
		arguments=call.arguments,
		prompt=prompt,
		preview=_preview(tool, call.arguments),
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

	A rule naming a role beats a general one at the same priority, so an exception for
	Sales Managers does not have to out-rank the rule it is an exception to.
	"""
	return frappe.get_all(
		"Sales AI Action Policy",
		filters={"tool": tool, "enabled": 1},
		fields=["name", "mode", "message", "role", "autonomous_override"],
		order_by="priority desc, role desc",
	)


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
