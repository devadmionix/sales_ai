# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""What a failing tool is allowed to tell the model.

The agent loop turns every tool failure into text that goes into the transcript, and the
transcript is shown to the user and fed back to the model. So the question these tests ask
is not "did it fail gracefully" but "what did it say". A message written for the model is
passed through; anything else is an internal failure whose text would leak — a record name,
a table, or the mere fact that a document exists — and is replaced by one fixed phrase.
"""

from __future__ import annotations

import json

import frappe
from frappe.tests import UnitTestCase

from sales_ai.guard import GuardError
from sales_ai.llm.agent import _TOOL_FAILED, Agent
from sales_ai.llm.tool import Tool, ToolError
from sales_ai.llm.types import ToolCall


def _tool(name: str, func) -> Tool:
	return Tool(name=name, description="", parameters={}, func=func)


def _call(tool: str, **arguments) -> ToolCall:
	return ToolCall(id="call-1", name=tool, arguments=arguments)


class TestToolFailureText(UnitTestCase):
	def setUp(self) -> None:
		self.logged: list[tuple[str, Exception]] = []

	def _agent(self, *tools: Tool) -> Agent:
		return Agent(
			model=None,
			tools=tools,
			on_error=lambda name, exc: self.logged.append((name, exc)),
		)

	def _run(self, agent: Agent, call: ToolCall):
		step = agent._execute(call)
		# The two must agree: `result` is what the model reads, `error` is what is stored.
		self.assertEqual(json.loads(step.result)["error"], step.error)
		return step.error

	# -- messages written for the model are kept -------------------------------------

	def test_guard_refusal_reaches_the_model_verbatim(self):
		"""The guard layer phrases its own refusals, so the loop must not overwrite them."""

		def refuse(name: str) -> str:
			raise GuardError("You do not have access to that record.")

		agent = self._agent(_tool("refuse", refuse))
		self.assertEqual(
			self._run(agent, _call("refuse", name="LEAD-0050")),
			"You do not have access to that record.",
		)
		self.assertEqual(self.logged, [])

	def test_bad_arguments_are_explained_so_the_model_can_retry(self):
		agent = self._agent(_tool("needs_name", lambda name: name))
		message = self._run(agent, _call("needs_name", wrong=1))
		self.assertIn("name", message)
		self.assertNotEqual(message, _TOOL_FAILED)
		self.assertEqual(self.logged, [])

	# -- everything else is replaced -------------------------------------------------

	def test_permission_error_does_not_confirm_the_record_exists(self):
		"""The whole point: frappe names the record it refused, and the model must not see it."""

		def read(name: str) -> str:
			raise frappe.PermissionError(f"No permission for Lead {name}")

		agent = self._agent(_tool("read", read))
		message = self._run(agent, _call("read", name="LEAD-0050"))
		self.assertEqual(message, _TOOL_FAILED)
		self.assertNotIn("LEAD-0050", message)
		self.assertNotIn("Permission", message)

	def test_a_missing_record_is_indistinguishable_from_a_forbidden_one(self):
		"""Two different failures, one phrase. Otherwise the pair is an existence oracle."""

		def missing(name: str) -> str:
			raise frappe.DoesNotExistError(f"Lead {name} not found")

		def forbidden(name: str) -> str:
			raise frappe.PermissionError(f"No permission for Lead {name}")

		agent = self._agent(_tool("missing", missing), _tool("forbidden", forbidden))
		self.assertEqual(
			self._run(agent, _call("missing", name="LEAD-0050")),
			self._run(agent, _call("forbidden", name="LEAD-0051")),
		)

	def test_the_exception_type_is_not_named(self):
		"""`KeyError: 'tabSales Order'` says which table the query touched."""

		def crash() -> str:
			raise KeyError("tabSales Order")

		agent = self._agent(_tool("crash", crash))
		message = self._run(agent, _call("crash"))
		self.assertEqual(message, _TOOL_FAILED)
		self.assertNotIn("KeyError", message)
		self.assertNotIn("tabSales Order", message)

	def test_the_real_exception_is_handed_to_on_error(self):
		"""Hiding the text from the model must not mean losing it: a bug still has to be findable."""

		def crash() -> str:
			raise KeyError("tabSales Order")

		agent = self._agent(_tool("crash", crash))
		self._run(agent, _call("crash"))
		self.assertEqual(len(self.logged), 1)
		name, exc = self.logged[0]
		self.assertEqual(name, "crash")
		self.assertIsInstance(exc, KeyError)
		self.assertIn("tabSales Order", str(exc))

	def test_a_crash_without_a_handler_still_answers(self):
		"""`on_error` is optional; the loop must not depend on the caller supplying one."""

		def crash() -> str:
			raise KeyError("tabSales Order")

		agent = Agent(model=None, tools=[_tool("crash", crash)])
		self.assertEqual(self._run(agent, _call("crash")), _TOOL_FAILED)

	# -- the guard's own errors are the kind that get through -------------------------

	def test_guard_error_is_a_tool_error(self):
		"""The guard layer opts in to being quoted by subclassing, not by being trusted here."""
		self.assertTrue(issubclass(GuardError, ToolError))
