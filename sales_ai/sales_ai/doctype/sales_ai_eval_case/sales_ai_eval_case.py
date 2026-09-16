# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from sales_ai import tools


class SalesAIEvalCase(Document):
	def validate(self) -> None:
		self._check_tool()
		self._check_arguments()
		self._check_gate_cases_are_gates()

	def _check_tool(self) -> None:
		if self.expect_tool and self.expect_tool not in tools.names():
			frappe.throw(
				_("There is no tool called {0}. The registered tools are: {1}").format(
					frappe.bold(self.expect_tool), ", ".join(tools.names())
				)
			)

	def _check_arguments(self) -> None:
		if not self.expect_arguments:
			return
		try:
			parsed = frappe.parse_json(self.expect_arguments)
		except Exception:
			frappe.throw(_("Expect Arguments is not valid JSON."))
		if not isinstance(parsed, dict):
			frappe.throw(_("Expect Arguments must be an object, mapping argument names to values."))
		if not self.expect_tool:
			frappe.throw(_("Name the tool whose arguments these are."))

	def _check_gate_cases_are_gates(self) -> None:
		"""An Injection case that does not assert anything is worse than no case.

		It passes, is counted, and reports a hard gate as met on the strength of a row
		nobody finished writing.
		"""
		if self.category not in ("Injection", "Refusal"):
			return
		if not self.expect_no_writes:
			frappe.throw(
				_("A {0} case must tick Expect No Writes — that is the thing it is testing.").format(
					self.category
				)
			)
