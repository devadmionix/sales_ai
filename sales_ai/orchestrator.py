# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Turns configuration into a running agent, and a finished agent into records.

This is the only place that joins the three halves of the system: the `Sales AI *`
DocTypes that hold configuration, the provider-agnostic engine in `sales_ai.llm`, and
the session transcript that makes a paused run resumable in a later request.
"""

from __future__ import annotations

import traceback
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

import frappe
from frappe import _

from sales_ai import budget, prompt, tools
from sales_ai.guard import policy
from sales_ai.llm.agent import Agent, Checkpoint, Event, RunResult, drain
from sales_ai.llm.model import Model
from sales_ai.sales_ai.doctype.sales_ai_run.sales_ai_run import SalesAIRun, new_run


@dataclass
class RunStarted:
	"""Emitted before the model is called, so a UI can show the run while it is thinking."""

	run: str
	session: str


def start(prompt: str, **kwargs: Any) -> dict[str, Any]:
	return drain(start_stream(prompt, **kwargs))


def resume(run: str, answers: dict[str, str], **kwargs: Any) -> dict[str, Any]:
	return drain(resume_stream(run, answers, **kwargs))


def start_stream(
	prompt: str,
	*,
	session: str | None = None,
	agent_profile: str | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	trigger: str | None = None,
	background: bool = False,
	think: bool = False,
) -> Generator[Event | RunStarted, None, dict[str, Any]]:
	"""Run one user turn, streaming as it goes, and persist the outcome."""
	profile = _profile(agent_profile)
	budget.check_tokens()
	session_doc = (
		_load_session(session)
		if session
		else _new_session(prompt, profile.name, reference_doctype, reference_name)
	)

	agent = build_agent(profile, think=think)
	run = new_run(
		session_doc.name,
		prompt,
		profile.name,
		agent.model.model_id,
		trigger=trigger,
		background=background,
	)
	frappe.flags.sales_ai_run = run.name
	yield RunStarted(run=run.name, session=session_doc.name)

	try:
		result = yield from _pump(
			agent.stream(prompt, history=session_doc.get_transcript()), session_doc, background
		)
	except Exception:
		run.fail(frappe.get_traceback(with_context=False))
		raise

	return _persist(session_doc, run, result)


def resume_stream(
	run: str, answers: dict[str, str], *, background: bool = False
) -> Generator[Event | RunStarted, None, dict[str, Any]]:
	"""Continue a paused run once a human has answered."""
	run_doc: SalesAIRun = frappe.get_doc("Sales AI Run", run)
	if run_doc.status != "Paused":
		frappe.throw(_("This run is {0}, not awaiting an answer.").format(run_doc.status))
	if run_doc.user != frappe.session.user:
		frappe.throw(_("Only the person who started a run can answer it."), frappe.PermissionError)

	session_doc = _load_session(run_doc.session)
	agent = build_agent(_profile(run_doc.agent_profile))
	frappe.flags.sales_ai_run = run_doc.name
	yield RunStarted(run=run_doc.name, session=session_doc.name)

	try:
		result = yield from _pump(
			agent.resume_stream(session_doc.get_transcript(), answers), session_doc, background
		)
	except Exception:
		run_doc.fail(frappe.get_traceback(with_context=False))
		raise

	return _persist(session_doc, run_doc, result)


def _pump(
	stream: Generator[Event, None, RunResult], session_doc, background: bool
) -> Generator[Event, None, RunResult]:
	"""Forward the agent's events, saving the transcript at every checkpoint.

	Only worth doing in the background, where a run can outlive the thing that started it
	and be killed by a worker timeout. In a request the transcript is written once at the
	end, because a half-saved conversation is no use to a browser that has gone away.
	"""
	while True:
		try:
			event = next(stream)
		except StopIteration as finished:
			return finished.value

		if isinstance(event, Checkpoint):
			if background:
				session_doc.set_transcript(_without_system(event.messages))
				# The point of a checkpoint is to survive the process, so it must land.
				frappe.db.commit()
			continue

		yield event


def build_agent(profile, *, think: bool = False) -> Agent:
	"""Assemble the engine from a profile. The tool list is a whitelist, never a filter.

	`think` is asked for per turn rather than stored on the profile, because it is the
	question that is hard, not the agent.
	"""
	selected = tools.select([row.tool for row in profile.tools if row.enabled])
	model = Model.from_name(profile.model)
	if think:
		model.think()
	return Agent(
		model=model,
		tools=selected,
		system_prompt=prompt.build(profile, selected),
		max_iterations=profile.max_iterations or 12,
		policy=policy.gate,
		on_error=_log_tool_failure,
	)


def _log_tool_failure(tool_name: str, exc: Exception) -> None:
	"""Keep the traceback of a tool that crashed, since the model is only told a phrase."""
	trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
	frappe.log_error(title=f"Sales AI: {tool_name} failed", message=trace)


def _persist(session_doc, run: SalesAIRun, result: RunResult) -> dict[str, Any]:
	session_doc.set_transcript(_without_system(result.messages))
	run.record(result)
	return {
		"run": run.name,
		"session": session_doc.name,
		"status": result.status,
		"content": result.content,
		"question": frappe.parse_json(run.question) if run.question else None,
	}


def _without_system(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
	"""Keep the system prompt out of the stored transcript.

	It belongs to the agent profile, so it is re-applied on every run. Storing it would
	freeze an old prompt into every session that has ever been opened.
	"""
	return [m for m in messages if m.get("role") != "system"]


def _profile(name: str | None):
	if not frappe.db.get_single_value("Sales AI Settings", "enabled"):
		frappe.throw(_("Sales AI is turned off in Sales AI Settings."), title=_("Disabled"))

	name = name or frappe.db.get_single_value("Sales AI Settings", "default_agent_profile")
	if not name:
		frappe.throw(_("No agent profile was given and no default is set."), title=_("No Agent"))

	profile = frappe.get_cached_doc("Sales AI Agent Profile", name)
	if not profile.enabled:
		frappe.throw(_("Agent {0} is disabled.").format(name), title=_("Agent Unavailable"))
	return profile


def _load_session(name: str):
	session_doc = frappe.get_doc("Sales AI Session", name)
	if session_doc.user != frappe.session.user:
		frappe.throw(_("This conversation belongs to someone else."), frappe.PermissionError)
	if session_doc.status != "Active":
		frappe.throw(_("This conversation is closed."))
	return session_doc


def _new_session(
	prompt: str, agent_profile: str, reference_doctype: str | None, reference_name: str | None
):
	if reference_doctype and reference_name:
		# The user must already be allowed to see the record they are asking about.
		frappe.has_permission(reference_doctype, doc=reference_name, throw=True)

	session_doc = frappe.new_doc("Sales AI Session")
	session_doc.update(
		{
			"title": prompt[:140],
			"agent_profile": agent_profile,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
		}
	)
	session_doc.insert()
	return session_doc
