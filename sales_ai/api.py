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
from frappe.utils import sbool
from werkzeug.wrappers import Response

from sales_ai import background, orchestrator
from sales_ai.llm import model as llm_model
from sales_ai.llm.agent import ToolFinished
from sales_ai.llm.types import Notice, ToolCallBegin
from sales_ai.orchestrator import RunStarted
from sales_ai.playbook import engine
from sales_ai.tools import read


def guard_entry() -> None:
	"""Decide whether this login gets the assistant at all, before anything else runs.

	Every endpoint here is `@frappe.whitelist()`, which asks only that somebody is logged
	in — and a customer with a portal account is logged in. Without this they reach the
	sales assistant: its system prompt, its tool list and the site's model budget. The
	permission layer underneath would still refuse to show them another customer's records,
	so this is not the only thing standing between them and the data; it is the thing that
	stops them being handed a salesperson's assistant in the first place.

	It is checked here rather than deeper down because a refusal that arrives after a model
	has been called has already cost money, and because one gate on the way in is something
	a reviewer can actually verify. Portal users are refused rather than given a smaller
	assistant: a customer-facing assistant is a different product with a different prompt
	and a different tool list, and it has not been built.
	"""
	if frappe.session.user == "Guest":
		raise frappe.PermissionError(_("Please log in to use the assistant."))

	if frappe.db.get_value("User", frappe.session.user, "user_type") != "System User":
		raise frappe.PermissionError(_("The assistant is not available for this account."))


@frappe.whitelist(methods=["POST"])
def chat(
	prompt: str,
	session: str | None = None,
	agent: str | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	think: bool | str = False,
) -> Response:
	"""Send a message. Streams the reply."""
	guard_entry()
	if not (prompt or "").strip():
		frappe.throw(_("Message cannot be empty."))

	return _sse(
		orchestrator.start_stream(
			prompt,
			session=session,
			agent_profile=_offered(agent),
			reference_doctype=reference_doctype,
			reference_name=reference_name,
			# Costs more and takes longer, so it is asked for per message rather than left
			# on. Ignored by a model with no reasoning mode.
			think=bool(sbool(think)),
		)
	)


def _offered(agent: str | None) -> str | None:
	"""Check that an agent named by the browser is one the browser was allowed to name.

	`orchestrator._profile` takes the name on trust, which is right for every other caller —
	resume, triggers, playbooks and background runs all pass a name the system itself wrote
	down. This is the one path where the name arrives from outside, and an agent is a tool
	whitelist and a system prompt, so accepting any name lets anyone who can open the panel
	run as any agent that exists, including one assembled for unattended work.

	Reading the DocType is enough on its own: that is a System Manager's privilege, and
	somebody who can edit the agents is not escalating anything by choosing one.
	"""
	if not agent:
		return None
	if frappe.db.get_value("Sales AI Agent Profile", agent, "selectable"):
		return agent
	# Naming the default is not a choice at all — it is what the run would have used.
	if agent == frappe.db.get_single_value("Sales AI Settings", "default_agent_profile"):
		return agent
	if frappe.has_permission("Sales AI Agent Profile", "read", doc=agent):
		return agent
	frappe.throw(_("Agent {0} is not one you can choose.").format(agent), frappe.PermissionError)


@frappe.whitelist(methods=["POST"])
def portal_chat(prompt: str, session: str | None = None) -> Response:
	"""Send a message as a signed-in customer. Streams the reply.

	Deliberately not `chat` with a flag. Three things differ and every one of them is a
	thing a customer must not be able to change:

	- the agent is read from Settings, never from the request, so a customer cannot name
	  the sales team's agent and inherit its tools and its prompt
	- there is no `reference_doctype`/`reference_name`, so the conversation cannot be
	  pointed at a record
	- there is no `think`, which is a way to spend several times the tokens per message on
	  a surface where the person asking is not paying the bill

	`portal_enabled` is its own switch. Turning the assistant on for staff should not also
	turn it on for everyone who can register on the website.
	"""
	if frappe.session.user == "Guest":
		raise frappe.PermissionError(_("Please log in to use the assistant."))
	if not frappe.db.get_single_value("Sales AI Settings", "portal_enabled"):
		raise frappe.PermissionError(_("The assistant is not available for this account."))

	profile = frappe.db.get_single_value("Sales AI Settings", "portal_agent_profile")
	if not profile:
		# Refused rather than falling back to the default agent, which is the staff one.
		# A missing setting must never widen what a customer can reach.
		raise frappe.PermissionError(_("The assistant is not available for this account."))

	if not (prompt or "").strip():
		frappe.throw(_("Message cannot be empty."))

	return _sse(orchestrator.start_stream(prompt, session=session, agent_profile=profile))


