# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""What comes back when the agent asks for a score, and what does not.

The interesting test here is the one about permissions. `Sales AI Insight` grants read to
every sales role, because a score is not secret in itself — but a churn score quotes the
customer's ordering history in its summary, so handing one to a user who cannot open that
customer leaks the record through the back door. Checking the insight's own permission and
stopping there would turn this layer into a way around every User Permission on the site,
and nothing about the returned rows would look wrong.

The rest pin the storage contract the reads depend on: one row per subject per kind, and no
row at all without its reasons.
"""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from sales_ai.guard.insights import read_insights
from sales_ai.intelligence.base import Factor, Score, record

KIND = "Churn Risk"
VERSION = "test-1"


def _score(value: float = 40.0) -> Score:
	return Score(
		value=value,
		factors=(Factor(label="Overdue", contribution=value, detail="made up for the test"),),
		summary="made up for the test",
	)


class TestInsightStorage(IntegrationTestCase):
	def _customer(self, name: str) -> str:
		if not frappe.db.exists("Customer", name):
			frappe.get_doc(
				{"doctype": "Customer", "customer_name": name, "customer_type": "Company"}
			).insert(ignore_permissions=True)
		return name

	def test_recomputing_replaces_rather_than_appends(self) -> None:
		"""Two opinions about one customer is two answers to one question."""
		customer = self._customer("Insight Test Alpha")
		first = record(KIND, "Customer", customer, _score(40.0), VERSION)
		second = record(KIND, "Customer", customer, _score(70.0), VERSION)

		self.assertEqual(first, second)
		self.assertEqual(
			frappe.db.count("Sales AI Insight", {"kind": KIND, "subject_name": customer}), 1
		)
		self.assertEqual(frappe.db.get_value("Sales AI Insight", first, "value"), 70.0)

	def test_the_old_factors_go_with_the_old_score(self) -> None:
		"""Appending to the table instead of replacing it would explain the number twice."""
		customer = self._customer("Insight Test Beta")
		record(KIND, "Customer", customer, _score(40.0), VERSION)
		name = record(KIND, "Customer", customer, _score(70.0), VERSION)

		doc = frappe.get_doc("Sales AI Insight", name)
		self.assertEqual(len(doc.factors), 1)
		self.assertEqual(doc.factors[0].contribution, 70.0)

	def test_the_band_is_derived_and_not_accepted(self) -> None:
		customer = self._customer("Insight Test Gamma")
		name = record(KIND, "Customer", customer, _score(80.0), VERSION)
		self.assertEqual(frappe.db.get_value("Sales AI Insight", name, "band"), "High")

	def test_a_score_with_no_reasons_is_refused(self) -> None:
		customer = self._customer("Insight Test Delta")
		blank = Score(value=50.0, factors=(), summary="trust me")
		self.assertRaises(
			frappe.ValidationError, record, KIND, "Customer", customer, blank, VERSION
		)


class TestInsightReads(IntegrationTestCase):
	def test_the_reasons_come_back_with_the_score(self) -> None:
		customer = "Insight Read Alpha"
		if not frappe.db.exists("Customer", customer):
			frappe.get_doc(
				{"doctype": "Customer", "customer_name": customer, "customer_type": "Company"}
			).insert(ignore_permissions=True)
		record(KIND, "Customer", customer, _score(55.0), VERSION)

		found = [
			row
			for row in read_insights(KIND, subject_doctype="Customer")["insights"]
			if row["subject"] == customer
		]
		self.assertEqual(len(found), 1)
		self.assertEqual(found[0]["why"][0]["factor"], "Overdue")
		self.assertEqual(found[0]["model_version"], VERSION)

	def test_a_subject_the_user_cannot_read_is_not_handed_over(self) -> None:
		"""The control that stops a score becoming a way around a User Permission."""
		visible = "Insight Perm Visible"
		hidden = "Insight Perm Hidden"
		for name in (visible, hidden):
			if not frappe.db.exists("Customer", name):
				frappe.get_doc(
					{"doctype": "Customer", "customer_name": name, "customer_type": "Company"}
				).insert(ignore_permissions=True)
			record(KIND, "Customer", name, _score(60.0), VERSION)

		user = "insight-perm@example.com"
		if not frappe.db.exists("User", user):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": user,
					"first_name": "Insight",
					"roles": [{"role": "Sales User"}],
				}
			).insert(ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": user,
				"allow": "Customer",
				"for_value": visible,
			}
		).insert(ignore_permissions=True)

		try:
			frappe.set_user(user)
			subjects = {
				row["subject"] for row in read_insights(KIND, subject_doctype="Customer", limit=50)["insights"]
			}
		finally:
			frappe.set_user("Administrator")

		self.assertIn(visible, subjects)
		self.assertNotIn(hidden, subjects)
