# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

import json

import frappe
from frappe.model.document import Document
from frappe.utils import now

from sales_ai.llm.agent import RunResult

# A tool result can be large; the audit row keeps a readable extract, not a blob.
MAX_STORED_RESULT = 20000

# Some providers append an encrypted reasoning signature to the tool call id, which runs to
# thousands of characters. The audit row only needs enough of it to cross-reference the call.
# The full id is kept in the transcript and in `question`, where resuming a paused run
# depends on handing the provider back the exact string it gave us.
MAX_TOOL_CALL_ID = 140


class SalesAIRun(Document):
	def record(self, result: RunResult) -> None:
		"""Persist the outcome of an agent run. Runs are system-written, never user-written."""
		self.status = _STATUS[result.status]
		self.response = result.content
		self.iterations = result.iterations
		self.prompt_tokens = result.usage.get("prompt_tokens", 0)
		self.completion_tokens = result.usage.get("completion_tokens", 0)
		self.total_tokens = result.usage.get("total_tokens", 0)
		self.question = json.dumps(_question_payload(result)) if result.question else None
		self.ended_at = None if result.status == "paused" else now()

		for step in result.steps:
			self.append(
				"steps",
				{
					"tool": step.name,
					"tool_call_id": step.tool_call_id[:MAX_TOOL_CALL_ID],
					"arguments": json.dumps(step.arguments, default=str),
					"result": step.result[:MAX_STORED_RESULT],
					"duration_ms": step.duration_ms,
					"approved_by_human": int(step.approved_by_human),
					"error": step.error,
				},
			)

		self.save(ignore_permissions=True)

	def fail(self, message: str) -> None:
		self.status = "Failed"
		self.error = message
		self.ended_at = now()
		self.save(ignore_permissions=True)


_STATUS = {"completed": "Completed", "paused": "Paused", "stopped": "Stopped"}


def _question_payload(result: RunResult) -> dict:
	question = result.question
	return {
		"tool_call_id": question.tool_call_id,
		"tool_name": question.tool_name,
		"arguments": question.arguments,
		"prompt": question.prompt,
		"options": question.options,
		"allow_other": question.allow_other,
		"preview": question.preview,
		"risk": question.risk,
	}


def new_run(
	session: str,
	prompt: str,
	agent_profile: str | None,
	model: str | None,
	*,
	trigger: str | None = None,
	background: bool = False,
) -> SalesAIRun:
	run = frappe.new_doc("Sales AI Run")
	run.update(
		{
			"session": session,
			"user": frappe.session.user,
			"status": "Running",
			"prompt": prompt,
			"agent_profile": agent_profile,
			"model": model,
			"trigger": trigger,
			"background": int(background),
			"started_at": now(),
		}
	)
	run.insert(ignore_permissions=True)
	return run