@frappe.whitelist()
def agents() -> dict[str, Any]:
	"""What the panel needs before its first message: who it may run as, and what it may
	be pointed at.

	Runs without a DocType permission check because ordinary sales users cannot read Agent
	Profiles at all — only what a System Manager has explicitly ticked as offerable is
	listed, and only its name and model, never its prompt or its tools.
	"""
	guard_entry()
	default = frappe.db.get_single_value("Sales AI Settings", "default_agent_profile")
	offered = frappe.get_all(
		"Sales AI Agent Profile",
		filters={"enabled": 1, "selectable": 1},
		fields=["name", "title", "model"],
		order_by="title asc",
		ignore_permissions=True,
	)
	# The default is what a run uses when the panel names nothing, so it belongs in the list
	# even when nobody remembered to tick it.
	if default and not any(row.name == default for row in offered):
		fallback = frappe.db.get_value(
			"Sales AI Agent Profile", default, ["name", "title", "model", "enabled"], as_dict=True
		)
		if fallback and fallback.pop("enabled"):
			offered.insert(0, fallback)

	# So the panel can hide "Think" for a model that has no reasoning mode, rather than
	# offering a switch that turns a working answer into a 400.
	for row in offered:
		row["reasoning"] = llm_model.supports_reasoning(row["model"])

	# Read straight off the tools' own enum rather than listed again here, so a record the
	# agent cannot open is never offered as something to ask about.
	return {"default": default, "agents": offered, "subjects": sorted(read.SalesDocType.__args__)}


@frappe.whitelist(methods=["POST"])
def answer(run: str, answers: str | dict[str, str]) -> Response:
	"""Answer a paused run's approval question. Streams the continuation."""
	guard_entry()
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
	guard_entry()
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
	guard_entry()
	frappe.get_doc("Sales AI Playbook", playbook).check_permission("write")
	if reference_doctype and reference_name:
		frappe.has_permission(reference_doctype, doc=reference_name, throw=True)

	return engine.dry_run(
		playbook, reference_doctype=reference_doctype, reference_name=reference_name
	)


@frappe.whitelist(methods=["POST"])
def answer_playbook(playbook_run: str, reply: str) -> dict[str, str]:
	"""Answer a playbook run that is parked on an approval."""
	guard_entry()
	engine.answer(playbook_run, reply)
	return {"status": "queued"}


@frappe.whitelist()
def playbook_graph(playbook: str | None = None, playbook_run: str | None = None) -> dict[str, Any]:
	"""The steps and the edges between them, for drawing.

	The edges come from `plan()` rather than being worked out again in the browser, so the
	picture is of what will actually run. For a run, the trace comes with it, which is what
	lets the drawing highlight the path that was taken.
	"""
	guard_entry()
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
	guard_entry()
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

	pending = _pending_question(doc.name)

	return {
		"session": doc.name,
		"title": doc.title,
		"status": doc.status,
		"reference_doctype": doc.reference_doctype,
		"reference_name": doc.reference_name,
		"messages": messages,
		"run": pending["run"] if pending else None,
		"question": pending["question"] if pending else None,
	}


def _pending_question(session: str) -> dict[str, Any] | None:
	"""The approval this conversation is parked on, if it is parked on one.

	A run pauses in one request and is answered in another, so the question has to be
	findable again. Without this, closing the panel on an approval strands the run
	forever: the card is gone, and nothing else in the product will ever ask again.

	Only the asker's own runs, because `resume_stream` will not accept an answer from
	anybody else — offering a card that cannot be answered is worse than offering none.
	"""
	paused = frappe.get_all(
		"Sales AI Run",
		filters={"session": session, "status": "Paused", "user": frappe.session.user},
		fields=["name", "question"],
		order_by="creation desc",
		limit=1,
	)
	if not paused or not paused[0].question:
		return None
	return {"run": paused[0].name, "question": frappe.parse_json(paused[0].question)}


@frappe.whitelist()
def sessions(limit: int = 20) -> list[dict[str, Any]]:
	"""Recent conversations belonging to the current user."""
	guard_entry()
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
	if isinstance(event, Notice):
		return "notice", {"message": event.message}
	return "unknown", {}


def _frame(event: str, data: dict[str, Any]) -> str:
	return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
