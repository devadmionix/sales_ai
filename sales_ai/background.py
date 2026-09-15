# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Running the agent on a worker, with nobody watching.

An unattended run differs from a chat turn in three ways, and each one is handled here
rather than in the orchestrator:

- Nobody is on the other end of a stream, so progress is published over realtime and the
  outcome is left as a notification.
- Nobody can answer an approval, so a run that pauses is parked and a to-do is raised for
  the person it runs as. The run waits; it does not proceed.
- The worker can be killed. The orchestrator checkpoints the transcript after every
  iteration, so a killed run can be answered and resumed rather than started again.

The person a run acts as is the person who approves it. That is not a shortcut: every
record it touches is written with their permissions, so they are the one accountable for
it, and asking anybody else would be asking someone who may not even be allowed to see
the record.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import add_days, today

from sales_ai import orchestrator
from sales_ai.llm.agent import ToolCallBegin, ToolFinished

# Long enough for a multi-step run, short enough that a wedged one is not held forever.
TIMEOUT = 1500

EVENT = "sales_ai:run"


def enqueue_run(
	prompt: str,
	*,
	run_as: str,
	agent_profile: str | None = None,
	trigger: str | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	job_id: str | None = None,
) -> None:
	"""Queue an unattended run. Fire and forget: the outcome arrives as a notification."""
	frappe.enqueue(
		"sales_ai.background.execute",
		queue="long",
		timeout=TIMEOUT,
		job_id=job_id,
		deduplicate=bool(job_id),
		# The document that caused this must be committed before a worker reads it.
		enqueue_after_commit=True,
		prompt=prompt,
		run_as=run_as,
		agent_profile=agent_profile,
		trigger=trigger,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
	)


def execute(
	prompt: str,
	*,
	run_as: str,
	agent_profile: str | None = None,
	trigger: str | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
) -> None:
	"""Worker entry point. Never raises: a failed job should leave a record, not a stack."""
	frappe.set_user(run_as)
	frappe.flags.sales_ai_unattended = True

	_run(
		orchestrator.start_stream(
			prompt,
			agent_profile=agent_profile,
			reference_doctype=reference_doctype,
			reference_name=reference_name,
			trigger=trigger,
			background=True,
		),
		label=trigger or prompt,
	)


def enqueue_answer(run: str, answers: dict[str, str]) -> None:
	"""Queue the continuation of a parked run, once its owner has answered.

	Authorisation happens here, as the person clicking, because on the worker the run is
	resumed as its own user and that check would pass for anybody.
	"""
	run_doc = frappe.get_doc("Sales AI Run", run)
	if run_doc.status != "Paused":
		frappe.throw(_("This run is {0}, not awaiting an answer.").format(run_doc.status))
	if run_doc.user != frappe.session.user:
		frappe.throw(_("Only the person a run acts as can answer it."), frappe.PermissionError)

	close_todos("Sales AI Run", run)
	frappe.enqueue(
		"sales_ai.background.execute_answer",
		queue="long",
		timeout=TIMEOUT,
		job_id=f"sales-ai-answer::{run}",
		deduplicate=True,
		enqueue_after_commit=True,
		run=run,
		answers=answers,
		run_as=run_doc.user,
	)


def execute_answer(run: str, answers: dict[str, str], run_as: str) -> None:
	"""Worker entry point for a parked run that has been answered."""
	frappe.set_user(run_as)
	frappe.flags.sales_ai_unattended = True
	_run(orchestrator.resume_stream(run, answers, background=True), label=run)


# -- the loop around a run -------------------------------------------------------------


def _run(stream: Any, *, label: str) -> None:
	run = None
	try:
		while True:
			try:
				event = next(stream)
			except StopIteration as finished:
				_settle(finished.value)
				frappe.db.commit()
				return
			run = _publish(event, run)
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title=f"Sales AI: unattended run failed ({label})")
		frappe.db.commit()


def _publish(event: Any, run: str | None) -> str | None:
	"""Mirror the run to anyone watching the desk. Losing a frame here is not a failure."""
	if isinstance(event, orchestrator.RunStarted):
		run = event.run
		_emit({"run": run, "session": event.session, "state": "started"})
	elif isinstance(event, ToolCallBegin):
		_emit({"run": run, "state": "tool", "tool": event.name})
	elif isinstance(event, ToolFinished):
		_emit({"run": run, "state": "tool_done", "tool": event.name, "error": event.error})
	return run


def _emit(payload: dict[str, Any]) -> None:
	frappe.publish_realtime(EVENT, payload, user=frappe.session.user)


def _settle(result: dict[str, Any]) -> None:
	"""Tell the user how it ended, and ask them if it is waiting on them."""
	run = result["run"]
	if result["status"] == "paused":
		_ask(run, result.get("question") or {})
	else:
		_notify(
			run,
			_("Sales AI finished a background task"),
			(result.get("content") or "").strip()[:500],
		)
	_emit({"run": run, "state": result["status"]})


def _ask(run: str, question: dict[str, Any]) -> None:
	ask("Sales AI Run", run, question.get("prompt") or "")


def _notify(run: str, subject: str, message: str) -> None:
	notify("Sales AI Run", run, subject, message)


# -- how a parked run reaches a person -------------------------------------------------
#
# Shared with playbook runs, which park for exactly the same reason. The channel has to
# outlive the tab that started the work, so it is a to-do and a notification rather than
# anything on screen.


def ask(doctype: str, name: str, prompt: str) -> None:
	prompt = prompt or _("An action needs your approval.")
	frappe.get_doc(
		{
			"doctype": "ToDo",
			"allocated_to": frappe.session.user,
			"reference_type": doctype,
			"reference_name": name,
			"date": add_days(today(), 1),
			"priority": "High",
			"description": _("Sales AI is waiting for your answer: {0}").format(prompt),
		}
	).insert(ignore_permissions=True)
	notify(doctype, name, _("Sales AI needs your approval"), prompt)


def notify(doctype: str, name: str, subject: str, message: str) -> None:
	frappe.get_doc(
		{
			"doctype": "Notification Log",
			"for_user": frappe.session.user,
			"type": "Alert",
			"subject": subject,
			"email_content": (message or "")[:500],
			"document_type": doctype,
			"document_name": name,
		}
	).insert(ignore_permissions=True)


def close_todos(doctype: str, name: str) -> None:
	for todo in frappe.get_all(
		"ToDo",
		filters={"reference_type": doctype, "reference_name": name, "status": "Open"},
		pluck="name",
	):
		frappe.db.set_value("ToDo", todo, "status", "Closed")
