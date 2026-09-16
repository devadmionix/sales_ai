# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Running the golden dataset, and undoing everything it did.

An eval asks the agent to do real things — draft this, update that — because asking it to
pretend would measure a different system from the one being shipped. So each case runs
against the live site, through the same orchestrator a chat message goes through, and is
then rolled back. What is measured is the real path; what survives is only the score.

That rollback is what makes the harness safe to run on a site with real data in it, and it
is why results are held in memory until the end. Writing a result as each case finishes
would put the results inside the very transaction that is about to be thrown away.

The one thing deliberately not rolled back is the model's bill. Tokens are spent whatever
the database does, which is the honest reason to keep the dataset small and to run it on
purpose rather than on a schedule.
"""

from __future__ import annotations

import json
import time
from typing import Any

import frappe
from frappe import _
from frappe.utils import flt, now

from sales_ai import orchestrator, tools
from sales_ai.evals import metrics
from sales_ai.evals.metrics import Expectation, Outcome
from sales_ai.guard import policy
from sales_ai.llm.agent import Question, RunResult, Step


@frappe.whitelist()
def run(
	agent_profile: str | None = None,
	cases: list[str] | str | None = None,
	pause: float = 0,
) -> str:
	"""Score the enabled cases against one agent profile and record the result.

	Returns the name of the `Sales AI Eval Run`. Long enough to want a background job, but
	left synchronous on purpose: whoever asks for a score is waiting for it, and a job that
	commits partway through would defeat the rollback everything here depends on.

	`pause` is seconds to wait between cases, and exists because of how badly a rate limit
	distorts a score. Cases that are refused are reported as unrunnable rather than failed,
	which is accurate but still leaves whichever gates they were the only evidence for
	untested — and an untested gate is not a met one.

	There is no pause that guarantees a clean run, because a case is not one request: the
	agent calls the model once per turn, so a case that reads a record and then answers
	costs two or three. Treat it as a dial to turn up until the errors stop rather than a
	number to calculate. It defaults to none because a paid key should not be made to wait.
	"""
	frappe.only_for("System Manager")

	pause = flt(pause)
	selected = _cases(cases)
	if not selected:
		frappe.throw(_("There are no enabled eval cases to run."))

	profile = _profile(agent_profile)
	eval_run = frappe.get_doc(
		{
			"doctype": "Sales AI Eval Run",
			"agent_profile": profile.name,
			"model": profile.model,
			"status": "Running",
			"started_at": now(),
		}
	).insert(ignore_permissions=True)
	# The run row must outlive the rollbacks below, so it is committed before any case
	# opens a savepoint.
	frappe.db.commit()

	try:
		outcomes = []
		for index, case in enumerate(selected):
			if index and pause:
				time.sleep(pause)
			outcomes.append(_run_case(case, profile.name))
	except Exception:
		eval_run.db_set({"status": "Failed", "error": frappe.get_traceback(with_context=False)})
		frappe.db.commit()
		raise

	_record(eval_run, selected, outcomes)
	return eval_run.name


def _run_case(case: Any, agent_profile: str) -> Outcome:
	"""Run one case as the user it names, then put the database back as it was.

	The undo is a full `rollback`, not a savepoint, and the difference is not stylistic.
	Frappe keeps documents in redis, which no database transaction can reach; the cache is
	invalidated instead by watchers that a commit or a rollback runs. A savepoint runs no
	watchers — frappe says so outright — so rolling back to one would undo the rows and
	leave the cached copies behind, and the next case would read a record this one changed
	and has already given back.
	"""
	expectation = expectation_of(case)
	was = frappe.session.user

	try:
		frappe.set_user(case.run_as or was)
		started = orchestrator.start(
			case.prompt,
			agent_profile=agent_profile,
			reference_doctype=case.reference_doctype,
			reference_name=case.reference_name,
		)
		result = _result_of(started["run"])
		scored = metrics.score(
			expectation, result, writes=_write_tools(), allowed=_allowed_tools()
		)
	except Exception:
		# A case that could not be run is a broken fixture, not a wrong answer. It is
		# reported as its own thing and kept out of every rate.
		scored = metrics.failed_to_run(expectation, frappe.get_traceback(with_context=False))
	finally:
		frappe.db.rollback()
		frappe.set_user(was)

	return scored


def expectation_of(case: Any) -> Expectation:
	"""Read a case document into the plain value the scorer works with."""
	return Expectation(
		title=case.title,
		category=case.category,
		expect_tool=case.expect_tool or "",
		expect_arguments=frappe.parse_json(case.expect_arguments) if case.expect_arguments else {},
		expect_no_writes=bool(case.expect_no_writes),
		must_contain=_lines(case.answer_must_contain),
		must_not_contain=_lines(case.answer_must_not_contain),
	)


def _result_of(run: str) -> RunResult:
	"""Rebuild what the agent did from the run it recorded.

	Scoring the audit trail rather than the engine's own return value is deliberate. It is
	the record a person would be shown if they asked what happened, so a bug that loses a
	step on the way into it is a bug the eval should notice.
	"""
	doc = frappe.get_doc("Sales AI Run", run)
	question = frappe.parse_json(doc.question) if doc.question else None
	return RunResult(
		status=doc.status.lower(),
		messages=[],
		steps=[
			Step(
				tool_call_id=step.tool_call_id or "",
				name=step.tool,
				arguments=frappe.parse_json(step.arguments) if step.arguments else {},
				result=step.result or "",
				duration_ms=step.duration_ms or 0,
				approved_by_human=bool(step.approved_by_human),
				error=step.error,
			)
			for step in doc.steps
		],
		content=doc.response,
		question=Question(
			tool_call_id=question.get("tool_call_id", ""),
			tool_name=question.get("tool_name", ""),
			arguments=question.get("arguments") or {},
			prompt=question.get("prompt", ""),
		)
		if question
		else None,
	)


def _write_tools() -> set[str]:
	return {tool.name for tool in tools.select(tools.names()) if tool.meta.get("writes")}


def _allowed_tools() -> set[str]:
	"""Write tools the policy would currently let run with nobody watching.

	Worked out once per run from the configuration as it stands, so the report says what
	this site permits rather than what the harness assumes.
	"""
	return {
		tool.name
		for tool in tools.select(tools.names())
		if tool.meta.get("writes") and policy.decide(tool).mode == policy.ALLOW
	}


def _record(eval_run: Any, cases: list[Any], outcomes: list[Outcome]) -> None:
	report = metrics.summarise(outcomes)
	for case, outcome in zip(cases, outcomes, strict=True):
		eval_run.append(
			"results",
			{
				"eval_case": case.name,
				"title": outcome.title,
				"category": outcome.category,
				"passed": int(outcome.passed),
				"tools_called": ", ".join(outcome.chose)[:140],
				"run_status": outcome.status,
				"failures": "\n".join(outcome.failures),
				"answer": outcome.answer,
				"error": outcome.error,
			},
		)

	eval_run.update(
		{
			"status": "Completed",
			"ended_at": now(),
			"cases_run": len(outcomes),
			"cases_passed": report.passed,
			"gate_report": report.render(),
			"gates_met": int(report.gates_met),
		}
	)
	eval_run.save(ignore_permissions=True)
	frappe.db.commit()


def _cases(cases: list[str] | str | None) -> list[Any]:
	if isinstance(cases, str):
		cases = json.loads(cases)
	if cases:
		return [frappe.get_doc("Sales AI Eval Case", name) for name in cases]
	return [
		frappe.get_doc("Sales AI Eval Case", name)
		for name in frappe.get_all(
			"Sales AI Eval Case", filters={"enabled": 1}, pluck="name", order_by="creation asc"
		)
	]


def _profile(agent_profile: str | None):
	name = agent_profile or frappe.db.get_single_value("Sales AI Settings", "default_agent_profile")
	if not name:
		frappe.throw(_("Name an agent profile to evaluate, or set a default in Sales AI Settings."))
	return frappe.get_doc("Sales AI Agent Profile", name)


def _lines(text: str | None) -> tuple[str, ...]:
	return tuple(line.strip() for line in (text or "").splitlines() if line.strip())
