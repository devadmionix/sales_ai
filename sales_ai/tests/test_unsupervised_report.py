# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""What the unsupervised-actions report counts as an unsupervised action.

The report is a review, so the way it fails is by being quietly incomplete: a manager
reads an empty week and concludes nothing happened. Each test here pins one of the four
reasons a step is or is not a finding, because those four predicates are the whole report
and none of them announce themselves when they break.
"""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from sales_ai.sales_ai.report.sales_ai_unsupervised_actions import (
	sales_ai_unsupervised_actions as report,
)

FROM_DATE = "2020-01-01"


class TestUnsupervisedActions(IntegrationTestCase):
	def _run(self, *steps: dict) -> str:
		session = frappe.get_doc(
			{"doctype": "Sales AI Session", "user": "Administrator", "title": "report test"}
		).insert(ignore_permissions=True)
		doc = frappe.get_doc(
			{
				"doctype": "Sales AI Run",
				"session": session.name,
				"user": "Administrator",
				"status": "Completed",
				"prompt": "report test",
				"started_at": frappe.utils.now(),
				"steps": [{"tool_call_id": f"c{i}", **s} for i, s in enumerate(steps)],
			}
		).insert(ignore_permissions=True)
		return doc.name

	def _tools(self, run: str) -> list[str]:
		_, rows = report.execute({"from_date": FROM_DATE})
		return [row["tool"] for row in rows if row["run"] == run]

	def test_a_write_nobody_approved_is_the_finding(self) -> None:
		run = self._run({"tool": "draft_quotation", "approved_by_human": 0})
		self.assertEqual(self._tools(run), ["draft_quotation"])

	def test_a_write_somebody_approved_is_not(self) -> None:
		"""Someone was asked and said yes. That is the system working, not a finding."""
		run = self._run({"tool": "draft_quotation", "approved_by_human": 1})
		self.assertEqual(self._tools(run), [])

	def test_a_read_is_not_an_action(self) -> None:
		"""Reads run unsupervised by design, and would drown the report if listed."""
		run = self._run({"tool": "get_record", "approved_by_human": 0})
		self.assertEqual(self._tools(run), [])

	def test_a_write_that_threw_changed_nothing(self) -> None:
		"""A failed write is a bug report, not a governance finding."""
		run = self._run({"tool": "add_note", "approved_by_human": 0, "error": "Boom"})
		self.assertEqual(self._tools(run), [])

	def test_which_tools_are_writes_comes_from_the_registry(self) -> None:
		"""Not a list in the report, which would go stale the next time a tool is added."""
		writes = report._write_tools()
		self.assertIn("draft_quotation", writes)
		self.assertNotIn("get_record", writes)
