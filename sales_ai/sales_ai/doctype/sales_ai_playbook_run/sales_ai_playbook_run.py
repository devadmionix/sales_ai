# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""The durable state of one playbook execution.

A playbook run can be put down for three days and picked up by a different process, so
everything the engine needs lives in columns here and nothing lives in memory: which step
is next, what each earlier step produced, and where the loops had got to.
"""

import json
from typing import Any

import frappe
from frappe.model.document import Document
from frappe.utils import now

# A step's output can be a whole result set; the trace keeps a readable extract.
MAX_STORED_OUTPUT = 20000


class SalesAIPlaybookRun(Document):
	def get_variables(self) -> dict[str, Any]:
		return frappe.parse_json(self.variables) if self.variables else {}

	def set_variables(self, variables: dict[str, Any]) -> None:
		self.variables = json.dumps(variables, default=str)

	def trace_step(
		self,
		step: dict[str, Any],
		*,
		status: str,
		started_at: str,
		duration_ms: int,
		note: str = "",
		inputs: Any = None,
		output: Any = None,
		iteration: int = 0,
	) -> None:
		self.append(
			"trace",
			{
				"step_key": step["step_key"],
				"step_type": step["step_type"],
				"status": status,
				"iteration": iteration,
				"duration_ms": duration_ms,
				"started_at": started_at,
				"note": note[:500],
				"inputs": _dump(inputs),
				"output": _dump(output),
			},
		)

	def finish(self, status: str, *, summary: str = "", error: str = "") -> None:
		self.status = status
		self.summary = summary or self.summary
		self.error = error
		self.cursor = None
		self.resume_at = None
		self.question = None
		self.ended_at = now()
		self.save(ignore_permissions=True)


def _dump(value: Any) -> str | None:
	if value is None:
		return None
	if isinstance(value, str):
		return value[:MAX_STORED_OUTPUT]
	return json.dumps(value, default=str, indent=1)[:MAX_STORED_OUTPUT]


def new_playbook_run(
	playbook: str,
	*,
	trigger: str | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	cursor: str | None = None,
	variables: dict[str, Any] | None = None,
) -> SalesAIPlaybookRun:
	run = frappe.new_doc("Sales AI Playbook Run")
	run.update(
		{
			"playbook": playbook,
			"status": "Running",
			"user": frappe.session.user,
			"trigger": trigger,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"cursor": cursor,
			"started_at": now(),
		}
	)
	run.set_variables(variables or {})
	run.insert(ignore_permissions=True)
	return run
