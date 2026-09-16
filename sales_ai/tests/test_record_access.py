# Copyright (c) 2026, Admionix and contributors
# For license information, please see license.txt

"""Naming a record must not be a way to find out that it exists.

A read tool takes a name from the model, which ultimately took it from the user. If a
record the user may not see fails differently from a record nobody has, then the pair is an
oracle: guess names, watch the wording, and the ones that come back "no access" are real.
For a Customer DocType that is the customer list; for a Quotation it is the deal count.

So the tests below are all one shape — restrict a user to one record, then compare what
they are told about a record they cannot see against what they are told about a record that
was never there. The messages have to match exactly.
"""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from sales_ai.guard import NO_SUCH_RECORD, GuardError, read_document
from sales_ai.guard.writes import update_record

VISIBLE = "Access Test Visible"
HIDDEN = "Access Test Hidden"
MISSING = "Access Test Never Existed"
USER = "record-access@example.com"


class TestRecordAccess(IntegrationTestCase):
	def setUp(self) -> None:
		for name in (VISIBLE, HIDDEN):
			if not frappe.db.exists("Customer", name):
				frappe.get_doc(
					{"doctype": "Customer", "customer_name": name, "customer_type": "Company"}
				).insert(ignore_permissions=True)

		if not frappe.db.exists("User", USER):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": USER,
					"first_name": "Record Access",
					"roles": [{"role": "Sales User"}],
				}
			).insert(ignore_permissions=True)

		if not frappe.db.exists("User Permission", {"user": USER, "for_value": VISIBLE}):
			frappe.get_doc(
				{
					"doctype": "User Permission",
					"user": USER,
					"allow": "Customer",
					"for_value": VISIBLE,
				}
			).insert(ignore_permissions=True)

		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user(USER)

	def _refusal(self, name: str) -> str:
		with self.assertRaises(GuardError) as caught:
			read_document("Customer", name)
		return str(caught.exception)

	def _expected(self, name: str) -> str:
		return NO_SUCH_RECORD.format(doctype="Customer", name=name)

	def _denials(self, name: str) -> list:
		return frappe.get_all(
			"Sales AI Action Log",
			filters={"user": USER, "outcome": "Denied", "reference_name": name},
			fields=["action", "reference_doctype", "reason"],
			order_by="creation asc",
			ignore_permissions=True,
		)

	def test_a_hidden_record_and_a_missing_one_read_the_same(self) -> None:
		"""The oracle, closed. Differing wording here would enumerate the customer list.

		The name is echoed back, so the two messages are not byte-identical; what has to
		match is everything around it, which is what the shared template pins.
		"""
		self.assertEqual(self._refusal(HIDDEN), self._expected(HIDDEN))
		self.assertEqual(self._refusal(MISSING), self._expected(MISSING))

	def test_the_refusal_does_not_say_whether_the_record_exists(self) -> None:
		message = self._refusal(HIDDEN)
		for giveaway in ("exist", "permission", "denied", "not found"):
			self.assertNotIn(giveaway, message.lower(), f"{message!r} hints via {giveaway!r}")

	def test_the_record_the_user_is_allowed_still_comes_back(self) -> None:
		"""Failing closed is only worth anything if the allowed case still works."""
		record = read_document("Customer", VISIBLE)
		self.assertEqual(record["name"], VISIBLE)

	def test_a_name_from_another_doctype_is_refused_the_same_way(self) -> None:
		"""A Lead's name handed to the Customer reader is just another name that is not there."""
		self.assertEqual(self._refusal("CRM-LEAD-9999"), self._expected("CRM-LEAD-9999"))

	def test_the_attempt_is_logged_even_though_nothing_happened(self) -> None:
		"""Nothing changed, so nothing else in the system would remember this happened.

		A single refusal is usually someone mistyping. The same refusal over and over is the
		shape of a person finding the edges, and an admin can only see that if the misses are
		written down as well as the hits.
		"""
		before = self._denials(HIDDEN)
		self._refusal(HIDDEN)
		frappe.set_user("Administrator")

		rows = self._denials(HIDDEN)
		# One row, not none and not two: a refusal that is logged twice reads as a pattern
		# that is not there, which is the thing this log exists to show.
		self.assertEqual(len(rows) - len(before), 1)
		self.assertEqual(rows[-1].action, "Read")
		self.assertEqual(rows[-1].reference_doctype, "Customer")
		self.assertEqual(rows[-1].reason, self._expected(HIDDEN))

	def test_the_log_keeps_a_name_that_never_existed(self) -> None:
		"""The name is the evidence. Link validation would throw it away for being wrong."""
		self._refusal(MISSING)
		frappe.set_user("Administrator")

		self.assertTrue(
			frappe.db.exists(
				"Sales AI Action Log",
				{"user": USER, "outcome": "Denied", "reference_name": MISSING},
			)
		)

	def test_a_write_to_a_hidden_record_is_refused_like_a_read(self) -> None:
		"""Writing is the more tempting way to probe: the answer is the same either way."""
		with self.assertRaises(GuardError) as caught:
			update_record("Customer", HIDDEN, {"website": "changed.example"}, tool="update_record")

		self.assertEqual(str(caught.exception), self._expected(HIDDEN))
		frappe.set_user("Administrator")
		self.assertFalse(frappe.db.get_value("Customer", HIDDEN, "website"))

	def test_the_refused_write_is_filed_under_what_it_would_have_been(self) -> None:
		with self.assertRaises(GuardError):
			update_record("Customer", HIDDEN, {"website": "changed.example"}, tool="update_record")

		frappe.set_user("Administrator")
		self.assertEqual(self._denials(HIDDEN)[-1].action, "Update")

	def test_an_allowed_read_is_not_filed_as_a_denial(self) -> None:
		"""Otherwise the Denied list fills with noise and stops being worth looking at."""
		read_document("Customer", VISIBLE)
		frappe.set_user("Administrator")

		self.assertFalse(
			frappe.db.exists(
				"Sales AI Action Log",
				{"user": USER, "outcome": "Denied", "reference_name": VISIBLE},
			)
		)
