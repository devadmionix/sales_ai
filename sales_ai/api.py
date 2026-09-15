# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""HTTP surface for the chat panel.

Replies stream as Server-Sent Events. The generator body is consumed after the request
handler has returned, which is after Frappe has already committed the request's
transaction — so this module commits explicitly at the end of a stream. The only writes a
read-only agent can reach are its own session and audit records, which is exactly what we
want to survive a failure.
"""

from __future__ import annotations

import json
from collections.abc import Generator, Iterator
from typing import Any

import frappe
from frappe import _
from werkzeug.wrappers import Response

from sales_ai import background, orchestrator
from sales_ai.llm.agent import ToolFinished
from sales_ai.llm.types import ToolCallBegin
from sales_ai.orchestrator import RunStarted
from sales_ai.playbook import engine


@frappe.whitelist(methods=["POST"])
def chat(
	prompt: str,
	session: str | None = None,
	agent: str | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
) -> Response:
	"""Send a message. Streams the reply."""
	if not (prompt or "").strip():
		frappe.throw(_("Message cannot be empty."))

	return _sse(
		orchestrator.start_stream(
			prompt,
			session=session,
			agent_profile=agent,
			reference_doctype=reference_doctype,
			reference_name=reference_name,
		)
	)


@frappe.whitelist(methods=["POST"])
def answer(run: str, answers: str | dict[str, str]) -> Response:
	"""Answer a paused run's approval question. Streams the continuation."""
	if isinstance(answers, str):
		answers = json.loads(answers)
	if not isinstance(answers, dict):
		frappe.throw(_("Answers must be a mapping of tool call id to reply."))

	if frappe.db.get_value("Sales AI Run", run, "background"):
		# Nobody was watching when this started and it may take minutes, so the answer goes
		# back to a worker. Queued here rather than in the stream so a refusal to answer
		# somebody else's run comes back as an ordinary error.
		background.enqueue_answer(run, answers)
		return _sse(_queued(run))

	return _sse(orchestrator.resume_stream(run, answers))


# -- playbooks -------------------------------------------------------------------------
#
# Playbooks do not stream. A run can wait three days for a quotation to age, so the answer
# to "did it work" is the Playbook Run record, not an open connection.


@frappe.whitelist(methods=["POST"])
def run_playbook(
	playbook: str, reference_doctype: str | None = None, reference_name: str | None = None
) -> dict[str, str]:
	"""Start a playbook by hand. It runs as the person who asked for it."""
	if not frappe.db.get_single_value("Sales AI Settings", "enabled"):
		frappe.throw(_("Sales AI is turned off in Sales AI Settings."), title=_("Disabled"))

	doc = frappe.get_doc("Sales AI Playbook", playbook)
	doc.check_permission("read")
	if not doc.enabled:
		frappe.throw(_("Playbook {0} is disabled.").format(playbook))
	if reference_doctype and reference_name:
		frappe.has_permission(reference_doctype, doc=reference_name, throw=True)

	engine.start(
		playbook,
		run_as=frappe.session.user,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
	)
	return {"status": "queued"}


@frappe.whitelist()
def dry_run_playbook(
	playbook: str, reference_doctype: str | None = None, reference_name: str | None = None
) -> list[dict[str, Any]]:
	"""Resolve every step without running anything. Nothing is written and no tokens are spent."""
	frappe.get_doc("Sales AI Playbook", playbook).check_permission("write")
	if reference_doctype and reference_name:
		frappe.has_permission(reference_doctype, doc=reference_name, throw=True)

	return engine.dry_run(
		playbook, reference_doctype=reference_doctype, reference_name=reference_name
	)


@frappe.whitelist(methods=["POST"])
def answer_playbook(playbook_run: str, reply: str) -> dict[str, str]:
	"""Answer a playbook run that is parked on an approval."""
	engine.answer(playbook_run, reply)
	return {"status": "queued"}


