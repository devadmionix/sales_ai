# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""A playbook is a recipe a person wrote, not a plan the model made up.

Everything that can be wrong with one is caught here, at save time, rather than halfway
through a run three days later: a jump to a step that does not exist, a loop whose body
runs off the end, a tool that was renamed, arguments that are not valid JSON.

The shape is deliberately small. Steps run in order and fall through to the next one; only
a Condition, an Approval or a Loop sends the run anywhere else. That is enough for the
work sales people actually describe, and it means a playbook can be read top to bottom.
"""

from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.model.document import Document

from sales_ai import tools

KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

# Names the run's own context already occupies.
RESERVED = {"doc", "now", "user"}

BRANCHING = ("Condition", "Approval")


class SalesAIPlaybook(Document):
	def validate(self):
		self._check_keys()
		self._check_steps()
		self._check_loops()

	# -- validation ------------------------------------------------------------------

	def _check_keys(self):
		seen: set[str] = set()
		for step in self.steps:
			key = (step.step_key or "").strip()
			if not KEY_PATTERN.match(key):
				frappe.throw(
					_("Step {0}: {1!r} is not a usable key. Use lower case letters, digits and underscores, starting with a letter.").format(
						step.idx, step.step_key
					),
					title=_("Bad Step Key"),
				)
			if key in RESERVED:
				frappe.throw(
					_("Step {0}: {1!r} is reserved.").format(step.idx, key), title=_("Bad Step Key")
				)
			if key in seen:
				frappe.throw(
					_("Two steps are both called {0!r}. Keys are how steps refer to each other, so they have to be distinct.").format(key),
					title=_("Duplicate Step Key"),
				)
			seen.add(key)
			step.step_key = key

	def _check_steps(self):
		keys = {step.step_key for step in self.steps}
		for step in self.steps:
			if step.step_type == "Tool":
				self._check_tool(step)
			elif step.step_type == "Condition":
				_check_expression(step, step.condition, "Condition")
			elif step.step_type == "Loop":
				_check_expression(step, step.loop_over, "Over")
				self._check_loop_variable(step)

			if step.on_false:
				if step.step_type not in BRANCHING:
					# Silently ignoring it would hide a branch the author thinks exists.
					frappe.throw(
						_("Step {0}: only a Condition or an Approval can branch.").format(step.step_key),
						title=_("Nothing to Branch On"),
					)
				if step.on_false not in keys:
					frappe.throw(
						_("Step {0} branches to {1!r}, which is not a step in this playbook.").format(
							step.step_key, step.on_false
						),
						title=_("Unknown Step"),
					)

	def _check_tool(self, step):
		if not tools.get(step.tool):
			frappe.throw(
				_("Step {0}: there is no tool called {1!r}. Available: {2}.").format(
					step.step_key, step.tool, ", ".join(tools.names())
				),
				title=_("Unknown Tool"),
			)
		if step.arguments and not isinstance(frappe.parse_json(step.arguments), dict):
			frappe.throw(
				_("Step {0}: arguments must be a JSON object.").format(step.step_key),
				title=_("Bad Arguments"),
			)

	def _check_loop_variable(self, step):
		name = (step.loop_as or "").strip()
		if not KEY_PATTERN.match(name):
			frappe.throw(
				_("Step {0}: {1!r} is not a usable variable name.").format(step.step_key, step.loop_as),
				title=_("Bad Loop Variable"),
			)
		if any(other.step_key == name for other in self.steps):
			frappe.throw(
				_("Step {0}: the loop variable {1!r} is also a step key, so one would hide the other.").format(
					step.step_key, name
				),
				title=_("Name Clash"),
			)
		step.loop_as = name

	def _check_loops(self):
		"""A loop's body must lie after it and must not straddle another loop's body."""
		order = {step.step_key: index for index, step in enumerate(self.steps)}
		spans: list[tuple[int, int, str]] = []

		for step in self.steps:
			if step.step_type != "Loop":
				continue
			end = order.get(step.loop_end)
			if end is None:
				frappe.throw(
					_("Loop {0} ends at {1!r}, which is not a step in this playbook.").format(
						step.step_key, step.loop_end
					),
					title=_("Unknown Step"),
				)
			start = order[step.step_key]
			if end <= start:
				frappe.throw(
					_("Loop {0} ends at {1}, which comes before the loop itself. A loop repeats the steps that follow it.").format(
						step.step_key, step.loop_end
					),
					title=_("Empty Loop"),
				)
			spans.append((start, end, step.step_key))

		for i, (start, end, key) in enumerate(spans):
			for other_start, other_end, other_key in spans[i + 1 :]:
				overlaps = start < other_start <= end < other_end
				if overlaps or (other_start < start <= other_end < end):
					frappe.throw(
						_("Loops {0} and {1} overlap. One loop can sit inside another, but they cannot cross.").format(
							key, other_key
						),
						title=_("Crossed Loops"),
					)

	# -- the plan the engine walks ---------------------------------------------------

	def plan(self) -> list[dict]:
		"""The steps as plain dicts, with each one's successor already worked out.

		Doing this once means the engine never has to search the table to find out what
		comes next, and the graph on the form is drawn from exactly what will execute.
		"""
		keys = [step.step_key for step in self.steps]
		# Where a loop's last step sends the run: back to the loop that owns it.
		back_edges = {step.loop_end: step.step_key for step in self.steps if step.step_type == "Loop"}

		plan = []
		for index, step in enumerate(self.steps):
			row = step.as_dict(no_default_fields=True)
			following = keys[index + 1] if index + 1 < len(keys) else None

			if step.step_type == "Loop":
				# A loop's successor is the first step of its own body, always. It is when
				# the loop is *exhausted* that it hands control on, and that is where an
				# enclosing loop would take it back.
				row["next"] = following
				end = keys.index(step.loop_end)
				row["after_loop"] = back_edges.get(step.step_key) or (
					keys[end + 1] if end + 1 < len(keys) else None
				)
			else:
				row["next"] = back_edges.get(step.step_key) or following

			plan.append(row)
		return plan


def _check_expression(step, expression: str, label: str) -> None:
	try:
		compile((expression or "").strip(), "<playbook>", "eval")
	except SyntaxError as e:
		frappe.throw(
			_("Step {0}: the {1} is not a valid Python expression ({2}).").format(
				step.step_key, label, e.msg
			),
			title=_("Bad Expression"),
		)