@frappe.whitelist()
def playbook_graph(playbook: str | None = None, playbook_run: str | None = None) -> dict[str, Any]:
	"""The steps and the edges between them, for drawing.

	The edges come from `plan()` rather than being worked out again in the browser, so the
	picture is of what will actually run. For a run, the trace comes with it, which is what
	lets the drawing highlight the path that was taken.
	"""
	trace: list[dict[str, Any]] = []
	cursor = None

	if playbook_run:
		run = frappe.get_doc("Sales AI Playbook Run", playbook_run)
		run.check_permission("read")
		playbook = run.playbook
		cursor = run.cursor
		trace = [row.as_dict(no_default_fields=True) for row in run.trace]

	doc = frappe.get_doc("Sales AI Playbook", playbook)
	if not playbook_run:
		# Reading the run is enough. Playbooks are a System Manager's to edit, but somebody
		# watching a run that acts as them has to be able to see its shape.
		doc.check_permission("read")

	nodes = [
		{
			"key": step["step_key"],
			"type": step["step_type"],
			"label": step.get("label") or step["step_key"],
			"next": step.get("next"),
			"on_false": step.get("on_false"),
			"after_loop": step.get("after_loop"),
		}
		for step in doc.plan()
	]

	return {"playbook": doc.name, "nodes": nodes, "trace": trace, "cursor": cursor}


@frappe.whitelist()
def history(session: str) -> dict[str, Any]:
	"""The visible conversation. Tool chatter is summarised, not replayed in full."""
	doc = frappe.get_doc("Sales AI Session", session)
	doc.check_permission("read")

	messages = []
	for message in doc.get_transcript():
		role = message.get("role")
		if role == "user":
			messages.append({"role": "user", "content": message.get("content")})
		elif role == "assistant":
			messages.append(
				{
					"role": "assistant",
					"content": message.get("content"),
					"tools": [c["function"]["name"] for c in message.get("tool_calls") or []],
				}
			)

	return {
		"session": doc.name,
		"title": doc.title,
		"status": doc.status,
		"reference_doctype": doc.reference_doctype,
		"reference_name": doc.reference_name,
		"messages": messages,
	}


@frappe.whitelist()
def sessions(limit: int = 20) -> list[dict[str, Any]]:
	"""Recent conversations belonging to the current user."""
	return frappe.get_list(
		"Sales AI Session",
		filters={"user": frappe.session.user, "status": "Active"},
		fields=["name", "title", "reference_doctype", "reference_name", "modified"],
		order_by="modified desc",
		limit_page_length=min(int(limit or 20), 50),
	)


# -- streaming -----------------------------------------------------------------------

# Raised deliberately by the orchestrator and the guard, so the message is meant to be read.
_EXPECTED = (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError)


def _queued(run: str) -> Generator[Any, None, dict[str, Any]]:
	"""A stream with nothing in it, for work that has been handed to a worker."""
	yield from ()
	return {
		"run": run,
		"status": "queued",
		"content": _("Thanks — that is running in the background. I will let you know."),
	}


def _sse(stream: Generator[Any, None, dict[str, Any]]) -> Response:
	return Response(
		_frames(stream),
		mimetype="text/event-stream",
		headers={
			"Cache-Control": "no-cache",
			"Connection": "keep-alive",
			# Stops nginx buffering the whole reply and delivering it in one lump.
			"X-Accel-Buffering": "no",
		},
	)


def _frames(stream: Generator[Any, None, dict[str, Any]]) -> Iterator[str]:
	try:
		while True:
			try:
				event = next(stream)
			except StopIteration as finished:
				frappe.db.commit()
				yield _frame("done", finished.value)
				return
			yield _frame(*_translate(event))
	except _EXPECTED as e:
		# Something the user can act on: app disabled, no access, missing record.
		frappe.db.commit()
		yield _frame("error", {"message": str(e)})
	except Exception:
		# Keep the failed run on record, then tell the user without leaking internals.
		frappe.db.commit()
		frappe.log_error(title="Sales AI: run failed", message=frappe.get_traceback())
		yield _frame("error", {"message": _("The assistant could not finish. The error has been logged.")})


def _translate(event: Any) -> tuple[str, dict[str, Any]]:
	if isinstance(event, str):
		return "text", {"value": event}
	if isinstance(event, ToolCallBegin):
		return "tool_start", {"id": event.id, "name": event.name}
	if isinstance(event, ToolFinished):
		return "tool_end", {"id": event.id, "name": event.name, "error": event.error}
	if isinstance(event, RunStarted):
		return "start", {"run": event.run, "session": event.session}
	return "unknown", {}


def _frame(event: str, data: dict[str, Any]) -> str:
	return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
